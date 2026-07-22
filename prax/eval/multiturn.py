"""Multi-turn capability evals — persona-driven conversations, graded on the final
state, reported as **pass^k** (reliability, not one lucky shot).

Everything else in the eval engine grades Prax on a **single** turn. Real tasks are
dialogues: the user is vague then clarifies, corrects a wrong assumption, adds a
constraint late. This suite runs a persona-driven **user simulator** against the
agent for up to N turns and grades the whole conversation **deterministically** on
what actually got accomplished — then repeats the case k times and reports
``pass^k`` (all k must pass). That's the τ²-bench insight: an agent that succeeds
*once* but not *reliably* isn't production-ready, and single-shot accuracy hides it.

Keyless by construction — the agent and the user simulator are **injected
functions**, so the loop, grading, and pass^k are unit-tested with zero API keys.
Only the live executors (`orchestrator_agent`, `bare_agent`, `llm_user_simulator`)
call a model. The grading reuses the deterministic capability checks
(contains/regex/absent/spoke/tool) — "verifiable beats judgeable", multi-turn.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from prax.eval.capability import CapCheck, CaseRun, _check_pass, _weighted

logger = logging.getLogger(__name__)

CASES_DIR = Path(__file__).parent / "multiturn_cases"

# The user simulator emits this token when its goal is met — ends the conversation
# early (a good agent resolves the task in fewer turns; that's part of the signal).
DONE_SIGNAL = "TASK_COMPLETE"


@dataclass
class MultiTurnCase:
    """One persona-driven multi-turn task, graded on the final conversation state."""
    id: str
    persona: str                       # the user simulator's goal + character
    opening: str                       # the first user message
    checks: list[CapCheck] = field(default_factory=list)  # graded on the transcript
    max_turns: int = 6                 # agent replies before we stop
    title: str = ""
    notes: str = ""


@dataclass
class Turn:
    role: str      # "user" | "assistant"
    content: str


@dataclass
class AgentReply:
    content: str = ""
    tools: list[str] = field(default_factory=list)
    spokes: list[str] = field(default_factory=list)
    tokens: int = 0


@dataclass
class Transcript:
    turns: list[Turn] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    spokes: list[str] = field(default_factory=list)
    error: str = ""
    tokens: int = 0

    def assistant_text(self) -> str:
        """All assistant turns joined (convenience / transcript view)."""
        return "\n".join(t.content for t in self.turns if t.role == "assistant")

    def final_answer(self) -> str:
        """The LAST assistant turn — the 'final answer' content checks grade, so a
        late constraint ('rewrite without word X') is judged on the result, not on
        earlier drafts that legitimately still contained X."""
        for t in reversed(self.turns):
            if t.role == "assistant":
                return t.content
        return ""

    def agent_turns(self) -> int:
        return sum(1 for t in self.turns if t.role == "assistant")


# Injected callable contracts:
#   AgentFn:   (history: list[Turn]) -> AgentReply         # respond to the last user turn
#   UserSimFn: (case, history: list[Turn]) -> str          # next user msg (may hold DONE_SIGNAL)
AgentFn = Callable[[list[Turn]], AgentReply]
UserSimFn = Callable[["MultiTurnCase", list[Turn]], str]


def run_conversation(case: MultiTurnCase, agent_fn: AgentFn, user_sim_fn: UserSimFn) -> Transcript:
    """Drive one persona conversation: opening → (agent ↔ user)* up to max_turns.

    Ends early when the user simulator emits ``DONE_SIGNAL``. Any executor error is
    captured (not raised) so one bad trial never takes down a pass^k run.
    """
    tr = Transcript(turns=[Turn("user", case.opening)])
    for _ in range(max(1, case.max_turns)):
        try:
            reply = agent_fn(tr.turns)
        except Exception as exc:  # noqa: BLE001 - an executor failure is a failed trial, not a crash
            tr.error = f"agent: {type(exc).__name__}: {exc}"
            break
        tr.turns.append(Turn("assistant", reply.content))
        tr.tools.extend(reply.tools)
        tr.spokes.extend(reply.spokes)
        tr.tokens += reply.tokens

        try:
            nxt = user_sim_fn(case, tr.turns)
        except Exception as exc:  # noqa: BLE001
            tr.error = f"user_sim: {type(exc).__name__}: {exc}"
            break
        if DONE_SIGNAL in (nxt or ""):
            break
        tr.turns.append(Turn("user", nxt))
    return tr


def grade_conversation(case: MultiTurnCase, tr: Transcript) -> dict:
    """Score the transcript against the case's deterministic checks. Pure — no LLM.

    Content checks (contains/regex/absent) grade the FINAL assistant turn; tool and
    spoke checks grade the whole conversation (a route taken *anywhere* counts). A
    trial passes only if every weighted check is satisfied AND no executor error.
    """
    run = CaseRun(
        answer=tr.final_answer(),
        tools=list(dict.fromkeys(tr.tools)),
        spokes=list(dict.fromkeys(tr.spokes)),
        error=tr.error,
        tokens=tr.tokens,
    )
    total = _weighted(case.checks, run) or 0.0
    passed = bool(total >= 0.999 and not tr.error)
    scores = {f"{c.kind}:{c.value}": (1.0 if _check_pass(c, run) else 0.0) for c in case.checks}
    return {
        "total": total,
        "passed": passed,
        "agent_turns": tr.agent_turns(),
        "tokens": tr.tokens,
        "error": tr.error,
        "scores": scores,
    }


def pass_hat_k(case: MultiTurnCase, agent_fn: AgentFn, user_sim_fn: UserSimFn, *, k: int = 3) -> dict:
    """Run the case ``k`` independent times; ``pass^k`` = 1.0 iff ALL k pass.

    Also reports the per-trial pass rate (the reliability curve) and average cost —
    a case that passes 2/3 has ``pass^k=0`` but ``trial_pass_rate≈0.67``, which is
    exactly the production-readiness gap single-shot accuracy hides.
    """
    k = max(1, k)
    trials = [grade_conversation(case, run_conversation(case, agent_fn, user_sim_fn)) for _ in range(k)]
    n_pass = sum(1 for t in trials if t["passed"])
    return {
        "id": case.id,
        "k": k,
        "pass_hat_k": 1.0 if n_pass == k else 0.0,
        "trial_pass_rate": round(n_pass / k, 3),
        "avg_tokens": round(sum(t["tokens"] for t in trials) / k, 1),
        "trials": trials,
    }


# ---------------------------------------------------------------------------
# Loading (YAML, mirrors the capability suite)
# ---------------------------------------------------------------------------

def load_multiturn_cases(directory: Path | None = None) -> list[MultiTurnCase]:
    """Load every ``*.yaml`` multi-turn case. Malformed files are skipped, not raised."""
    import yaml

    directory = directory or CASES_DIR
    out: list[MultiTurnCase] = []
    if not directory.exists():
        return out
    seen: set[str] = set()
    for path in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text()) or {}
            cid = str(data.get("id") or path.stem)
            if cid in seen:
                logger.warning("Duplicate multi-turn case id %r — skipping", cid)
                continue
            seen.add(cid)
            checks = [
                CapCheck(kind=str(c["kind"]), value=str(c.get("value", "")),
                         weight=float(c.get("weight", 1.0)))
                for c in (data.get("checks") or [])
            ]
            out.append(MultiTurnCase(
                id=cid,
                persona=str(data.get("persona", "")),
                opening=str(data.get("opening", "")),
                checks=checks,
                max_turns=int(data.get("max_turns", 6)),
                title=str(data.get("title", "")),
                notes=str(data.get("notes", "")),
            ))
        except Exception:
            logger.warning("Skipping malformed multi-turn case %s", path, exc_info=True)
    return out


# ---------------------------------------------------------------------------
# Suite runner
# ---------------------------------------------------------------------------

def run_multiturn_suite(cases: list[MultiTurnCase] | None = None, *,
                        tier: str = "medium", model_override: str | None = None,
                        k: int = 3, agent_factory: Callable[[MultiTurnCase], AgentFn] | None = None,
                        user_sim: UserSimFn | None = None) -> dict:
    """Run every case k times and aggregate pass^k. Deterministically graded.

    ``agent_factory(case) -> AgentFn`` and ``user_sim`` are injectable — the default
    is the live orchestrator + a cheap LLM user simulator; a test passes stubs to
    run the whole suite with no API keys.
    """
    cases = cases if cases is not None else load_multiturn_cases()
    sim = user_sim or llm_user_simulator(tier="low")
    results: list[dict] = []
    for case in cases:
        agent = (agent_factory(case) if agent_factory
                 else orchestrator_agent(tier=tier, model_override=model_override, case_id=case.id))
        res = pass_hat_k(case, agent, sim, k=k)
        results.append({"title": case.title, **res})

    n = len(results) or 1
    agg = {
        "cases": len(results),
        "k": k,
        "pass_hat_k": round(sum(r["pass_hat_k"] for r in results) / n, 3),
        "avg_trial_pass_rate": round(sum(r["trial_pass_rate"] for r in results) / n, 3),
        "avg_tokens": round(sum(r["avg_tokens"] for r in results) / n, 1),
    }
    return {"aggregate": agg, "results": results}


# ---------------------------------------------------------------------------
# Live executors — call a model (never touched by the keyless unit tests)
# ---------------------------------------------------------------------------

_USER_SIM_SYSTEM = """\
You are role-playing a USER talking to an AI assistant. Stay in character and pursue
your goal; reply as the user would — short, natural, one message. Do NOT act as the
assistant. When your goal is fully met, reply with exactly the token {done} and
nothing else. Your persona and goal:

