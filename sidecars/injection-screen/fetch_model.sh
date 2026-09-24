#!/bin/sh
# Download the default model (ProtectAI deberta-v3-base-prompt-injection-v2, ONNX,
# Apache-2.0, not gated) into $1 (default ./model). No token needed.
set -e
DEST="${1:-./model}"
BASE=https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2/resolve/main/onnx
mkdir -p "$DEST"
for f in model.onnx tokenizer.json config.json special_tokens_map.json tokenizer_config.json; do
  curl -fsSL -o "$DEST/$f" "$BASE/$f"
done
echo "model in $DEST"
