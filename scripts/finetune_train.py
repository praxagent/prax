#!/usr/bin/env python3
"""Standalone LoRA fine-tuning script using Unsloth.

This runs as a separate process (may need a different venv with CUDA deps).
The Flask app launches it via subprocess and reads the status file for progress.

Usage:
    python scripts/finetune_train.py \
        --base-model unsloth/Qwen3-8B-unsloth-bnb-4bit \
        --data training_data.jsonl \
        --output ./adapters/adapter_20260320 \
        --max-steps 60 \
        --learning-rate 2e-4 \
        --lora-rank 16

Requires: pip install unsloth
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def write_status(status_file: str, data: dict) -> None:
    if status_file:
        with open(status_file, "w") as f:
            json.dump(data, f)


def main():
    parser = argparse.ArgumentParser(description="LoRA fine-tune with Unsloth")
    parser.add_argument("--base-model", required=True, help="HuggingFace model ID")
    parser.add_argument("--data", required=True, help="Path to JSONL training data")
    parser.add_argument("--output", required=True, help="Output directory for adapter")
    parser.add_argument("--max-steps", type=int, default=60)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    args = parser.parse_args()

    status_file = os.environ.get("FINETUNE_STATUS_FILE", "")

    write_status(status_file, {"state": "loading_model", "step": 0, "max_steps": args.max_steps})

    try:
        from unsloth import FastLanguageModel
    except ImportError:
        print("ERROR: unsloth is not installed. Run: pip install unsloth", file=sys.stderr)
        write_status(status_file, {"state": "failed", "error": "unsloth not installed"})
        sys.exit(1)

    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer
    from unsloth.chat_templates import get_chat_template

    # 1. Load base model with 4-bit quantization.
    print(f"Loading model: {args.base_model}")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.base_model,
        max_seq_length=args.max_seq_length,
        load_in_4bit=True,
    )

    # 2. Apply LoRA adapters.
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_rank,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        lora_alpha=args.lora_rank,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
    )

    # 3. Setup chat template.
    tokenizer = get_chat_template(tokenizer, chat_template="chatml")

    # 4. Load training data.
    write_status(status_file, {"state": "loading_data", "step": 0, "max_steps": args.max_steps})
    dataset = load_dataset("json", data_files=args.data, split="train")

    def format_examples(examples):
        texts = []
        for msgs in examples["messages"]:
            text = tokenizer.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=False,
            )
            texts.append(text)
        return {"text": texts}

    dataset = dataset.map(format_examples, batched=True)
    print(f"Training on {len(dataset)} examples")

    # 5. Train.
    write_status(status_file, {"state": "training", "step": 0, "max_steps": args.max_steps})

    training_config = SFTConfig(
        output_dir=args.output,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        max_steps=args.max_steps,
        learning_rate=args.learning_rate,
        fp16=True,
        logging_steps=5,
        save_steps=args.max_steps,  # save only at end
        warmup_steps=5,
        dataset_text_field="text",
        max_seq_length=args.max_seq_length,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=training_config,
    )

    trainer.train()

    # 6. Save LoRA adapter.
    write_status(status_file, {"state": "saving", "step": args.max_steps, "max_steps": args.max_steps})
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)

    write_status(status_file, {
        "state": "completed",
        "step": args.max_steps,
        "max_steps": args.max_steps,
        "output": args.output,
    })
    print(f"Adapter saved to {args.output}")


if __name__ == "__main__":
    main()
