#!/usr/bin/env python3
"""Run the LLM-judge BIAS audit against real models (task #36, the bias half).

The stability half pinned ``JUDGE_TEMPERATURE`` at the four grader sites. That
fixed variance. This measures bias — a judge can be perfectly reproducible and
reproducibly wrong.

Four probes (see ``prax/eval/judge_bias.py`` for what each one means):
``length_bias``, ``verdict_drift``, ``criterion_order``, ``self_preference``.

Usage
-----
    uv run python scripts/judge_bias_audit.py --goldens research_multiperspective \\
        --trials 5 --orders 3

    # add the generator x judge grid that self-preference needs
    uv run python scripts/judge_bias_audit.py --families \\
        openai=openai:gpt-5.4-nano,deepseek=openrouter:deepseek/deepseek-v4-flash-0731

Design notes
------------
* **The judge under test is the REAL default judge** — the same ``eval_judge``
  routing and ``JUDGE_TEMPERATURE`` the eval suite uses. Measuring a judge we do
  not ship would be measuring nothing.
* **The rubric is frozen for the campaign.** Every probe re-grades the same
  golden; if a rubric changed between probes the numbers would not be comparable.
  The script fingerprints each golden's rubric and aborts on a mid-run change —
  the read-only-scorer property, learned the hard way on 2026-08-07.
* **Self-preference needs a 2x2 and is SKIPPED, loudly, without one.** One judge
  disagreeing with another is disagreement, not self-preference.
* **A probe that errors is reported as errored**, never as an effect of zero. A
  broken probe is not evidence of an unbiased judge.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Route egress through the secrets-proxy exactly as the live server does. An
# ad-hoc script that skips this gets a 401 that looks like a bad key — pydantic
# reads .env itself and does NOT export to os.environ.
from prax.settings import _export_proxy_env_from_dotenv  # noqa: E402

_export_proxy_env_from_dotenv()

from prax.eval import judge_bias  # noqa: E402
from prax.eval.goldens import load_goldens  # noqa: E402


def _rubric_fingerprint(golden) -> str:
    """Hash the criterion keys, weights and descriptions — the scorer's identity."""
    payload = json.dumps(
        [[c.key, c.weight, c.description, c.verify] for c in golden.rubric],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _make_judge(provider: str, model: str):
    """A ``callable(prompt) -> str`` backed by a named model, pinned to judge temp."""
    from prax.agent.llm_factory import build_llm
    from prax.settings import settings

    llm = build_llm(provider=provider, model=model,
                    temperature=settings.judge_temperature)

    def judge(prompt: str) -> str:
        resp = llm.invoke(prompt)
        return resp.content if hasattr(resp, "content") else str(resp)

    return judge


def _generate(provider: str, model: str, prompt: str) -> str:
    """Generate an answer with a named model — the generator half of the 2x2.

    Deliberately WARM (0.7): these are generations, not gradings. Pinning a
    generator to 0 would be the mirror of the bug this whole task started from.
    """
    from prax.agent.llm_factory import build_llm
    llm = build_llm(provider=provider, model=model, temperature=0.7)
    resp = llm.invoke(prompt)
    return resp.content if hasattr(resp, "content") else str(resp)


def _parse_families(spec: str) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        family, _, target = part.partition("=")
        provider, _, model = target.partition(":")
        if not (family and provider and model):
            raise SystemExit(f"bad --families entry {part!r}; want family=provider:model")
        out[family.strip()] = (provider.strip(), model.strip())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--goldens", default="",
                    help="comma-separated golden ids (default: every golden with judged criteria)")
    ap.add_argument("--answer-model", default="openrouter:deepseek/deepseek-v4-flash-0731",
                    help="provider:model used to produce the answer under grading")
    ap.add_argument("--families", default="",
                    help="family=provider:model,... — enables the self-preference 2x2")
    ap.add_argument("--trials", type=int, default=5, help="verdict_drift re-grades")
    ap.add_argument("--orders", type=int, default=3, help="criterion_order permutations")
    ap.add_argument("--out", default="", help="write the full JSON record here")
    args = ap.parse_args()

    wanted = {g.strip() for g in args.goldens.split(",") if g.strip()}
    goldens = [g for g in load_goldens()
               if (not wanted or g.id in wanted)
               and any(not c.verify for c in g.rubric)]
    if not goldens:
        raise SystemExit("no goldens with judged criteria matched")

    families = _parse_families(args.families)
    ap_provider, _, ap_model = args.answer_model.partition(":")

    out_path = Path(args.out) if args.out else Path(
        os.environ.get("PRAX_EVAL_DIR", "/tmp")) / "judge-bias" / "audit.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "started": datetime.now(UTC).isoformat(),
        "answer_model": args.answer_model,
        "families": {k: f"{v[0]}:{v[1]}" for k, v in families.items()},
        "trials": args.trials,
        "orders": args.orders,
        "goldens": {},
    }

    def _persist() -> None:
        """Checkpoint after every golden — a killed run still reports its finished work."""
        out_path.write_text(json.dumps(record, indent=2, default=str))

    _persist()
    for g in goldens:
        fp_before = _rubric_fingerprint(g)
        print(f"\n=== {g.id}  (rubric {fp_before}, "
              f"{sum(1 for c in g.rubric if not c.verify)} judged criteria) ===", flush=True)

        try:
            answer = _generate(ap_provider, ap_model, g.prompt)
        except Exception as exc:
            print(f"  answer generation FAILED: {type(exc).__name__}: {exc}")
            record["goldens"][g.id] = {"error": f"answer generation: {exc}"}
            _persist()
            continue
        print(f"  answer: {len(answer)} chars from {args.answer_model}", flush=True)

        answers: dict[str, str] = {}
        judges: dict[str, object] = {}
        if len(families) >= 2:
            for fam, (prov, model) in families.items():
                try:
                    answers[fam] = _generate(prov, model, g.prompt)
                    judges[fam] = _make_judge(prov, model)
                    print(f"  [{fam}] answer {len(answers[fam])} chars + judge ready", flush=True)
                except Exception as exc:
                    print(f"  [{fam}] FAILED: {type(exc).__name__}: {exc}")
                    answers.pop(fam, None)
                    judges.pop(fam, None)

        try:
            out = judge_bias.run_bias_audit(
                g, answer, trials=args.trials, orders=args.orders,
                answers=answers or None, judges=judges or None,
            )
        except Exception as exc:
            print(f"  PROBES ERRORED: {type(exc).__name__}: {exc}")
            record["goldens"][g.id] = {"error": f"probes: {exc}"}
            _persist()
            continue

        fp_after = _rubric_fingerprint(g)
        if fp_after != fp_before:
            raise SystemExit(f"ABORT: {g.id} rubric changed mid-campaign "
                             f"({fp_before} -> {fp_after}); results are not comparable")

        for line in out["summary"]:
            print(f"  {line}", flush=True)
        for s in out["skipped"]:
            print(f"  SKIPPED: {s}", flush=True)

        record["goldens"][g.id] = {
            "rubric_fingerprint": fp_before,
            "answer_chars": len(answer),
            "results": {k: v.as_record() for k, v in out["results"].items()},
            "flagged": out["flagged"],
            "void": out["void"],
            "skipped": out["skipped"],
        }
        _persist()

    # ---- aggregate, honestly -------------------------------------------------
    print("\n" + "=" * 70)
    scored = {gid: r for gid, r in record["goldens"].items() if "results" in r}
    errored = [gid for gid, r in record["goldens"].items() if "error" in r]
    print(f"AUDIT COMPLETE — {len(scored)} golden(s) probed"
          + (f", {len(errored)} ERRORED ({', '.join(errored)})" if errored else ""))
    for probe in ("length_bias", "verdict_drift", "criterion_order", "self_preference"):
        vals = [r["results"][probe]["effect"] for r in scored.values()
                if probe in r["results"] and not r["results"][probe]["void"]]
        weak = [gid for gid, r in scored.items()
                if probe in r["results"] and r["results"][probe].get("low_power")]
        voids = [gid for gid, r in scored.items()
                 if probe in r["results"] and r["results"][probe]["void"]]
        if not vals:
            print(f"  {probe:<17} not measured"
                  + (f" (void on {', '.join(voids)})" if voids else ""))
            continue
        hits = [gid for gid, r in scored.items()
                if probe in r["results"] and r["results"][probe]["flagged"]]
        print(f"  {probe:<17} mean effect {sum(vals)/len(vals):+.3f}  "
              f"(n={len(vals)} goldens, flagged on {len(hits)}: {', '.join(hits) or '-'})"
              + (f"  [LOW POWER on {len(weak)}: {', '.join(weak)}]" if weak else ""))
    print("=" * 70)
    print("n is small. These are effect sizes on a handful of goldens, not a rate.")

    record["finished"] = datetime.now(UTC).isoformat()
    _persist()
    print(f"record: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
