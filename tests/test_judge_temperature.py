"""A grader must not inherit the agent's creativity setting.

Found auditing Prax's LLM-judge surfaces (#36). Every `eval_judge` call site
built its model with no explicit temperature, so it fell through to
`AGENT_TEMPERATURE` — 0.7, the knob tuned to make the *agent* write well.
Verified from `build_llm`'s own log line:

    build_llm -> provider=openai model=gpt-5.4-nano tier=low temp=0.7

The consequence is that a verdict was stochastic: the same answer could be
graded pass on one run and fail on the next, and a published eval number was
not reproducible. That is the same "same case, different verdict" instability
already observed on the injection cases, one layer down — at the judge rather
than the regex.

Note the distinction these tests are careful about: a *generator* at 0.7 is
correct. The multiturn user-simulator and the agent-under-test should be warm.
Only the graders are pinned.
"""

import re
from pathlib import Path

import pytest

from prax.settings import AppSettings

ROOT = Path(__file__).resolve().parents[1] / "prax"

# Call sites whose output is a VERDICT: they grade, gate, or score.
JUDGE_SITES = [
    ("eval/goldens.py", "eval_judge"),
    ("eval/runner.py", "eval_judge"),
    ("eval/live_eval.py", "eval_judge"),
    ("eval/self_regen.py", "self_regen_auditor"),
]


class TestDefault:
    def test_judge_temperature_defaults_to_zero(self):
        s = AppSettings(_env_file=None)
        assert s.judge_temperature == 0.0

    def test_it_is_not_the_agent_temperature(self):
        """The whole point: graders get their own knob. If these ever collapse
        into one setting, a change made for generation quality silently
        destabilises every verdict."""
        s = AppSettings(_env_file=None)
        assert s.judge_temperature != s.agent_temperature


@pytest.mark.parametrize("relpath,config_key", JUDGE_SITES,
                         ids=[f"{p}:{k}" for p, k in JUDGE_SITES])
def test_judge_sites_pin_their_temperature(relpath, config_key):
    """Source-scanned rather than executed: these sites need API keys to run,
    and the property is about how the model is CONSTRUCTED."""
    src = (ROOT / relpath).read_text()
    call = re.search(
        r"build_llm\([^)]*config_key=[\"']" + re.escape(config_key) + r"[\"'][^)]*\)",
        src, re.DOTALL)
    assert call, f"no build_llm call with config_key={config_key!r} in {relpath}"
    assert "judge_temperature" in call.group(0), (
        f"{relpath} builds the {config_key!r} grader without pinning "
        f"temperature — it will inherit AGENT_TEMPERATURE (0.7) and its "
        f"verdicts will not be reproducible")


def test_no_new_judge_site_inherits_the_agent_temperature():
    """Catches a grader added later. Scans for judge-ish config_keys and
    requires each to pin a temperature.

    Deliberately keyed on `config_key` names containing judge/audit/grade/
    review rather than on every build_llm call: a generator SHOULD be warm, and
    a rule that fires on generators would be turned off."""
    offenders: list[str] = []
    for py in ROOT.rglob("*.py"):
        src = py.read_text()
        for call in re.finditer(r"build_llm\((?:[^()]|\([^()]*\))*\)", src, re.DOTALL):
            text = call.group(0)
            key = re.search(r"config_key=[\"']([a-z_]+)[\"']", text)
            if not key:
                continue
            name = key.group(1)
            if not any(w in name for w in ("judge", "audit", "grade", "review")):
                continue
            if "temperature" not in text:
                offenders.append(f"{py.relative_to(ROOT)}: config_key={name!r}")
    assert not offenders, (
        "grader surfaces built without an explicit temperature — they inherit "
        "AGENT_TEMPERATURE and their verdicts become stochastic:\n  "
        + "\n  ".join(offenders))


def test_generators_are_left_warm():
    """The counterpart guard. If someone 'fixes' the proposer or the user
    simulator to 0 as well, this fails — a generator pinned to 0 is a
    different bug, not the same fix applied consistently."""
    src = (ROOT / "eval" / "self_regen.py").read_text()
    proposer = re.search(
        r"build_llm\([^)]*config_key=[\"']self_regen_proposer[\"'][^)]*\)", src, re.DOTALL)
    assert proposer, "self_regen_proposer call not found"
    assert "judge_temperature" not in proposer.group(0), (
        "the proposer GENERATES candidate patches — pinning it to the judge "
        "temperature would remove the variation the search depends on")


def test_note_quality_gate_uses_judge_temperature_not_a_hardcoded_warm_value():
    """`note_quality` returns ``approved: bool`` — a GATE, so its verdict must not drift.

    It shipped at a hardcoded 0.2: the same defect the eval graders had at 0.7,
    one layer over and with a smaller blast radius. Small is not correct.
    """
    import prax.services.note_quality as nq
    from prax.settings import settings

    captured = {}

    def fake_build_llm(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop here — we only care about how the LLM was built")

    import prax.agent.llm_factory as factory
    import prax.plugins.llm_config as cfgmod
    orig_build, orig_cfg = factory.build_llm, cfgmod.get_component_config
    factory.build_llm = fake_build_llm
    cfgmod.get_component_config = lambda key: {}
    try:
        nq.llm_review("A title", "some note content")
    except Exception:
        pass
    finally:
        factory.build_llm, cfgmod.get_component_config = orig_build, orig_cfg

    assert captured.get("temperature") == settings.judge_temperature
    assert captured["temperature"] == 0.0


def test_an_explicit_routing_temperature_still_wins_for_the_note_gate():
    """Operators keep control — llm_routing.yaml overrides the default."""
    import prax.agent.llm_factory as factory
    import prax.plugins.llm_config as cfgmod
    import prax.services.note_quality as nq

    captured = {}

    def fake_build_llm(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop")

    orig_build, orig_cfg = factory.build_llm, cfgmod.get_component_config
    factory.build_llm = fake_build_llm
    cfgmod.get_component_config = lambda key: {"temperature": 0.4}
    try:
        nq.llm_review("A title", "content")
    except Exception:
        pass
    finally:
        factory.build_llm, cfgmod.get_component_config = orig_build, orig_cfg

    assert captured.get("temperature") == 0.4
