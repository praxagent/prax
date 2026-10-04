"""Single interception point for tool governance.

Every tool invocation passes through ``wrap_with_governance`` before
reaching the LangGraph agent.  This is the ONE choke point where:

1. The tool's risk level is classified (with earned-trust downgrade)
2. Arguments are summarized/scrubbed
3. Confirmation requirement is evaluated (with smart auto-approve)
4. An audit record is emitted to the workspace trace log
5. Tool call budget is tracked (with agent-initiated escalation)
6. Prediction error is computed (Active Inference Phase 1)
7. Epistemic read-before-write gate is enforced (Phase 2)
8. Logprob entropy is checked when available (Phase 3)
9. Semantic entropy gate blocks divergent HIGH-risk calls (Phase 4)

Wired into the agent via ``tool_registry.get_registered_tools()`` (the hub)
and ``govern_spoke_tools`` (spoke-internal tool lists).

## Two layers, one wrapper

``layer="hub"`` (default) is the full stack above.  ``layer="spoke"`` wraps
the tools a spoke's own loop calls: it ALWAYS records (audit entry, lethal-
trifecta legs) and ENFORCES the confirmation gates (HIGH first-call block /
scoped confirm, trifecta escalation) only when ``enforce`` is true — which
``govern_spoke_tools`` derives from ``SPOKE_GOVERNANCE_ENABLED`` (default
off).  With enforcement off a spoke tool executes exactly as an unwrapped one:
no budget accounting, no loop/epistemic gates, no result tagging, no schema
change.  The hub's turn budget and Active-Inference gates stay hub-only.

## Per-turn state

Everything governance remembers about a turn lives in ONE
:class:`TurnGovernanceState` held in a :class:`~contextvars.ContextVar`.  The
orchestrator creates it with :func:`begin_turn` at turn start, binds the same
object into its graph worker thread (:func:`use_turn_state` — ContextVars do
not cross thread boundaries) and drains it with :func:`drain_audit_log` at turn
end.  Contexts copied from the turn (LangGraph's tool executor, spoke
``invoke_isolated``, ``delegate_parallel`` workers) share the object, so their
entries land in the turn that spawned them; two turns running in different
contexts never see each other's audit entries or confirmation latches.  A
context that never called ``begin_turn`` gets a state created on first use —
note that a state created inside a *copied* context (e.g. by a bare
``tool.invoke`` outside any turn) is not visible to the parent.
"""
from __future__ import annotations

import logging
import re
import sys
import types
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from prax.agent.action_policy import (
    RiskLevel,
    SourcedResult,
    SourceReliability,
    get_risk_level,
    get_tool_capability,
    log_action,
)
from prax.agent.hard_floors import CREDENTIAL_TOOLS
from prax.agent.message_text import WITHHELD_OUTPUT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-turn state
# ---------------------------------------------------------------------------

@dataclass
class TurnGovernanceState:
    """Everything governance remembers about ONE turn.

    ``audit``: entries flushed to the workspace trace by the orchestrator
    after each turn (via ``drain_audit_log``).

    ``high_risk_seen`` / ``high_risk_confirmed`` / ``high_risk_confirmed_tools``:
    the HIGH-risk confirmation latch.  First call of a HIGH tool returns a
    confirmation prompt; the second call executes.  With
    ``HIGH_RISK_SCOPED_CONFIRM`` off (default) a user confirmation unlocks
    every HIGH tool for the rest of the turn (``high_risk_confirmed``); with it
    on, only the confirmed tool (``high_risk_confirmed_tools``).  Smart
    auto-approve only ever adds to the per-tool set.

    ``trifecta_*``: lethal-trifecta legs touched this turn and the DEDICATED
    (tool, args)-keyed confirmation latch for external sinks — never unlocked
    by an ordinary HIGH-risk confirmation earlier in the turn.

    ``tool_call_count`` / ``tool_call_budget``: soft per-turn tool-call budget
    (``budget == 0`` means unlimited).
    """

    audit: list[dict] = field(default_factory=list)
    high_risk_seen: set[str] = field(default_factory=set)
    high_risk_confirmed: bool = False
    high_risk_confirmed_tools: set[str] = field(default_factory=set)
    trifecta_untrusted: bool = False
    trifecta_private: bool = False
    trifecta_seen: set[str] = field(default_factory=set)
    trifecta_confirmed: set[str] = field(default_factory=set)
    # (tool, exact-arguments) keys a PERSON approved out of band this turn
    # (OUT_OF_BAND_APPROVALS_ENABLED). One approval covers both gates for that
    # exact call, so a call that is HIGH and closes the trifecta asks once.
    human_approved: set[str] = field(default_factory=set)
    # Whether this turn has told the sandbox egress gate it read private data.
    egress_tainted: bool = False
    tool_call_count: int = 0
    tool_call_budget: int = 0
    # The registry entry a person can cancel (prax.services.turn_registry).
    # None outside an orchestrator turn. Not cleared by reset(): it names the
    # turn, it is not state accumulated during it.
    turn: Any = None
    # spoke label -> failure summaries this turn (SPOKE_FAILURE_LIMIT).
    spoke_failures: dict[str, list[str]] = field(default_factory=dict)
    # spoke label -> delegations this turn, success or not (SPOKE_CALL_LIMIT).
    spoke_calls: dict[str, int] = field(default_factory=dict)
    # The turn's execution graph, for its running cost (TURN_BUDGET_USD).
    graph: Any = None
    # Tool calls refused because the turn is over budget.
    budget_refusals: int = 0
    # Secret values a credential tool handed out this turn, masked by every
    # observability sink (prax.agent.turn_secrets). In memory only, never in a
    # repr. Not cleared by reset(): the audit drain runs at turn end, but a
    # timed-out or stopped turn's graph worker can still be running tools on
    # this same object — they must stay masked. The values go away with the
    # object, which begin_turn replaces every turn.
    secrets: set[str] = field(default_factory=set, repr=False)

    def reset(self) -> None:
        """Clear everything IN PLACE.

        Identity is preserved on purpose: a worker thread or a copied context
        holding a reference to this state sees the reset too, exactly as every
        caller used to see the module globals being cleared.
        """
        self.audit.clear()
        self.high_risk_seen.clear()
        self.high_risk_confirmed = False
        self.high_risk_confirmed_tools.clear()
        self.human_approved.clear()
        if self.egress_tainted:
            self.egress_tainted = False
            try:
                from prax.services import egress_gate_service
                egress_gate_service.release(id(self))
            except Exception:
                pass
        self.trifecta_untrusted = False
        self.trifecta_private = False
        self.trifecta_seen.clear()
        self.trifecta_confirmed.clear()
        self.tool_call_count = 0
        self.tool_call_budget = 0
        self.spoke_failures.clear()
        self.spoke_calls.clear()
        self.budget_refusals = 0


