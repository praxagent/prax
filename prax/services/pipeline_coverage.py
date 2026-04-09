"""Pipeline coverage instrumentation — Phase 0 of the pipeline evolution roadmap.

Captures every orchestrator turn with: the user's request, which spoke matched
(or whether it fell through to the generic fallback), the outcome status, and
the latency. Used to produce a Pareto chart of coverage gaps so we know where
the existing spoke library actually fails before building any L1 escape hatch.

Storage: append-only JSONL in the workspace directory, mirroring the pattern
established in ``health_telemetry.py``. Bounded in-memory ring buffer + disk
persistence with periodic pruning.

See: docs/PIPELINE_EVOLUTION_TODO.md and docs/research/pipeline-composition.md
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


@dataclass
class CoverageEvent:
    """A single orchestrator turn captured for coverage analysis."""
    timestamp: float
    user_id: str = ""
    request: str = ""
    matched_spoke: str = ""           # e.g. "browser", "knowledge", "fallback", "direct"
    delegations: list[str] = field(default_factory=list)
    outcome_status: str = "completed"  # completed | failed | timeout
    tool_call_count: int = 0
    duration_ms: float = 0
    embedding: list[float] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_AGE_DAYS = 30
_MAX_EVENTS_IN_MEMORY = 5000
_MAX_REQUEST_LENGTH = 500          # truncate stored requests
_DEFAULT_REPORT_LIMIT = 20         # top N clusters in reports

# Names that should NOT count as "real spoke matches" for fallback rate.
# These represent cases where the orchestrator handled the request directly
# without specialised delegation.
_FALLBACK_LABELS = frozenset({"fallback", "delegate_task", "generic", ""})

# Spoke labels we expect to see (used for spoke usage breakdown).
_KNOWN_SPOKES = frozenset({
    "browser", "content", "course", "finetune", "knowledge",
    "memory", "sandbox", "scheduler", "sysadmin", "workspace",
    "research", "professor",
})


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_events: list[dict] = []
_file_path: Path | None = None
_initialized = False

# Test mode — when enabled, all events are written to a separate file so
# harness/test data never pollutes real user telemetry. Toggled via
# set_test_mode() (callable from a remote endpoint).
_test_mode = False
_test_file_path: Path | None = None


def set_test_mode(enabled: bool, test_file: Path | None = None) -> None:
    """Enable/disable test mode.

    In test mode, all events are written to a separate file
    (``.pipeline_coverage_harness.jsonl`` by default) so test data is
    never mixed with real user data. The in-memory event buffer is
    cleared on every toggle so a freshly enabled test session starts
    from a clean slate, and disabling test mode also clears it so the
    next access reloads real events from disk.
    """
    global _test_mode, _test_file_path, _initialized
    with _lock:
        _test_mode = bool(enabled)
        if enabled:
            if test_file is None:
                from prax.settings import settings
                workspace = Path(settings.workspace_dir)
                workspace.mkdir(parents=True, exist_ok=True)
                test_file = workspace / ".pipeline_coverage_harness.jsonl"
            _test_file_path = Path(test_file)
            _test_file_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            _test_file_path = None
        _events.clear()
        _initialized = False


def is_test_mode() -> bool:
    """Return whether test mode is currently enabled."""
    return _test_mode


def _get_file_path() -> Path:
    """Return the active JSONL path — test file when in test mode."""
    global _file_path
    if _test_mode and _test_file_path is not None:
        return _test_file_path
    if _file_path is None:
        from prax.settings import settings
        workspace = Path(settings.workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        _file_path = workspace / ".pipeline_coverage.jsonl"
    return _file_path


def _init() -> None:
    """Load existing events from disk on first access."""
    global _initialized
    if _initialized:
        return
    _initialized = True
    path = _get_file_path()
    if not path.exists():
        return
    cutoff = time.time() - MAX_AGE_DAYS * 86_400
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                    if evt.get("timestamp", 0) >= cutoff:
                        _events.append(evt)
                except json.JSONDecodeError:
                    continue
        if len(_events) > _MAX_EVENTS_IN_MEMORY:
            _events[:] = _events[-_MAX_EVENTS_IN_MEMORY:]
    except Exception:
        logger.debug("Failed to load coverage events", exc_info=True)


def record_turn(
    *,
    user_id: str = "",
    request: str = "",
    matched_spoke: str = "",
    delegations: list[str] | None = None,
    outcome_status: str = "completed",
    tool_call_count: int = 0,
    duration_ms: float = 0,
    embedding: list[float] | None = None,
    extra: dict | None = None,
) -> None:
    """Record a single orchestrator turn for coverage analysis.

    No-op when ``HEALTH_MONITOR_ENABLED=false`` (we reuse that toggle since
    coverage is part of the same telemetry family).
    """
    try:
        from prax.settings import settings
        if not settings.health_monitor_enabled:
            return
    except Exception:
        pass

    truncated = request[:_MAX_REQUEST_LENGTH] if request else ""

    evt = CoverageEvent(
        timestamp=time.time(),
        user_id=user_id or "anonymous",
        request=truncated,
        matched_spoke=matched_spoke or "direct",
        delegations=list(delegations or []),
        outcome_status=outcome_status,
        tool_call_count=tool_call_count,
        duration_ms=duration_ms,
        embedding=list(embedding or []),
        extra=extra or {},
    )

    # In-memory row keeps the embedding for fast clustering this session.
    in_memory_row = asdict(evt)

    # On-disk row strips the embedding (~15KB per event) — embeddings can
    # be recomputed on demand from the request text at report time. This
    # keeps the disk file small (~500 bytes/event vs ~15KB/event).
    on_disk_row = {k: v for k, v in in_memory_row.items() if k != "embedding"}

    with _lock:
        _init()
        _events.append(in_memory_row)
        if len(_events) > _MAX_EVENTS_IN_MEMORY:
            _events[:] = _events[-_MAX_EVENTS_IN_MEMORY:]
        try:
            with open(_get_file_path(), "a") as f:
                f.write(json.dumps(on_disk_row, default=str) + "\n")
        except Exception:
            pass


def get_recent_events(days: int = 14, limit: int = 5000) -> list[dict]:
    """Return recent coverage events, newest first."""
    with _lock:
        _init()
        cutoff = time.time() - days * 86_400
        result = []
        for evt in reversed(_events):
            if evt.get("timestamp", 0) < cutoff:
                break
            result.append(evt)
            if len(result) >= limit:
                break
        return result


def prune_old_events() -> int:
    """Remove events older than MAX_AGE_DAYS. Returns count removed.

    Rewrites the on-disk JSONL file to drop the pruned entries. Strips
    embeddings on rewrite (they aren't persisted on disk anyway).
    """
    cutoff = time.time() - MAX_AGE_DAYS * 86_400
    with _lock:
        _init()
        before = len(_events)
        _events[:] = [e for e in _events if e.get("timestamp", 0) >= cutoff]
        removed = before - len(_events)
        if removed > 0:
            try:
                with open(_get_file_path(), "w") as f:
                    for evt in _events:
                        # Strip embeddings on disk write to match record_turn().
                        on_disk = {k: v for k, v in evt.items() if k != "embedding"}
                        f.write(json.dumps(on_disk, default=str) + "\n")
            except Exception:
                pass
        return removed


# Auto-prune state — keeps disk usage bounded without a separate scheduler.
_turns_since_prune = 0
_PRUNE_EVERY_N_TURNS = 100


def maybe_prune() -> int:
    """Run prune_old_events() once every N turns. Cheap no-op otherwise.

    Called by the orchestrator's turn-end hook so disk pruning happens
    naturally during normal operation, with no scheduler dependency.
    Returns the number of events pruned (or 0 if not yet time to prune).
    """
    global _turns_since_prune
    _turns_since_prune += 1
    if _turns_since_prune < _PRUNE_EVERY_N_TURNS:
        return 0
    _turns_since_prune = 0
    try:
        return prune_old_events()
    except Exception:
        logger.debug("Auto-prune failed", exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Embedding helper (best-effort)
# ---------------------------------------------------------------------------


def _embed_request(text: str) -> list[float]:
    """Best-effort embedding of a request. Returns empty list on failure.

    Uses the existing memory embedder so we don't pay for a separate
    embedding service. Failures are silent — coverage instrumentation
    must never block the main turn flow.
    """
    if not text:
        return []
    try:
        from prax.services.memory.embedder import embed_texts
        result = embed_texts([text[:_MAX_REQUEST_LENGTH]])
        if result and result[0]:
            return list(result[0])
    except Exception:
        logger.debug("Coverage embedding failed", exc_info=True)
    return []


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns 0 if either is empty."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def cluster_events(
    events: list[dict], similarity_threshold: float = 0.75,
) -> list[dict]:
    """Cluster events by request embedding similarity (greedy single-pass).

    Returns a list of cluster dicts, each with:
        - centroid: averaged embedding
        - members: list of event dicts in this cluster
        - sample_request: representative request text
        - count: number of events
        - fallback_count: how many events fell through to a generic handler
        - failure_count: how many events failed/timed out
        - matched_spokes: dict of spoke -> count
    """
    clusters: list[dict] = []

    for evt in events:
        emb = evt.get("embedding") or []
        if not emb:
            # Events without embeddings get their own cluster keyed by spoke,
            # so they still show up in reports.
            label = f"_no_embed:{evt.get('matched_spoke', 'unknown')}"
            existing = next(
                (c for c in clusters if c.get("_label") == label), None,
            )
            if existing:
                existing["members"].append(evt)
            else:
                clusters.append({
                    "_label": label,
                    "centroid": [],
                    "members": [evt],
                    "sample_request": evt.get("request", ""),
                })
            continue

        # Find the most similar existing cluster.
        best_idx = -1
        best_sim = similarity_threshold
        for i, cluster in enumerate(clusters):
            if not cluster.get("centroid"):
                continue
            sim = _cosine_similarity(emb, cluster["centroid"])
            if sim > best_sim:
                best_sim = sim
                best_idx = i

        if best_idx >= 0:
            cluster = clusters[best_idx]
            cluster["members"].append(evt)
            # Update centroid as running mean.
            n = len(cluster["members"])
            cluster["centroid"] = [
                ((n - 1) * c + e) / n
                for c, e in zip(cluster["centroid"], emb, strict=False)
            ]
        else:
            clusters.append({
                "centroid": list(emb),
                "members": [evt],
                "sample_request": evt.get("request", ""),
            })

    # Compute cluster statistics.
    for cluster in clusters:
        members = cluster["members"]
        cluster["count"] = len(members)
        cluster["fallback_count"] = sum(
            1 for m in members
            if m.get("matched_spoke", "") in _FALLBACK_LABELS
        )
        cluster["failure_count"] = sum(
            1 for m in members
            if m.get("outcome_status", "") in ("failed", "timeout")
        )
        cluster["fallback_rate"] = (
            cluster["fallback_count"] / cluster["count"]
            if cluster["count"] else 0
        )
        cluster["failure_rate"] = (
            cluster["failure_count"] / cluster["count"]
            if cluster["count"] else 0
        )
        # Pick the longest sample request as representative.
        sorted_members = sorted(
            members, key=lambda m: len(m.get("request", "")), reverse=True,
        )
        cluster["sample_request"] = (
            sorted_members[0].get("request", "") if sorted_members else ""
        )
        # Spoke usage breakdown within the cluster.
        spoke_counts: dict[str, int] = {}
        for m in members:
            spoke = m.get("matched_spoke", "direct")
            spoke_counts[spoke] = spoke_counts.get(spoke, 0) + 1
        cluster["matched_spokes"] = spoke_counts
        # Don't return raw embeddings or full member lists in API responses
        cluster.pop("centroid", None)
        cluster.pop("_label", None)
        # Keep up to 5 sample requests for inspection
        cluster["sample_requests"] = [
            m.get("request", "")[:200] for m in members[:5]
        ]
        del cluster["members"]

    # Sort by count desc.
    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


# ---------------------------------------------------------------------------
# Pareto report
# ---------------------------------------------------------------------------


def _lazy_embed_events(events: list[dict]) -> None:
    """Re-compute embeddings for events that don't have one (loaded from disk).

    Embeddings are stripped on disk write to keep the file small. We
    recompute them lazily at report time so clustering still works after
    a process restart. The result is cached on the in-memory event dict
    so subsequent reports don't pay the cost.

    Best-effort: if embedding fails for any reason, we leave the event
    without an embedding and the clusterer falls back to spoke-based
    grouping for that event.
    """
    needs_embedding = [e for e in events if not e.get("embedding") and e.get("request")]
    if not needs_embedding:
        return
    try:
        from prax.services.memory.embedder import embed_texts
        texts = [e["request"][:_MAX_REQUEST_LENGTH] for e in needs_embedding]
        # Batch the embedding call — much cheaper than per-event.
        vectors = embed_texts(texts)
        for evt, vec in zip(needs_embedding, vectors, strict=False):
            if vec:
                evt["embedding"] = list(vec)
    except Exception:
        logger.debug("Lazy embedding failed", exc_info=True)


def get_coverage_report(days: int = 14, top_n: int = _DEFAULT_REPORT_LIMIT) -> dict:
    """Generate the Pareto coverage report.

    Returns a dict with:
        - total_turns
        - fallback_rate (overall)
        - failure_rate (overall)
        - clusters: top N clusters by event count, sorted by fallback rate desc
        - top_failures: turns where the matched spoke produced an error
        - coverage_by_spoke: usage frequency per spoke
        - decision_hint: a textual recommendation based on the rubric
    """
    events = get_recent_events(days=days)
    # Re-compute embeddings for events loaded from disk (which don't carry
    # embeddings). This is a one-time cost per process restart.
    _lazy_embed_events(events)
    total = len(events)

    if total == 0:
        return {
            "total_turns": 0,
            "fallback_rate": 0,
            "failure_rate": 0,
            "clusters": [],
            "top_failures": [],
            "coverage_by_spoke": {},
            "decision_hint": (
                "No coverage data yet. Use Prax for at least 2 weeks before "
                "making architectural decisions."
            ),
            "window_days": days,
        }

    fallback_count = sum(
        1 for e in events if e.get("matched_spoke", "") in _FALLBACK_LABELS
    )
    failure_count = sum(
        1 for e in events if e.get("outcome_status", "") in ("failed", "timeout")
    )
    fallback_rate = fallback_count / total
    failure_rate = failure_count / total

    # Cluster by intent.
    clusters = cluster_events(events)
    # Sort by fallback rate descending, then by count descending — surface
    # the highest-fallback-rate intent groups first.
    clusters_by_gap = sorted(
        clusters,
        key=lambda c: (c["fallback_rate"], c["count"]),
        reverse=True,
    )[:top_n]

    # Top failures (matched a spoke but failed)
    top_failures = []
    for evt in events:
        if (evt.get("outcome_status", "") in ("failed", "timeout")
                and evt.get("matched_spoke", "") not in _FALLBACK_LABELS):
            top_failures.append({
                "request": evt.get("request", "")[:200],
                "matched_spoke": evt.get("matched_spoke", ""),
                "outcome_status": evt.get("outcome_status", ""),
                "duration_ms": evt.get("duration_ms", 0),
                "timestamp": evt.get("timestamp", 0),
            })
            if len(top_failures) >= top_n:
                break

    # Coverage by spoke
    coverage_by_spoke: dict[str, int] = {}
    for evt in events:
        spoke = evt.get("matched_spoke", "direct")
        coverage_by_spoke[spoke] = coverage_by_spoke.get(spoke, 0) + 1

    # Decision hint
    decision_hint = _build_decision_hint(
        total, fallback_rate, failure_rate, clusters_by_gap,
    )

    return {
        "total_turns": total,
        "fallback_rate": round(fallback_rate, 4),
        "failure_rate": round(failure_rate, 4),
        "clusters": clusters_by_gap,
        "top_failures": top_failures,
        "coverage_by_spoke": coverage_by_spoke,
        "decision_hint": decision_hint,
        "window_days": days,
    }


def _build_decision_hint(
    total: int, fallback_rate: float, failure_rate: float,
    top_clusters: list[dict],
) -> str:
    """Apply the rubric from PIPELINE_EVOLUTION_TODO.md to the data."""
    if total < 50:
        return (
            f"Only {total} turns logged. Collect at least 100 turns over 2 weeks "
            "of real usage before making architectural decisions."
        )

    pct = fallback_rate * 100

    if fallback_rate < 0.05:
        msg = (
            f"Fallback rate is {pct:.1f}% — existing spokes cover the long tail. "
            "Stay at L0. Phase 1 is NOT justified. "
        )
        if failure_rate >= 0.10:
            msg += (
                f"However, failure rate is {failure_rate*100:.1f}% — focus on "
                "fixing quality bugs in matched spokes before considering Phase 1."
            )
        else:
            msg += "Add 1-2 new spokes for the highest-impact fallback clusters if any."
        return msg

    if fallback_rate < 0.15:
        # Check if concentrated.
        if top_clusters and len(top_clusters) <= 3:
            return (
                f"Fallback rate is {pct:.1f}% but concentrated in {len(top_clusters)} "
                "clusters. Cheaper to add specialised spokes for those clusters than "
                "to build the L1 escape hatch. Phase 1 is borderline — try spokes first."
            )
        return (
            f"Fallback rate is {pct:.1f}% — genuine coverage gap with scattered "
            "request shapes. Phase 1 (run_custom_pipeline with APE) is JUSTIFIED. "
            "Build it next."
        )

    if fallback_rate < 0.30:
        return (
            f"Fallback rate is {pct:.1f}% — significant coverage gap. "
            "Look at the top 3 fallback clusters: if all variations on 1-2 themes, "
            "add spokes. If scattered across many themes, build Phase 1."
        )

    return (
        f"Fallback rate is {pct:.1f}% — STOP. The spoke abstraction itself may "
        "be wrong. Reconsider whether users are asking for things Prax is "
        "fundamentally not designed for."
    )