{persona}"""


def llm_user_simulator(*, tier: str = "low", model_override: str | None = None) -> UserSimFn:
    """A live user simulator backed by a (cheap) model. Returns the next user turn."""
    def _sim(case: MultiTurnCase, history: list[Turn]) -> str:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        from prax.agent.llm_factory import build_llm
        llm = build_llm(model=model_override, tier=(None if model_override else tier))
        sys = _USER_SIM_SYSTEM.format(done=DONE_SIGNAL, persona=case.persona)
        # From the user's POV the roles flip: the assistant's turns are the "human"
        # input the simulator is reacting to, and prior user turns are its own.
        msgs: list = [SystemMessage(content=sys)]
        for t in history:
            msgs.append(AIMessage(content=t.content) if t.role == "user"
                        else HumanMessage(content=t.content))
        resp = llm.invoke(msgs)
        return getattr(resp, "content", "") or ""
    return _sim


def _history_to_messages(history: list[Turn]):
    """Split history into (prior conversation messages, latest user_input)."""
    from langchain_core.messages import AIMessage, HumanMessage
    msgs = []
    for t in history[:-1]:
        msgs.append(HumanMessage(content=t.content) if t.role == "user"
                    else AIMessage(content=t.content))
    latest = history[-1].content if history and history[-1].role == "user" else ""
    return msgs, latest


def bare_agent(*, tier: str = "medium", model_override: str | None = None) -> AgentFn:
    """Baseline agent: a naked LLM with the running history, no tools/routing.

    The control condition for a multi-turn harness-lift (full orchestrator vs this).
    """
    def _agent(history: list[Turn]) -> AgentReply:
        from langchain_core.messages import AIMessage, HumanMessage

        from prax.agent.llm_factory import build_llm
        from prax.eval.telemetry import collect_usage
        msgs = [HumanMessage(content=t.content) if t.role == "user" else AIMessage(content=t.content)
                for t in history]
        try:
            llm = build_llm(model=model_override, tier=(None if model_override else tier))
            with collect_usage() as usage:
                resp = llm.invoke(msgs)
            return AgentReply(content=getattr(resp, "content", "") or "",
                              tokens=int(usage.snapshot().get("total_tokens", 0)))
        except Exception as exc:  # noqa: BLE001
            return AgentReply(content="", tokens=0, tools=[f"error:{type(exc).__name__}"])
    return _agent


def orchestrator_agent(*, tier: str = "medium", model_override: str | None = None,
                       case_id: str = "mt") -> AgentFn:
    """Full Prax harness as a multi-turn agent: each call runs the orchestrator with
    the prior conversation threaded in via ``agent.run(conversation=…)``.

    Stateless per call (replays history), reusing the capability suite's isolated
    eval scope + telemetry. Live-only; unit tests inject a stub instead.
    """
    def _agent(history: list[Turn]) -> AgentReply:
        from prax.eval import PRAX_EVAL_DIR
        from prax.eval.gaia_single import _isolated_prax_scope
        from prax.eval.telemetry import collect_usage

        prior, latest = _history_to_messages(history)
        workspace = PRAX_EVAL_DIR / "runs" / f"mt-{case_id}" / "workspace"
        try:
            with collect_usage() as usage, _isolated_prax_scope(workspace, case_id, user_prefix="mt-eval"):
                from prax.agent.orchestrator import ConversationAgent
                agent = (ConversationAgent(model=model_override) if model_override
                         else ConversationAgent(tier=tier))
                answer = agent.run(conversation=prior, user_input=latest,
                                   trigger=f"[multiturn eval: {case_id}]")
            return AgentReply(content=answer or "", tools=list(usage.tools),
                              spokes=usage.spokes(),
                              tokens=int(usage.snapshot().get("total_tokens", 0)))
        except Exception as exc:  # noqa: BLE001
            return AgentReply(content="", tools=[f"error:{type(exc).__name__}"])
    return _agent