_turn_state: ContextVar[TurnGovernanceState | None] = ContextVar(
    "prax_turn_governance_state", default=None,
)


def current_turn_state() -> TurnGovernanceState:
    """The current context's turn state, created on first use."""
    state = _turn_state.get()
    if state is None:
        state = TurnGovernanceState()
        _turn_state.set(state)
    return state


def peek_turn_state() -> TurnGovernanceState | None:
    """The current context's turn state, or ``None`` — never creates one."""
    return _turn_state.get()


def begin_turn(budget: int = 0) -> TurnGovernanceState:
    """Bind a FRESH state for a new turn in the current context.

    Called by the orchestrator at turn start (where ``init_turn_budget`` used
    to reset the budget on the shared globals).  A fresh object — not a reset
    of the existing one — is what keeps two turns in different contexts apart:
    each turn's tools, workers and spokes resolve to the object bound in their
    own context.  Returns the state so the caller can bind it into worker
    threads with :func:`use_turn_state`.
    """
    state = TurnGovernanceState(tool_call_budget=max(0, int(budget)))
    _turn_state.set(state)
    return state


@contextmanager
def use_turn_state(state: TurnGovernanceState):
    """Bind *state* as the current turn state for the duration of the block.

    For threads the turn spawns by hand (the orchestrator's graph worker):
    ``threading.Thread`` starts with an empty context, so the turn's state has
    to be re-bound inside the worker — the same reason the loop heartbeat is.
    """
    token = _turn_state.set(state)
    try:
        yield state
    finally:
        _turn_state.reset(token)


def drain_audit_log() -> list[dict]:
    """Return all buffered audit entries for the current turn and reset the
    turn's governance state (latches, legs, budget) in place."""
    state = current_turn_state()
    entries = list(state.audit)
    state.reset()
    # Reset loop detector at turn boundary.
    try:
        from prax.agent.loop_detector import reset as _reset_loops
        _reset_loops()
    except Exception:
        pass
    return entries


def init_turn_budget(budget: int) -> None:
    """Set the soft tool-call budget for the current turn (count restarts at 0)."""
    state = current_turn_state()
    state.tool_call_budget = budget
    state.tool_call_count = 0


def extend_budget(additional: int) -> None:
    """Increase the tool-call budget (called by request_extended_budget)."""
    current_turn_state().tool_call_budget += min(additional, 50)  # cap single extension at 50


def get_budget_status() -> tuple[int, int]:
    """Return (calls_used, budget)."""
    state = current_turn_state()
    return state.tool_call_count, state.tool_call_budget


def _trifecta_key(tool_name: str, kwargs: dict) -> str:
    """Bind the trifecta confirmation latch to the EXACT (tool, arguments) pair.

    Keying by tool name alone would let a confirmed sink be re-invoked with
    DIFFERENT (e.g. injection-substituted) arguments — so the arguments are hashed
    into the latch key. A second call only counts as confirmed if its arguments
    match the call the user was actually shown.

    ``expected_observation`` is governance's own field, not an argument of the
    action, and it is popped partway through a call: the hard-floor gate keys
    the call before the pop, the HIGH-risk gate after. Keying it would give one
    call two keys, so a person's floor approval was not recognised by the HIGH
    gate — which then asked again (twice, with out-of-band approvals on), or let
    the model "confirm" by calling a second time.
    """
    import hashlib
    import json
    kwargs = {k: v for k, v in (kwargs or {}).items() if k != "expected_observation"}
    try:
        blob = json.dumps(kwargs, sort_keys=True, default=str)
    except Exception:
        blob = repr(sorted((kwargs or {}).items()))
    return f"{tool_name}\x00{hashlib.sha256(blob.encode()).hexdigest()[:16]}"


# ---------------------------------------------------------------------------
# Smart auto-approve (browser interaction tools only)
# ---------------------------------------------------------------------------

# HIGH-risk browser tools that may be auto-approved when the user's own message
# explicitly asked for the interaction.  Nothing else is ever auto-approved.
_BROWSER_AUTO_APPROVE_TOOLS = frozenset({
    "browser_click", "browser_fill", "browser_request_login", "browser_finish_login",
})

