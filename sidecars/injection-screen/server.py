"""Prompt-injection screen — an independent classifier, outside the agent.

A small HTTP service that scores text for prompt injection with an open
classifier (default: ProtectAI deberta-v3-base-prompt-injection-v2, ONNX,
Apache-2.0). Prax calls it on untrusted content before that content enters the
model's context (INJECTION_SCREEN_URL; prax/agent/loop_middleware.py).

It runs as its own process on purpose: the agent cannot reconfigure or switch
off a check that does not live in its process, and the model weights and
runtime stay out of Prax's dependencies.

    INJECTION_SCREEN_MODEL_DIR=/models/prompt-injection \\
    python server.py            # 127.0.0.1:8795

POST /screen {"text": "..."}  ->  {"injection": bool, "score": float, "windows": n}
GET  /health

Long text is scored in overlapping 512-token windows; the score is the
highest window's. Known limits (from the model card): English only; not meant
for system-prompt-like text, where it false-positives. Measure before trusting
it: scripts/eval_injection_screen.py.
"""
from __future__ import annotations

import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

WINDOW, STRIDE, MAX_CHARS = 512, 256, 200_000
# Memory is bounded by construction, not by hoping the text is short: at most
# MAX_WINDOWS windows are scored per request (head and tail of a long text),
# BATCH at a time. Scoring every window of a long README in one batch took
# this process to 5.6 GB and set off the host's OOM killer (2026-09-24).
MAX_WINDOWS, BATCH = 48, 4


class Screen:
    def __init__(self, model_dir: str, threshold: float):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        with open(os.path.join(model_dir, "config.json")) as f:
            id2label = {int(k): v.upper() for k, v in json.load(f)["id2label"].items()}
        self.injection_index = next(i for i, v in id2label.items() if "INJECTION" in v)
        self.tokenizer = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.session = ort.InferenceSession(os.path.join(model_dir, "model.onnx"),
                                            providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.session.get_inputs()}
        self.threshold = threshold
        # One scoring at a time: memory is bounded per request (~1.8 GB peak),
        # so two concurrent requests could exceed a 2 GB container limit.
        self._lock = threading.Lock()

    def score(self, text: str) -> tuple[float, int]:
        with self._lock:
            return self._score(text)

    def _score(self, text: str) -> tuple[float, int]:
        ids = self.tokenizer.encode(text[:MAX_CHARS], add_special_tokens=False).ids
        cls = self.tokenizer.token_to_id("[CLS]")
        sep = self.tokenizer.token_to_id("[SEP]")
        body = WINDOW - 2
        starts = list(range(0, max(1, len(ids) - body + STRIDE), STRIDE)) or [0]
        if len(starts) > MAX_WINDOWS:
            # Injections are usually near the start or the end of a page.
            half = MAX_WINDOWS // 2
            starts = starts[:half] + starts[-half:]
        best = 0.0
        for i in range(0, len(starts), BATCH):
            windows = [[cls, *ids[s:s + body], sep] for s in starts[i:i + BATCH]]
            width = max(len(w) for w in windows)
            input_ids = np.array([w + [0] * (width - len(w)) for w in windows], dtype=np.int64)
            mask = np.array([[1] * len(w) + [0] * (width - len(w)) for w in windows], dtype=np.int64)
            feed = {"input_ids": input_ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(input_ids)
            logits = self.session.run(None, feed)[0]
            exp = np.exp(logits - logits.max(axis=1, keepdims=True))
            probs = exp / exp.sum(axis=1, keepdims=True)
            best = max(best, float(probs[:, self.injection_index].max()))
        return best, len(starts)


def make_handler(screen: Screen, token: str):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                self._send(200, {"ok": True, "threshold": screen.threshold})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if token and not hmac.compare_digest(
                    self.headers.get("Authorization", "").removeprefix("Bearer ").strip(), token):
                self._send(401, {"error": "unauthorized"})
                return
            if self.path != "/screen":
                self._send(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            text = json.loads(self.rfile.read(length) or b"{}").get("text", "")
            score, n = screen.score(str(text))
            self._send(200, {"injection": score >= screen.threshold, "score": round(score, 4),
                             "windows": n})

        def log_message(self, *args):  # quiet: never log the screened text
            pass
    return Handler


def main() -> None:
    screen = Screen(os.environ.get("INJECTION_SCREEN_MODEL_DIR", "/models/prompt-injection"),
                    float(os.environ.get("INJECTION_SCREEN_THRESHOLD", "0.95")))
    host = os.environ.get("INJECTION_SCREEN_HOST", "127.0.0.1")
    port = int(os.environ.get("INJECTION_SCREEN_PORT", "8795"))
    ThreadingHTTPServer((host, port), make_handler(screen, os.environ.get("INJECTION_SCREEN_TOKEN", ""))).serve_forever()


if __name__ == "__main__":
    main()
