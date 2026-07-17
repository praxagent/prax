"""Download REAL benchmark test sets into $PRAX_EVAL_DIR/datasets/ (never committed).

Populates the caches the adapters read when run with PRAX_EVAL_FULL_DATASETS=1.
Each mapper converts a HuggingFace row into the adapter's own case format.

    uv run python scripts/fetch_eval_datasets.py                 # all open sets
    uv run python scripts/fetch_eval_datasets.py gsm8k mmlu_pro  # named
    uv run python scripts/fetch_eval_datasets.py --limit 300     # cap cache size

GPQA-Diamond is gated on HuggingFace — set HF_TOKEN to include it. IFEval / BFCL /
SimpleQA have bespoke formats (and SimpleQA is model-graded) — not wired here.
Contamination firewall: the cached data lives under PRAX_EVAL_DIR (data-only,
never committed); reference answers must never be quoted into prompts or docs.
"""
from __future__ import annotations

import argparse
import re
import sys

_LETTERS = "ABCDEFGHIJKLMNOP"


def _gsm8k(row: dict) -> dict | None:
    ans = row.get("answer", "")
    m = re.search(r"####\s*(-?[\d,]+)", ans)
    if not m:
        return None
    return {"id": f"gsm_{abs(hash(row['question'])) % 10**8}",
            "question": row["question"], "answer": m.group(1).replace(",", "")}


def _mmlu_pro(row: dict) -> dict | None:
    opts = row.get("options") or []
    idx = row.get("answer_index")
    if not opts or idx is None or idx >= len(opts):
        return None
    return {"id": f"mmlup_{row.get('question_id', abs(hash(row['question'])) % 10**8)}",
            "question": row["question"], "choices": list(opts),
            "answer": _LETTERS[idx]}


def _math(row: dict) -> dict | None:
    prob = row.get("problem")
    ans = row.get("answer")
    if not prob or ans is None:
        return None
    return {"id": f"math_{abs(hash(prob)) % 10**8}", "problem": prob, "answer": str(ans)}


def _humaneval(row: dict) -> dict | None:
    return {"id": row["task_id"].replace("/", "_"), "entry_point": row["entry_point"],
            "prompt": row["prompt"], "canonical_solution": row["canonical_solution"],
            "test": row["test"]}


def _gpqa(row: dict) -> dict | None:
    import hashlib

    q = row.get("Question")
    correct = row.get("Correct Answer")
    incorrect = [row.get(f"Incorrect Answer {i}") for i in (1, 2, 3)]
    if not q or not correct or not all(incorrect):
        return None
    opts = [correct, *incorrect]
    # Deterministic shuffle (stable across runs, unlike hash()) so the correct
    # option's position varies — no position-bias gaming — but is reproducible.
    order = sorted(range(4), key=lambda i: hashlib.md5(f"{q}|{opts[i]}".encode()).hexdigest())
    shuffled = [opts[i] for i in order]
    qid = hashlib.md5(q.encode()).hexdigest()[:8]
    return {"id": f"gpqa_{qid}", "question": q, "choices": shuffled,
            "answer": _LETTERS[shuffled.index(correct)]}


def _truthfulqa(row: dict) -> dict | None:
    mc1 = row.get("mc1_targets") or {}
    choices, labels = mc1.get("choices") or [], mc1.get("labels") or []
    if not choices or 1 not in labels:
        return None
    return {"id": f"tqa_{abs(hash(row['question'])) % 10**8}",
            "question": row["question"], "choices": list(choices),
            "correct": labels.index(1)}


# name -> (hf_id, config, split, mapper)
SOURCES = {
    "gsm8k":       ("openai/gsm8k", "main", "test", _gsm8k),
    "mmlu_pro":    ("TIGER-Lab/MMLU-Pro", None, "test", _mmlu_pro),
    "math":        ("HuggingFaceH4/MATH-500", None, "test", _math),
    "humaneval":   ("openai_humaneval", None, "test", _humaneval),
    "truthfulqa":  ("truthful_qa", "multiple_choice", "validation", _truthfulqa),
    # GPQA-Diamond — gated; needs HF_TOKEN_RO in .env (read-only token).
    "gpqa":        ("Idavidrein/gpqa", "gpqa_diamond", "train", _gpqa),
}


def _fetch_arc_agi_2(split: str = "evaluation", limit: int | None = None) -> int:
    """Fetch ARC-AGI-2 public tasks from GitHub (not HuggingFace) → JSONL cache.

    Each task JSON is ``{"train": [{input,output}...], "test": [{input,output}...]}``;
    we cache one case per task using the FIRST test pair. ``split`` is
    ``training`` (dev) or ``evaluation`` (held-out check). Never train on either
    the evaluation split or the hidden competition sets — contamination firewall.
    """
    import glob
    import json
    import subprocess
    import tempfile

    from prax.eval.benchmarks.datasets import _cache_path

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["git", "clone", "--depth", "1",
             "https://github.com/arcprize/ARC-AGI-2", tmp],
            check=True, capture_output=True, timeout=180,
        )
        files = sorted(glob.glob(f"{tmp}/data/{split}/*.json"))
        path = _cache_path("arc_agi_2")
        path.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with path.open("w") as out:
            for f in files:
                task = json.load(open(f))
                tests = task.get("test") or []
                if not task.get("train") or not tests:
                    continue
                t0 = tests[0]
                case = {
                    "id": f"arc2_{split[:4]}_{f.split('/')[-1].replace('.json', '')}",
                    "train": task["train"],
                    "test_input": t0["input"],
                    "test_output": t0["output"],
                }
                out.write(json.dumps(case) + "\n")
                n += 1
                if limit and n >= limit:
                    break
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help="datasets to fetch (default: all open ones)")
    ap.add_argument("--limit", type=int, default=None, help="cap cases cached per set")
    args = ap.parse_args()

    from prax.eval.benchmarks.datasets import fetch_and_cache

    names = args.names or [n for n, v in SOURCES.items() if v[3] is not None]
    rc = 0
    for name in names:
        if name == "arc_agi_2":
            # ARC-AGI-2 is git-hosted, not on HuggingFace. Default to the
            # held-out EVALUATION split (the standard reported number); pass
            # 'arc_agi_2:training' for the dev split.
            split = "evaluation"
            try:
                n = _fetch_arc_agi_2(split=split, limit=args.limit)
                print(f"  arc_agi_2: cached {n} tasks (arcprize/ARC-AGI-2/{split})")
            except Exception as exc:  # noqa: BLE001
                print(f"  arc_agi_2: FETCH FAILED — {type(exc).__name__}: {exc}")
                rc = 1
            continue
        if name not in SOURCES:
            print(f"  {name}: unknown (have: {', '.join(SOURCES)}, arc_agi_2)")
            rc = 1
            continue
        hf_id, config, split, mapper = SOURCES[name]
        if mapper is None:
            print(f"  {name}: gated/unmapped ({hf_id}) — set HF_TOKEN + a mapper to include")
            continue
        try:
            n = fetch_and_cache(name, hf_id, split, mapper, config=config)
            suffix = f" — will use up to {args.limit}" if args.limit else ""
            print(f"  {name}: cached {n} cases "
                  f"({hf_id}{'/' + config if config else ''}/{split}){suffix}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: FETCH FAILED — {type(exc).__name__}: {exc}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