# The rule, in full: the user's message must contain an interaction VERB and a
# browser OBJECT word (anywhere in the message), and the match unlocks ONLY the
# browser tool that asked.  "click the login button" / "fill in the email
# field" qualify; "check my email" or "read the page" do not — neither
# requests an interaction.  "log in" / "sign in" count as both verb and object
# (they name the action and the thing).  Read-only verbs (check, read, scroll,
# search, browse, visit, navigate) were removed: they never request a click,
# a fill or a login, and they used to unlock every HIGH tool for the turn.
_USER_ACTION_VERB_PATTERN = re.compile(
    r"\b(click|press|tap|fill|submit|enter|type|open|select|choose|log\s*in|sign\s*in)\b",
    re.IGNORECASE,
)
_BROWSER_OBJECT_PATTERN = re.compile(
    r"\b(link|button|(?:web)?page|(?:web)?site|form|field|login|log\s*in|sign\s*in)\b",
    re.IGNORECASE,
)


def _scoped_confirm() -> bool:
    """True when HIGH-risk confirmation should unlock only the confirmed tool."""
    try:
        from prax.settings import settings
        return bool(settings.high_risk_scoped_confirm)
    except Exception:
        return False


def spoke_governance_enforced() -> bool:
    """True when spoke-internal tools ENFORCE the confirmation gates
    (``SPOKE_GOVERNANCE_ENABLED``).  Recording is unconditional either way."""
    try:
        from prax.settings import settings
        return bool(settings.spoke_governance_enabled)
    except Exception:
        return False


# Turns nobody is present for. Their "user message" is a schedule's prompt or
# a Kanban card, written earlier and possibly by an agent (any MCP client with
# a space key can add a card), not a person speaking now.
_UNATTENDED_SOURCES = frozenset({"scheduler", "task_runner"})


def _attended_user_message() -> str:
    """The user's own words this turn, or "" when nobody is present.

    Consent read from a message ("share report.pdf publicly", "click the Buy
    button") means a person said it just now. In a scheduler or task-runner
    turn that is never true, so the text never counts as consent there; the
    decision goes to a person (parked, when out-of-band approvals are on).
    """
    from prax.agent.user_context import current_turn_source, current_user_message
    if current_turn_source.get() in _UNATTENDED_SOURCES:
        return ""
    return current_user_message.get("")


def _user_explicitly_requested_action(tool_name: str) -> bool:
    """True when the user's own message explicitly requested *tool_name*'s
    browser interaction — see ``_USER_ACTION_VERB_PATTERN`` for the rule.

    The user message is the only trusted text here; tool results are never
    consulted, so fetched content cannot satisfy this check, and neither can
    the prompt of an unattended turn (see :func:`_attended_user_message`).
    """
    if tool_name not in _BROWSER_AUTO_APPROVE_TOOLS:
        return False
    msg = _attended_user_message()
    if not msg:
        return False
    return bool(_USER_ACTION_VERB_PATTERN.search(msg) and _BROWSER_OBJECT_PATTERN.search(msg))


# ---------------------------------------------------------------------------
# The wrapper
# ---------------------------------------------------------------------------

