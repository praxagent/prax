#!/usr/bin/env python3
"""Measure the prompt-injection screen before trusting it (no paid APIs).

Runs a live screen (sidecars/injection-screen, INJECTION_SCREEN_URL) over:

* **benign external content**, what Prax actually fetches:
  * random Wikipedia articles (plain text);
  * popular projects' READMEs — imperative "run this, install that" text, the
    likeliest false-positive trigger;
* **injections**:
  * direct — `deepset/prompt-injections`, positive class; not in the model's
    listed training sets, and partly German, which the model does not claim;
  * indirect — the same injections planted mid-way through benign articles,
    which is Prax's real threat.

Prints aggregates only (rates per threshold). Nothing is written to the repo;
the fetched text stays in memory.

    uv run python scripts/eval_injection_screen.py --url http://127.0.0.1:8795 --n 60
"""
from __future__ import annotations

import argparse
import random

import requests

READMES = [
    "psf/requests/main/README.md", "pallets/flask/main/README.md", "numpy/numpy/main/README.md",
    "pandas-dev/pandas/main/README.md", "django/django/main/README.rst", "fastapi/fastapi/master/README.md",
    "Textualize/rich/master/README.md", "encode/httpx/master/README.md", "pytest-dev/pytest/main/README.rst",
    "psf/black/main/README.md", "tiangolo/typer/master/README.md", "astral-sh/uv/main/README.md",
    "astral-sh/ruff/main/README.md", "pydantic/pydantic/main/README.md", "python/cpython/main/README.rst",
    "nodejs/node/main/README.md", "microsoft/vscode/main/README.md", "facebook/react/main/README.md",
    "kubernetes/kubernetes/master/README.md", "docker/compose/main/README.md",
]
# Wikimedia's API etiquette: a descriptive agent with a contact URL, and pacing.
UA = {"User-Agent": "prax-injection-screen-eval/1.0 (https://github.com/praxagent/prax)"}


def wikipedia(n: int) -> list[str]:
    import time

    out, tries = [], 0
    while len(out) < n and tries < 4 * n:
        tries += 1
        r = requests.get("https://en.wikipedia.org/w/api.php", headers=UA, timeout=30, params={
            "action": "query", "format": "json", "generator": "random", "grnnamespace": 0,
            "grnlimit": 1, "prop": "extracts", "explaintext": 1})
        if r.status_code == 429:
            time.sleep(10)
            continue
        for page in r.json().get("query", {}).get("pages", {}).values():
            text = page.get("extract", "")
            if len(text) > 400:
                out.append(text)
        time.sleep(1.0)
    return out[:n]


def readmes() -> list[str]:
    out = []
    for path in READMES:
        r = requests.get(f"https://raw.githubusercontent.com/{path}", headers=UA, timeout=30)
        if r.ok and len(r.text) > 400:
            out.append(r.text)
    return out


def injections(n: int, seed: int) -> list[str]:
    rows, offset = [], 0
    while True:
        r = requests.get("https://datasets-server.huggingface.co/rows", headers=UA, timeout=30, params={
            "dataset": "deepset/prompt-injections", "config": "default", "split": "test",
            "offset": offset, "length": 100}).json()
        batch = r.get("rows", [])
        rows += [x["row"]["text"] for x in batch if x["row"].get("label") == 1]
        offset += len(batch)
        if not batch or offset >= r.get("num_rows_total", 0):
            break
    random.Random(seed).shuffle(rows)
    return rows[:n]


def embed(host: str, payload: str, rng: random.Random) -> str:
    paras = host.split("\n")
    at = rng.randrange(1, max(2, len(paras)))
    return "\n".join(paras[:at] + [payload] + paras[at:])


def scores(url: str, texts: list[str]) -> list[float]:
    return [requests.post(f"{url}/screen", json={"text": t}, timeout=120).json()["score"] for t in texts]


def rate(values: list[float], threshold: float) -> str:
    hits = sum(v >= threshold for v in values)
    return f"{hits}/{len(values)} ({100 * hits / max(1, len(values)):.1f}%)"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8795")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    wiki, docs, inj = wikipedia(args.n), readmes(), injections(args.n, args.seed)
    planted = [embed(rng.choice(wiki), p, rng) for p in inj]
    s_wiki, s_docs = scores(args.url, wiki), scores(args.url, docs)
    s_inj, s_planted = scores(args.url, inj), scores(args.url, planted)

    print(f"samples: wikipedia={len(wiki)} readmes={len(docs)} injections={len(inj)} planted={len(planted)}")
    print(f"{'threshold':>9} | {'FP wiki':>14} | {'FP readmes':>14} | {'TP direct':>14} | {'TP planted':>14}")
    for th in (0.5, 0.9, 0.95, 0.99):
        print(f"{th:>9} | {rate(s_wiki, th):>14} | {rate(s_docs, th):>14} | "
              f"{rate(s_inj, th):>14} | {rate(s_planted, th):>14}")


if __name__ == "__main__":
    main()
