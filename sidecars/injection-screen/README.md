# Injection screen (sidecar)

An independent prompt-injection classifier, run outside Prax. When
`INJECTION_SCREEN_URL` is set, Prax scores untrusted tool results with it
before they enter the model's context. It either labels flagged content
(`INJECTION_SCREEN_MODE=label`) or withholds it (`block`).

**Status: not recommended with the default model.** We measured it on
2026-09-24 with `scripts/eval_injection_screen.py`: no paid APIs, 60 random
Wikipedia articles, 20 popular projects' READMEs, and 60 `deepset/prompt-injections`
positives, used directly and planted inside the articles. At threshold 0.95:

| | rate |
|---|---|
| Wikipedia falsely flagged | 0/60 (0%) |
| READMEs falsely flagged | 5/20 (25%) |
| Direct injections caught | 19/60 (32%) — part of the set is German, which the model does not claim |
| **Injections planted in pages caught** | **3/60 (5%)** — the case Prax actually faces |

The default model is ProtectAI `deberta-v3-base-prompt-injection-v2`
(ONNX, Apache-2.0). It was trained on direct prompts; it does not find an
instruction buried in a page, and it reads documentation as instructions.
Treat this sidecar as a slot: plug in a better model, run the eval, and
enable it only if the numbers justify it.

**Resources.** ~1.3 GB of RAM at rest, with a ~1.8 GB peak while scoring.
Latency is about 30 ms for a short text, but around 20 s for a text that
fills the 48-window cap, so Prax sends at most 20,000 characters. Always run
it under a memory limit. Uncapped, an earlier version scored every window of
a long page in one batch, grew to 5.6 GB and triggered the host's OOM killer.
The window cap and mini-batches now bound memory by construction.