def wrap_with_governance(
    tool: BaseTool,
    *,
    layer: str = "hub",
    enforce: bool = True,
    classify_as: BaseTool | None = None,
) -> BaseTool:
    """Wrap a tool with governance: risk classification, audit logging,
    and (for HIGH-risk tools) a confirmation gate.

    ``layer`` selects the stack (see the module docstring): ``"hub"`` is the
    full orchestrator-level stack; ``"spoke"`` records always and gates only
    when ``enforce`` is true.  ``classify_as`` names the tool whose code-set
    metadata (``_risk_level``, ``_trifecta_legs``) classifies this one — used
    when *tool* is a context-binding wrapper that does not carry the raw
    tool's attributes.

    Returns a ``StructuredTool`` that delegates to the original tool
    through the governance layer.
    """
    if layer not in ("hub", "spoke"):
        raise ValueError(f"unknown governance layer {layer!r}")
    hub = layer == "hub"
    meta_src = classify_as if classify_as is not None else tool
    tool_name = tool.name
    static_risk = getattr(meta_src, "_risk_level", None) or get_risk_level(tool_name)
    from prax.agent.user_context import capture_user_context, use_user_context
    bound_context = capture_user_context()

    # Static lethal-trifecta legs.  A delegate_<spoke> tool's legs are recorded
    # BEFORE it runs (its spoke's inner tools execute inside the call and must
    # see them); every tool's legs are recorded after a successful run.
    from prax.agent.trifecta import LEG_PRIVATE, LEG_UNTRUSTED, legs_for
    static_legs = legs_for(tool_name, declared=getattr(meta_src, "_trifecta_legs", None))
    is_delegate = tool_name.startswith("delegate_")

    # Resolve capability metadata for epistemic tagging.
    capability = get_tool_capability(tool_name)
    reliability = (
        capability.get("reliability", SourceReliability.INFORMATIONAL)
        if capability
        else None
    )
    epistemic_note = capability.get("epistemic_note", "") if capability else ""

    def _governed_run(**kwargs: Any) -> Any:
        with use_user_context(bound_context):
            return _governed_run_bound(**kwargs)

    def _record_legs(state: TurnGovernanceState) -> None:
        if LEG_UNTRUSTED in static_legs:
            state.trifecta_untrusted = True
        if LEG_PRIVATE in static_legs:
            state.trifecta_private = True
            _taint_egress(state, f"{tool_name} read private data")

    def _governed_run_bound(**kwargs: Any) -> Any:
        state = current_turn_state()
        # Why this call is allowed to run, recorded on its audit entry. Local to
        # the call: parallel tool calls share the turn state.
        prov: dict[str, str] = {}
        # A stopped turn ends at its next tool call — hub or spoke. Raised,
        # not returned as text: text is a result the model can decide to
        # retry around, which is how a stop used to get ignored.
        turn = state.turn
        if turn is not None and turn.cancel.is_set():
            from prax.services.turn_registry import TurnCancelled
            raise TurnCancelled(turn.reason or "stopped")
        over = _over_budget(state)
        if over:
            return over

        # --- Hard floors (HARD_FLOORS_ENABLED) ---
        # Before earned trust, smart auto-approve, the turn-wide latch and the
        # spoke enforce switch: none of them may lower a floor. See hard_floors.
        from prax.agent import hard_floors
        exposure_decision = None
        if hard_floors.is_floor(tool_name, kwargs):
            floor_refusal = _floor_gate(state, tool_name, kwargs, prov)
            if floor_refusal:
                return floor_refusal
            if hard_floors.is_exposure(tool_name, kwargs):
                # The share registry accepts a public share only inside this
                # decision (prax/services/exposure_gate.py).
                exposure_decision = prov.get("approval") or "person"

        # --- Active Inference: extract expected observation (Phase 1) ---
        expected_observation = kwargs.pop("expected_observation", None)

        # --- Prediction tracker init (hub only) ---
        tracker = None
        if hub:
            try:
                from prax.agent.prediction_tracker import (
                    READ_TOOLS,
                    extract_resource_key,
                    get_prediction_tracker,
                )
                tracker = get_prediction_tracker()
            except Exception:
                pass  # tracker stays None — all tracker-dependent gates degrade gracefully

        # --- Epistemic ledger: read-before-write gate (Phase 2) ---
        if tracker is not None:
            try:
                gate_msg = tracker.check_epistemic_gate(tool_name, kwargs)
                if gate_msg:
                    logger.info("Epistemic gate blocked %s: %s", tool_name, gate_msg)
                    state.audit.append(log_action(
                        tool_name, static_risk, kwargs,
                        result=f"BLOCKED — epistemic gate ({gate_msg[:80]})", from_tool=False,
                    ))
                    return gate_msg
            except Exception:
                pass  # Gate failure must not disable tracker for later phases

        # --- Earned trust: dynamic risk adjustment ---
        risk = static_risk
        if risk is RiskLevel.HIGH:
            try:
                from prax.agent.earned_trust import get_trust_adjustments
                from prax.agent.user_context import current_component
                component = current_component.get("orchestrator")
                trust = get_trust_adjustments(component)
                if tool_name in trust.risk_downgrade_eligible:
                    risk = RiskLevel.MEDIUM
                    prov.setdefault("approval", "auto:earned_trust")
                    logger.debug(
                        "Earned trust: downgraded %s from HIGH to MEDIUM for %s",
                        tool_name, component,
                    )
            except Exception:
                pass

        # --- Lethal-trifecta guard (flag-gated, default off) ---
        # Once the turn has ingested untrusted content AND read private data, an
        # external-sink action is the exfiltration point of an indirect prompt
        # injection.  Gate it behind a DEDICATED confirmation latch — deliberately
        # NOT the shared HIGH-risk latch, so an unrelated confirmation earlier in
        # the turn cannot silently unlock the exfil action.
        if enforce:
            try:
                from prax.agent.trifecta import should_escalate_sink, trifecta_guard_enabled
                _tf_key = _trifecta_key(tool_name, kwargs)  # latch is bound to the ARGS too
                if (trifecta_guard_enabled()
                        and _tf_key not in state.trifecta_confirmed
                        and _tf_key not in state.human_approved
                        and should_escalate_sink(
                            tool_name, untrusted_seen=state.trifecta_untrusted,
                            private_seen=state.trifecta_private, legs=static_legs)):
                    from prax.agent import human_approval
                    if human_approval.enabled():
                        refusal = _ask_a_person(
                            state, tool_name, kwargs, _tf_key, kind="lethal_trifecta",
                            reason=("This turn read UNTRUSTED content and PRIVATE data, and "
                                    "this action sends or acts externally — the classic "
                                    "prompt-injection exfiltration point."), prov=prov)
                        if refusal:
                            return refusal
                    elif _tf_key not in state.trifecta_seen:
                        state.trifecta_seen.add(_tf_key)
                        state.audit.append(log_action(
                            tool_name, RiskLevel.HIGH, kwargs,
                            result="BLOCKED — lethal-trifecta confirmation required", from_tool=False))
                        logger.info("Lethal-trifecta: blocked external-sink %s pending "
                                    "confirmation (turn touched untrusted + private)", tool_name)
                        return (
                            f"⚠️ Lethal-trifecta guard: this turn has read UNTRUSTED "
                            f"content AND PRIVATE data, and {tool_name} sends/acts "
                            f"externally — the classic prompt-injection exfiltration "
                            f"point. Confirm with the user this is intended, then call "
                            f"{tool_name} again with the same arguments to proceed."
                        )
                    else:
                        # 2nd call with the SAME arguments = confirmed (different args re-block)
                        state.trifecta_confirmed.add(_tf_key)
                        prov["approval"] = "model_reconfirmed"
            except Exception:
                pass  # Guard must never break the tool path.

        # --- Budget tracking (hub only) ---
        if hub and state.tool_call_budget > 0:
            state.tool_call_count += 1
            if state.tool_call_count > state.tool_call_budget and tool_name != "request_extended_budget":
                state.audit.append(log_action(
                    tool_name, risk, kwargs,
                    result="BLOCKED — tool call budget exhausted", from_tool=False,
                ))
                try:
                    from prax.services.health_telemetry import EventCategory, Severity, record_event
                    record_event(
                        EventCategory.BUDGET_EXHAUSTED, Severity.WARNING,
                        component="governed_tool",
                        details=f"Budget exhausted at {state.tool_call_budget} calls (tool: {tool_name})",
                    )
                except Exception:
                    pass
                return (
                    f"Tool call budget exhausted ({state.tool_call_budget} calls used). "
                    f"Use request_extended_budget(reason, additional_calls) to "
                    f"request more calls if the task genuinely requires it."
                )

        # --- Loop detection (hub only) ---
        if hub:
            try:
                from prax.agent.loop_detector import check as _loop_check
                loop_msg = _loop_check(tool_name, kwargs)
                if loop_msg:
                    state.audit.append(log_action(
                        tool_name, risk, kwargs,
                        result=f"LOOP — {loop_msg[:80]}", from_tool=False,
                    ))
                    return loop_msg
            except Exception:
                pass

        # --- HIGH-risk gate with smart auto-approve ---
        # When ``high_risk_scoped_confirm`` is on, a user confirmation unlocks
        # ONLY the specific tool that was confirmed; otherwise every HIGH-risk
        # tool for the turn (the default, broader behaviour).  Smart
        # auto-approve is per-tool in BOTH modes.
        if enforce:
            scoped = _scoped_confirm()
            already_confirmed = (
                tool_name in state.high_risk_confirmed_tools
                or (not scoped and state.high_risk_confirmed)
            )
            _call_key = _trifecta_key(tool_name, kwargs)
            if risk is RiskLevel.HIGH and not already_confirmed and _call_key not in state.human_approved:
                from prax.agent import human_approval
                # Smart confirmation: if the user explicitly requested THIS
                # browser interaction (e.g. "click the login button"),
                # auto-approve this tool only — never the turn-wide latch.
                if _user_explicitly_requested_action(tool_name):
                    state.high_risk_confirmed_tools.add(tool_name)
                    prov["approval"] = "auto:user_request"
                    logger.info(
                        "Smart auto-approve: %s (user explicitly requested action)",
                        tool_name,
                    )
                elif human_approval.enabled():
                    # Out of band: a person answers in TeamWork, the model cannot.
                    refusal = _ask_a_person(
                        state, tool_name, kwargs, _call_key, kind="high_risk",
                        reason=f"{tool_name} is classified HIGH risk.", prov=prov)
                    if refusal:
                        return refusal
                elif tool_name not in state.high_risk_seen:
                    state.high_risk_seen.add(tool_name)
                    state.audit.append(log_action(
                        tool_name, risk, kwargs, result="BLOCKED — awaiting confirmation", from_tool=False,
                    ))
                    logger.info(
                        "HIGH-risk tool %s blocked pending confirmation (args=%s)",
                        tool_name, _summarize_args(kwargs),
                    )
                    return (
                        f"⚠️ This action ({tool_name}) is classified as HIGH risk. "
                        f"Please confirm with the user before proceeding. "
                        f"To execute, call {tool_name} again with the same arguments."
                    )
                elif scoped:
                    # User confirmed — unlock ONLY this tool for the turn.
                    state.high_risk_confirmed_tools.add(tool_name)
                    prov["approval"] = "model_reconfirmed"
                else:
                    # User confirmed — unlock all HIGH-risk tools for this turn.
                    state.high_risk_confirmed = True
                    prov["approval"] = "model_reconfirmed"
            elif risk is RiskLevel.HIGH:
                prov.setdefault("approval", "person" if _call_key in state.human_approved
                                else "earlier_confirmation")

        # --- Semantic entropy gate (Phase 4, hub only) ---
        if hub and risk is RiskLevel.HIGH:
            try:
                from prax.agent.semantic_entropy import check_semantic_entropy
                entropy_warning = check_semantic_entropy(tool_name, kwargs)
                if entropy_warning:
                    logger.warning("Semantic entropy blocked %s: %s", tool_name, entropy_warning)
                    try:
                        from prax.observability.metrics import HALLUCINATION_GUARD
                        HALLUCINATION_GUARD.labels(type="semantic_entropy").inc()
                    except Exception:
                        pass
                    state.audit.append(log_action(
                        tool_name, risk, kwargs,
                        result=f"BLOCKED — semantic entropy ({entropy_warning[:80]})", from_tool=False,
                    ))
                    return entropy_warning
            except Exception:
                pass  # Graceful fallback — don't block on gate failure.

        # --- Lethal-trifecta: a delegate's STATIC legs are recorded before it
        # runs, so the spoke's inner (governed) tools see them. ---
        if is_delegate:
            _record_legs(state)

        # Code about to run in the sandbox can read /workspace — the user's
        # data — within this very call, so the egress gate is tainted BEFORE
        # it runs, not after (a `cat … | curl …` is one call).
        if tool_name in _SANDBOX_EXEC_TOOLS:
            _taint_egress(state, f"{tool_name} ran code over the workspace")

        # Execute the tool.
        logger.info("Tool %s starting [%s] (args=%s)", tool_name, risk.value, _summarize_args(kwargs))
        if hub:
            from prax.services.teamwork_hooks import set_role_status
            set_role_status("Executor", "working")
            if risk is RiskLevel.HIGH:
                set_role_status("Auditor", "working")
        try:
            if exposure_decision:
                from prax.services.exposure_gate import person_decided
                with person_decided(exposure_decision):
                    result = tool.invoke(kwargs if kwargs else {})
            else:
                result = tool.invoke(kwargs if kwargs else {})
            result_str = str(result) if result is not None else None
            # A credential tool's secret values are masked by every sink for
            # the rest of the turn — including when the model passes them on
            # as another tool's argument (browser_login -> browser_fill).
            if tool_name in CREDENTIAL_TOOLS:
                from prax.agent.message_text import tool_output_text
                from prax.agent.turn_secrets import register_from_output
                register_from_output(tool_name, tool_output_text(result))
            state.audit.append(log_action(
                tool_name, risk, kwargs, result=result_str,
                approval=prov.get("approval") or (
                    "high_risk_not_enforced" if risk is RiskLevel.HIGH and not enforce
                    else "none_needed")))
            logger.info("Tool %s finished [%s]", tool_name, risk.value)

            # --- Lethal-trifecta: record which legs this turn has now touched ---
            # A tool can touch several legs (the browser both reads untrusted
            # pages AND acts).  Recorded unconditionally — it is two booleans;
            # only the escalation is flag-gated.
            _record_legs(state)

            if not hub:
                # Spoke layer: the result reaches the spoke's LLM exactly as the
                # raw tool returned it.
                return result

            # --- Active Inference: record prediction error (Phase 1) ---
            if expected_observation and tracker:
                try:
                    tracker.record_prediction(
                        tool_name, expected_observation, result_str or "",
                    )
                except Exception:
                    pass

            # --- Epistemic ledger: record reads (Phase 2) ---
            if tracker and tool_name in READ_TOOLS:
                try:
                    resource = extract_resource_key(tool_name, kwargs)
                    if resource:
                        tracker.record_read(resource)
                except Exception:
                    pass

            # --- Logprob entropy check (Phase 3) ---
            if tracker and risk in (RiskLevel.MEDIUM, RiskLevel.HIGH):
                try:
                    from prax.agent.logprob_analyzer import get_entropy_for_tool
                    entropy = get_entropy_for_tool(tool_name)
                    if entropy and entropy.is_uncertain:
                        logger.warning(
                            "Logprob entropy HIGH for %s: score=%.3f "
                            "tokens=%s",
                            tool_name, entropy.entropy_score,
                            entropy.high_entropy_tokens[:5],
                        )
                except Exception:
                    pass  # Graceful fallback — logprobs not available.

            # Epistemic tagging: prepend source-reliability metadata so the
            # LLM knows how much to trust this result for factual claims.
            # A tool can override its static tier tag per-result by returning
            # a SourcedResult with its own accurate tag (e.g. fetch_url_content
            # for a post served by the platform's native API: exact provenance,
            # but in-post claims stay unverified).  The override is a code-set
            # attribute, so fetched content cannot spoof it in-band.
            if isinstance(result, SourcedResult) and result.epistemic_tag:
                result = f"{result.epistemic_tag}\n\n{str(result)}"
            elif reliability is not None and result is not None:
                result = _tag_result(result, reliability, epistemic_note)

            # The tool error rate's denominator.  Without it the window held
            # only TOOL_ERROR events and every rate read 0% or 100%.  Counted
            # last, so a raise in the tagging above counts once, as an error.
            # Hub only, like TOOL_ERROR: a delegate's inner spoke tools are not
            # counted, so a delegation is one call, not one plus its steps.
            # A count, not an event: at tool-call rate, events evicted the
            # rare alerts from the telemetry store's cap.
            try:
                from prax.services.health_telemetry import count_tool_success
                count_tool_success()
            except Exception:
                pass

            return result
        except Exception as exc:
            # A credential tool's exception text is its own output: the type
            # is recorded, the message withheld (log_action's rule).
            error = (f"ERROR: {type(exc).__name__} ({WITHHELD_OUTPUT})"
                     if tool_name in CREDENTIAL_TOOLS else f"ERROR: {exc}")
            state.audit.append(log_action(
                tool_name, risk, kwargs, result=error, from_tool=False,
            ))
            if hub:
                try:
                    from prax.services.health_telemetry import EventCategory, Severity, record_event
                    record_event(
                        EventCategory.TOOL_ERROR, Severity.WARNING,
                        component=tool_name,
                        details=f"{type(exc).__name__}: {str(exc)[:200]}",
                    )
                except Exception:
                    pass
            raise

    # Augment the tool's args schema with expected_observation so the
    # LLM can (optionally) declare its prediction for each tool call.
    # Hub only: a spoke's tool surface must not change with governance.
    args_schema = _augment_schema(tool_name, tool.args_schema) if hub else tool.args_schema

    governed = StructuredTool.from_function(
        func=_governed_run,
        name=tool_name,
        description=tool.description,
        args_schema=args_schema,
    )
    # Carry the classification forward so a wrapper stacked on top of this one
    # (or a registry test) sees what governance decided.
    governed._risk_level = static_risk
    governed._trifecta_legs = static_legs
    return governed


def govern_spoke_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Bind request context into a spoke's tool list, then wrap it with
    spoke-layer governance.

    Replaces the bare ``bind_tools_user_context(tools)`` call at every spoke
    build site.  Governance goes on AFTER the binding wrapper; the raw tool is
    passed as ``classify_as`` because the binding wrapper does not carry the
    raw tool's ``_risk_level`` / ``_trifecta_legs`` metadata.  Enforcement
    follows ``SPOKE_GOVERNANCE_ENABLED`` (default off: record only).
    """
    from prax.agent.user_context import bind_tools_user_context
    enforce = spoke_governance_enforced()
    bound = bind_tools_user_context(tools)
    return [
        wrap_with_governance(b, layer="spoke", enforce=enforce, classify_as=raw)
        for raw, b in zip(tools, bound, strict=True)
    ]


def _augment_schema(tool_name: str, original_schema):
    """Add ``expected_observation`` to a tool's Pydantic args schema.

    Returns the augmented schema, or the original if augmentation fails.
    The field is optional (default ``None``) so existing tool calls
    without it continue to work.
    """
    if original_schema is None:
        return None
    try:
        from pydantic import Field, create_model
        return create_model(
            f"{tool_name}_Governed",
            __base__=original_schema,
            expected_observation=(
                str | None,
                Field(
                    None,
                    description=(
                        "Optional: your brief prediction of what this tool "
                        "call will return (e.g. 'file will be saved successfully', "
                        "'tests will pass'). Used for uncertainty measurement."
                    ),
                ),
            ),
        )
    except Exception:
        logger.debug("Schema augmentation failed for %s", tool_name, exc_info=True)
        return original_schema


_RELIABILITY_TAGS: dict[SourceReliability, str] = {
    SourceReliability.INFORMATIONAL: (
        "[INFORMATIONAL SOURCE — general web content, not structured data. "
        "Do NOT state specific numbers, prices, statistics, rankings, or "
        "quantities from this result as verified facts. "
        "Use only for background context and general understanding.]"
    ),
    SourceReliability.INDICATIVE: (
        "[INDICATIVE SOURCE — data may be approximate or stale. "
        "If citing specific values, label them as approximate and name the source URL.]"
    ),
    SourceReliability.VERIFIED: (
        "[VERIFIED SOURCE — structured data from a purpose-built API. "
        "Values can be cited directly with source attribution.]"
    ),
}


def _tag_result(
    result: Any,
    reliability: SourceReliability,
    epistemic_note: str = "",
) -> Any:
    """Prepend epistemic metadata to a tool result string.

    Only tags string results; non-string results pass through unchanged.
    """
    if not isinstance(result, str):
        return result
    tag = _RELIABILITY_TAGS.get(reliability, "")
    if epistemic_note:
        tag = f"{tag}\n{epistemic_note}" if tag else epistemic_note
    if tag:
        return f"{tag}\n\n{result}"
    return result


# Tools that run code inside the sandbox, where /workspace is the user's data.
_SANDBOX_EXEC_TOOLS = frozenset({"sandbox_shell", "run_python", "data_query", "lean_check"})


# How many tool calls an over-budget turn may still attempt (each refused) to
# write its report before it is ended outright.
_BUDGET_GRACE_CALLS = 3


def _budget_state(state: TurnGovernanceState) -> str:
    """Why this turn is over its budget, or "" (TURN_BUDGET_USD / _SECONDS)."""
    try:
        from prax.settings import settings
        max_usd = float(getattr(settings, "turn_budget_usd", 0) or 0)
        max_s = int(getattr(settings, "turn_budget_seconds", 0) or 0)
    except Exception:
        return ""
    if max_s > 0 and state.turn is not None:
        age = state.turn.age_seconds()
        if age >= max_s:
            return f"{int(age // 60)} min {int(age % 60)} s of a {max_s // 60} min {max_s % 60} s time budget"
    if max_usd > 0 and state.graph is not None:
        try:
            spent, complete = state.graph.cost_so_far()
        except Exception:
            return ""
        if spent >= max_usd:
            approx = "" if complete else " (at least — some model rates are unknown)"
            return f"${spent:.2f}{approx} of a ${max_usd:.2f} cost budget"
    return ""


def _over_budget(state: TurnGovernanceState) -> str:
    """Refuse the call when the turn is over budget; end it if it keeps going.

    Past the budget the agent is not cut off mid-thought: its next few tool
    calls are refused with an instruction to stop and report, so the person
    gets what was done and what is left. A turn that keeps calling tools
    anyway is ended (TurnBudgetExceeded). Either way the person decides
    whether to continue — a new message starts a fresh budget.
    """
    reason = _budget_state(state)
    if not reason:
        return ""
    state.budget_refusals += 1
    if state.budget_refusals > _BUDGET_GRACE_CALLS:
        from prax.services.turn_registry import TurnBudgetExceeded
        raise TurnBudgetExceeded(reason)
    return (
        f"BUDGET REACHED — not run. This request has used {reason}. Make no more "
        "tool calls. Reply to the user now: what you did, what is still left, "
        "what you spent, and ask whether they want you to continue (a new "
        "message gets a fresh budget)."
    )


def _taint_egress(state: TurnGovernanceState, reason: str) -> None:
    """Tell the sandbox egress gate this turn has touched private data."""
    if state.egress_tainted:
        return
    state.egress_tainted = True
    try:
        from prax.services import egress_gate_service
        egress_gate_service.mark_tainted(id(state), reason)
    except Exception:
        pass  # the gate is a second line; never break the tool path


def _floor_gate(state: TurnGovernanceState, tool_name: str, kwargs: dict,
                prov: dict | None = None) -> str | None:
    """Run a hard-floor action only on a person's decision about this exact call.

    ``None`` = go ahead. A person's out-of-band approval counts unless a timed
    grant gave it; with approvals off, the user's own message must name the
    action and its target. Each call is decided on its own.
    """
    from prax.agent import hard_floors, human_approval

    call_key = _trifecta_key(tool_name, kwargs)
    target = hard_floors._target(kwargs)
    prov = prov if prov is not None else {}
    if call_key in state.human_approved:
        prov["approval"] = "person"
        return None
    if human_approval.enabled():
        state.audit.append(log_action(
            tool_name, RiskLevel.HIGH, kwargs, result="PAUSED — hard floor, awaiting a person", from_tool=False))
        decision = human_approval.request(
            tool_name, kwargs, kind="hard_floor",
            reason=hard_floors.approval_reason(tool_name, target),
            summary=_summarize_args(kwargs, max_len=600))
        if decision.approved and not decision.decided_by.startswith("grant:"):
            state.human_approved.add(call_key)
            state.audit.append(log_action(
                tool_name, RiskLevel.HIGH, kwargs,
                result=f"APPROVED by a person — hard floor (approval {decision.approval_id})", from_tool=False))
            prov["approval"] = f"person:{decision.approval_id}"
            return None
        why = ("a timed grant can't approve a hard-floor action"
               if decision.approved else decision.message[:120])
        state.audit.append(log_action(
            tool_name, RiskLevel.HIGH, kwargs, result=f"REFUSED — hard floor: {why}", from_tool=False))
        if decision.message.startswith("PARKED"):
            return decision.message  # the model must say the task is waiting, not refused
        return hard_floors.refusal(tool_name, target, approvals=True)
    if hard_floors.user_named_it(tool_name, kwargs, _attended_user_message()):
        state.human_approved.add(call_key)
        state.audit.append(log_action(
            tool_name, RiskLevel.HIGH, kwargs,
            result="APPROVED by the user's own message — hard floor", from_tool=False))
        prov["approval"] = "user_message"
        return None
    state.audit.append(log_action(
        tool_name, RiskLevel.HIGH, kwargs, result="REFUSED — hard floor, no person's decision", from_tool=False))
    return hard_floors.refusal(tool_name, target, approvals=False)


def _ask_a_person(state: TurnGovernanceState, tool_name: str, kwargs: dict,
                  call_key: str, *, kind: str, reason: str,
                  prov: dict | None = None) -> str | None:
    """Block on an out-of-band approval. ``None`` = approved, go ahead;
    otherwise the refusal to return to the model instead of running the tool."""
    from prax.agent import human_approval

    state.audit.append(log_action(
        tool_name, RiskLevel.HIGH, kwargs,
        result=f"PAUSED — awaiting out-of-band approval ({kind})", from_tool=False))
    decision = human_approval.request(
        tool_name, kwargs, kind=kind, reason=reason,
        summary=_summarize_args(kwargs, max_len=600))
    if decision.approved:
        state.human_approved.add(call_key)
        state.audit.append(log_action(
            tool_name, RiskLevel.HIGH, kwargs,
            result=f"APPROVED by a person (approval {decision.approval_id})", from_tool=False))
        if prov is not None:
            prov["approval"] = f"person:{decision.approval_id}"
        return None
    state.audit.append(log_action(
        tool_name, RiskLevel.HIGH, kwargs,
        result=f"REFUSED — {decision.message[:120]}", from_tool=False))
    return f"⛔ {decision.message}"


def _summarize_args(args: dict, max_len: int = 120) -> str:
    """Compact string summary of tool args for logs and approval requests.

    Secret values a credential tool handed out this turn are masked
    (``turn_secrets.scrub``) before truncating, so no prefix of one survives.
    """
    from prax.agent.turn_secrets import scrub
    s = scrub(str(args))
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


# ---------------------------------------------------------------------------
# Backwards-compatible module attributes
# ---------------------------------------------------------------------------

class _GovernedToolModule(types.ModuleType):
    """Thin accessors for the old module-global names.

    The per-turn state used to be module globals (``_audit_buffer``,
    ``_high_risk_seen``, ``_tool_call_budget``, ...) and callers and tests
    still read AND assign them by name (``gov._tool_call_budget = 15``).
    These properties route every such access to the CURRENT context's turn
    state, so the old names keep working without the old process-wide
    sharing.  Nothing inside this module uses them.
    """


def _state_property(attr: str) -> property:
    def fget(self):
        return getattr(current_turn_state(), attr)

    def fset(self, value):
        setattr(current_turn_state(), attr, value)

    return property(fget, fset, doc=f"Alias for current_turn_state().{attr}")


for _alias, _attr in {
    "_audit_buffer": "audit",
    "_high_risk_seen": "high_risk_seen",
    "_high_risk_confirmed": "high_risk_confirmed",
    "_high_risk_confirmed_tools": "high_risk_confirmed_tools",
    "_trifecta_untrusted": "trifecta_untrusted",
    "_trifecta_private": "trifecta_private",
    "_trifecta_seen": "trifecta_seen",
    "_trifecta_confirmed": "trifecta_confirmed",
    "_tool_call_count": "tool_call_count",
    "_tool_call_budget": "tool_call_budget",
}.items():
    setattr(_GovernedToolModule, _alias, _state_property(_attr))

# Customising module attribute access by swapping the module's class is the
# documented mechanism (Data model → "Customizing module attribute access").
sys.modules[__name__].__class__ = _GovernedToolModule
