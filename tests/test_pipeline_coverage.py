"""Tests for pipeline coverage instrumentation (Phase 0)."""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from prax.services import pipeline_coverage


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    """Reset module-level state and use a temp directory between tests."""
    pipeline_coverage._events.clear()
    pipeline_coverage._initialized = False
    pipeline_coverage._file_path = tmp_path / ".pipeline_coverage.jsonl"
    pipeline_coverage._test_mode = False
    pipeline_coverage._test_file_path = None
    yield
    pipeline_coverage._events.clear()
    pipeline_coverage._initialized = False
    pipeline_coverage._file_path = None
    pipeline_coverage._test_mode = False
    pipeline_coverage._test_file_path = None


def _embed(text: str, dim: int = 8, seed_offset: int = 0) -> list[float]:
    """Deterministic synthetic embedding for testing — same text → same vector."""
    h = hash(text + str(seed_offset))
    return [((h >> (i * 4)) & 0xF) / 16.0 for i in range(dim)]


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


class TestRecording:
    def test_record_creates_event(self):
        pipeline_coverage.record_turn(
            user_id="alice",
            request="Make me a note about gradient descent",
            matched_spoke="knowledge",
            outcome_status="completed",
            tool_call_count=3,
            duration_ms=1234,
        )
        events = pipeline_coverage.get_recent_events(days=14)
        assert len(events) == 1
        assert events[0]["matched_spoke"] == "knowledge"
        assert events[0]["request"] == "Make me a note about gradient descent"
        assert events[0]["tool_call_count"] == 3

    def test_record_truncates_long_request(self):
        long_request = "x" * 10_000
        pipeline_coverage.record_turn(
            user_id="alice", request=long_request, matched_spoke="direct",
        )
        events = pipeline_coverage.get_recent_events()
        assert len(events[0]["request"]) <= pipeline_coverage._MAX_REQUEST_LENGTH

    def test_record_persists_to_disk(self):
        pipeline_coverage.record_turn(
            user_id="alice", request="test", matched_spoke="direct",
        )
        path = pipeline_coverage._get_file_path()
        assert path.exists()
        with open(path) as f:
            lines = [line for line in f if line.strip()]
        assert len(lines) == 1

    def test_record_skipped_when_health_monitor_disabled(self):
        with patch("prax.settings.settings") as mock_settings:
            mock_settings.health_monitor_enabled = False
            pipeline_coverage.record_turn(
                user_id="alice", request="test", matched_spoke="direct",
            )
            assert pipeline_coverage.get_recent_events() == []

    def test_default_matched_spoke_is_direct(self):
        pipeline_coverage.record_turn(user_id="alice", request="test")
        events = pipeline_coverage.get_recent_events()
        assert events[0]["matched_spoke"] == "direct"

    def test_user_id_defaults_to_anonymous(self):
        pipeline_coverage.record_turn(request="test", matched_spoke="direct")
        events = pipeline_coverage.get_recent_events()
        assert events[0]["user_id"] == "anonymous"


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


class TestQuerying:
    def test_get_recent_events_newest_first(self):
        pipeline_coverage.record_turn(request="oldest", matched_spoke="direct")
        time.sleep(0.01)
        pipeline_coverage.record_turn(request="middle", matched_spoke="direct")
        time.sleep(0.01)
        pipeline_coverage.record_turn(request="newest", matched_spoke="direct")
        events = pipeline_coverage.get_recent_events()
        assert events[0]["request"] == "newest"
        assert events[2]["request"] == "oldest"

    def test_get_recent_events_respects_limit(self):
        for i in range(20):
            pipeline_coverage.record_turn(request=f"req {i}", matched_spoke="direct")
        events = pipeline_coverage.get_recent_events(limit=5)
        assert len(events) == 5

    def test_get_recent_events_excludes_old(self):
        # Inject an old event directly into _events
        pipeline_coverage._events.append({
            "timestamp": time.time() - 100 * 86_400,  # 100 days ago
            "user_id": "alice",
            "request": "ancient",
            "matched_spoke": "direct",
            "outcome_status": "completed",
            "tool_call_count": 0,
            "duration_ms": 0,
            "delegations": [],
            "embedding": [],
            "extra": {},
        })
        pipeline_coverage.record_turn(request="recent", matched_spoke="direct")
        events = pipeline_coverage.get_recent_events(days=14)
        assert all(e["request"] != "ancient" for e in events)
        assert any(e["request"] == "recent" for e in events)


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------


class TestPruning:
    def test_prune_removes_old_events(self):
        pipeline_coverage._events.append({
            "timestamp": time.time() - 100 * 86_400,
            "request": "ancient",
            "matched_spoke": "direct",
        })
        pipeline_coverage.record_turn(request="recent", matched_spoke="direct")
        removed = pipeline_coverage.prune_old_events()
        assert removed == 1
        events = pipeline_coverage._events
        assert all(e.get("request") != "ancient" for e in events)


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert pipeline_coverage._cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert pipeline_coverage._cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert pipeline_coverage._cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_empty_vectors(self):
        assert pipeline_coverage._cosine_similarity([], [1.0]) == 0.0
        assert pipeline_coverage._cosine_similarity([1.0], []) == 0.0

    def test_mismatched_dimensions(self):
        assert pipeline_coverage._cosine_similarity([1.0, 2.0], [1.0]) == 0.0


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


class TestClustering:
    def test_similar_requests_cluster_together(self):
        # Three "knowledge" requests (similar embeddings) and three "browser"
        # requests (different embeddings).
        events = []
        knowledge_emb = [1.0, 0.0, 0.0]
        browser_emb = [0.0, 1.0, 0.0]
        for i in range(3):
            events.append({
                "timestamp": time.time(),
                "request": f"knowledge request {i}",
                "matched_spoke": "knowledge",
                "outcome_status": "completed",
                "embedding": [x + 0.01 * i for x in knowledge_emb],
            })
            events.append({
                "timestamp": time.time(),
                "request": f"browser request {i}",
                "matched_spoke": "browser",
                "outcome_status": "completed",
                "embedding": [x + 0.01 * i for x in browser_emb],
            })
        clusters = pipeline_coverage.cluster_events(events)
        assert len(clusters) == 2

    def test_cluster_stats(self):
        events = [
            {
                "timestamp": time.time(),
                "request": "question 1",
                "matched_spoke": "fallback",
                "outcome_status": "completed",
                "embedding": [1.0, 0.0],
            },
            {
                "timestamp": time.time(),
                "request": "question 2",
                "matched_spoke": "knowledge",
                "outcome_status": "completed",
                "embedding": [1.0, 0.0],
            },
            {
                "timestamp": time.time(),
                "request": "question 3",
                "matched_spoke": "fallback",
                "outcome_status": "failed",
                "embedding": [1.0, 0.0],
            },
        ]
        clusters = pipeline_coverage.cluster_events(events)
        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster["count"] == 3
        assert cluster["fallback_count"] == 2
        assert cluster["failure_count"] == 1
        assert cluster["fallback_rate"] == pytest.approx(2 / 3)
        assert "matched_spokes" in cluster
        assert cluster["matched_spokes"]["fallback"] == 2
        assert cluster["matched_spokes"]["knowledge"] == 1

    def test_events_without_embeddings_grouped_by_spoke(self):
        events = [
            {
                "timestamp": time.time(),
                "request": "no embedding 1",
                "matched_spoke": "fallback",
                "outcome_status": "completed",
                "embedding": [],
            },
            {
                "timestamp": time.time(),
                "request": "no embedding 2",
                "matched_spoke": "fallback",
                "outcome_status": "completed",
                "embedding": [],
            },
            {
                "timestamp": time.time(),
                "request": "no embedding browser",
                "matched_spoke": "browser",
                "outcome_status": "completed",
                "embedding": [],
            },
        ]
        clusters = pipeline_coverage.cluster_events(events)
        # Two clusters: one for fallback (2 events), one for browser (1 event)
        assert len(clusters) == 2

    def test_empty_events(self):
        assert pipeline_coverage.cluster_events([]) == []


# ---------------------------------------------------------------------------
# Coverage report
# ---------------------------------------------------------------------------


class TestCoverageReport:
    def test_empty_report(self):
        report = pipeline_coverage.get_coverage_report()
        assert report["total_turns"] == 0
        assert report["fallback_rate"] == 0
        assert "Use Prax for at least" in report["decision_hint"]

    def test_low_fallback_rate_recommends_no_phase_1(self):
        # 50 turns, 95% match a spoke, 5% fallback
        for i in range(48):
            pipeline_coverage.record_turn(
                request=f"covered request {i}",
                matched_spoke="knowledge",
                outcome_status="completed",
            )
        for i in range(2):
            pipeline_coverage.record_turn(
                request=f"uncovered {i}",
                matched_spoke="fallback",
                outcome_status="completed",
            )
        report = pipeline_coverage.get_coverage_report()
        assert report["total_turns"] == 50
        assert report["fallback_rate"] < 0.05
        # Decision hint should reflect "stay at L0"
        # but only after we have enough data — 50 is the threshold.
        assert "Phase 1" in report["decision_hint"]

    def test_insufficient_data_warning(self):
        for i in range(10):
            pipeline_coverage.record_turn(
                request=f"req {i}", matched_spoke="direct",
            )
        report = pipeline_coverage.get_coverage_report()
        assert "Only 10 turns" in report["decision_hint"]

    def test_medium_fallback_rate_recommends_phase_1(self):
        # 100 turns, 12% fallback rate scattered across many requests
        for i in range(88):
            pipeline_coverage.record_turn(
                request=f"covered {i}",
                matched_spoke="knowledge",
                outcome_status="completed",
                embedding=[float(i % 5), 0.0, 0.0],  # 5 distinct clusters
            )
        for i in range(12):
            # Make the fallbacks have varied embeddings (scattered)
            pipeline_coverage.record_turn(
                request=f"uncovered request {i} with very different content",
                matched_spoke="fallback",
                outcome_status="completed",
                embedding=[float(i), float(i * 2), float(i * 3)],
            )
        report = pipeline_coverage.get_coverage_report()
        assert 0.05 <= report["fallback_rate"] < 0.15
        assert "Phase 1" in report["decision_hint"] or "spokes" in report["decision_hint"]

    def test_high_fallback_rate_warns(self):
        for i in range(35):
            pipeline_coverage.record_turn(
                request=f"covered {i}", matched_spoke="knowledge",
            )
        for i in range(15):
            pipeline_coverage.record_turn(
                request=f"weird request {i}", matched_spoke="fallback",
                embedding=[float(i), 0.0],
            )
        report = pipeline_coverage.get_coverage_report()
        assert report["fallback_rate"] >= 0.15

    def test_extreme_fallback_rate_says_stop(self):
        for i in range(20):
            pipeline_coverage.record_turn(
                request=f"covered {i}", matched_spoke="knowledge",
            )
        for i in range(40):
            pipeline_coverage.record_turn(
                request=f"weird {i}", matched_spoke="fallback",
                embedding=[float(i), 0.0],
            )
        report = pipeline_coverage.get_coverage_report()
        assert report["fallback_rate"] > 0.30
        assert "STOP" in report["decision_hint"]

    def test_top_failures_excludes_fallback(self):
        pipeline_coverage.record_turn(
            request="failed knowledge",
            matched_spoke="knowledge",
            outcome_status="failed",
        )
        pipeline_coverage.record_turn(
            request="failed fallback",
            matched_spoke="fallback",
            outcome_status="failed",
        )
        report = pipeline_coverage.get_coverage_report()
        # top_failures only includes failed turns where a real spoke matched
        failures = report["top_failures"]
        assert len(failures) == 1
        assert failures[0]["matched_spoke"] == "knowledge"

    def test_coverage_by_spoke(self):
        pipeline_coverage.record_turn(request="r1", matched_spoke="knowledge")
        pipeline_coverage.record_turn(request="r2", matched_spoke="knowledge")
        pipeline_coverage.record_turn(request="r3", matched_spoke="browser")
        report = pipeline_coverage.get_coverage_report()
        assert report["coverage_by_spoke"]["knowledge"] == 2
        assert report["coverage_by_spoke"]["browser"] == 1

    def test_concentrated_fallbacks_recommends_spokes(self):
        # 100 turns, 12% fallback BUT all in one tight cluster
        for i in range(88):
            pipeline_coverage.record_turn(
                request=f"covered {i}", matched_spoke="knowledge",
                embedding=[1.0, 0.0],
            )
        # All fallbacks have very similar embeddings → 1 cluster
        for i in range(12):
            pipeline_coverage.record_turn(
                request=f"slide deck request {i}",
                matched_spoke="fallback",
                outcome_status="completed",
                embedding=[0.0, 1.0],  # all the same
            )
        report = pipeline_coverage.get_coverage_report()
        # Should hint toward adding spokes since concentrated
        # (concentrated means top clusters list is short)
        fallback_clusters = [
            c for c in report["clusters"]
            if c["fallback_count"] > 0
        ]
        assert len(fallback_clusters) >= 1


# ---------------------------------------------------------------------------
# End-to-end realistic scenarios
# ---------------------------------------------------------------------------


class TestRealisticScenarios:
    """Synthetic scenarios that mirror real Prax usage patterns."""

    def test_mostly_knowledge_with_a_few_misses(self):
        """A user who mostly asks for notes — 90% knowledge, 10% misses."""
        # 90 knowledge requests with similar embeddings
        for i in range(90):
            pipeline_coverage.record_turn(
                user_id="alice",
                request=f"Save note about topic {i}",
                matched_spoke="knowledge",
                outcome_status="completed",
                tool_call_count=2,
                duration_ms=2000,
                embedding=[1.0, 0.0, 0.0, 0.1 * (i % 3)],
            )
        # 10 fallback requests with varied embeddings
        for i in range(10):
            pipeline_coverage.record_turn(
                user_id="alice",
                request=f"Random other request {i}",
                matched_spoke="fallback",
                outcome_status="completed",
                tool_call_count=5,
                duration_ms=8000,
                embedding=[0.0, 1.0, float(i) / 10, float(i) / 5],
            )

        report = pipeline_coverage.get_coverage_report()
        assert report["total_turns"] == 100
        assert report["fallback_rate"] == pytest.approx(0.10, abs=0.01)
        assert report["coverage_by_spoke"]["knowledge"] == 90
        assert report["coverage_by_spoke"]["fallback"] == 10
        # Should recommend Phase 1 (10% scattered) or spokes (10% concentrated)
        assert ("Phase 1" in report["decision_hint"]
                or "spokes" in report["decision_hint"])

    def test_diverse_usage_well_covered(self):
        """A user with varied requests that all hit appropriate spokes."""
        spokes = ["knowledge", "browser", "research", "content", "scheduler"]
        for i in range(60):
            pipeline_coverage.record_turn(
                user_id="alice",
                request=f"request {i}",
                matched_spoke=spokes[i % 5],
                outcome_status="completed",
                tool_call_count=3,
                duration_ms=1500,
                embedding=[float(i % 5), 0.0, 0.0],
            )

        report = pipeline_coverage.get_coverage_report()
        assert report["total_turns"] == 60
        assert report["fallback_rate"] == 0
        assert "Phase 1 is NOT justified" in report["decision_hint"] or "L0" in report["decision_hint"]

    def test_restart_robustness_loads_from_disk(self, tmp_path, monkeypatch):
        """After a 'restart' (clearing in-memory state), events reload from disk."""
        path = tmp_path / ".pipeline_coverage.jsonl"
        pipeline_coverage._file_path = path

        # Record some events
        pipeline_coverage.record_turn(
            user_id="alice", request="first turn", matched_spoke="knowledge",
            embedding=[1.0, 0.0],
        )
        pipeline_coverage.record_turn(
            user_id="alice", request="second turn", matched_spoke="browser",
            embedding=[0.0, 1.0],
        )
        assert len(pipeline_coverage._events) == 2

        # Verify the file was written and embeddings are STRIPPED on disk
        with open(path) as f:
            disk_events = [json.loads(line) for line in f if line.strip()]
        assert len(disk_events) == 2
        for evt in disk_events:
            assert "embedding" not in evt  # stripped to save space

        # Simulate a restart: clear in-memory state, reset _initialized
        pipeline_coverage._events.clear()
        pipeline_coverage._initialized = False

        # First access after "restart" should trigger _init() which loads from disk
        events = pipeline_coverage.get_recent_events()
        assert len(events) == 2
        # Events loaded from disk should NOT have embeddings yet
        assert all(not e.get("embedding") for e in events)

    def test_lazy_re_embedding_at_report_time(self, tmp_path):
        """Events without embeddings get re-embedded when generating a report."""
        path = tmp_path / ".pipeline_coverage.jsonl"
        pipeline_coverage._file_path = path

        # Record events WITHOUT embeddings (simulating post-restart load)
        pipeline_coverage._events.append({
            "timestamp": time.time(),
            "user_id": "alice",
            "request": "Make me a note about gradient descent",
            "matched_spoke": "knowledge",
            "outcome_status": "completed",
            "tool_call_count": 2,
            "duration_ms": 1500,
            "delegations": [],
            "embedding": [],
            "extra": {},
        })
        pipeline_coverage._events.append({
            "timestamp": time.time(),
            "user_id": "alice",
            "request": "Make me a note about backpropagation",
            "matched_spoke": "knowledge",
            "outcome_status": "completed",
            "tool_call_count": 2,
            "duration_ms": 1500,
            "delegations": [],
            "embedding": [],
            "extra": {},
        })

        # Stub out the embedder to avoid hitting a real API
        with patch("prax.services.memory.embedder.embed_texts") as mock_embed:
            mock_embed.return_value = [[1.0, 0.0], [1.0, 0.1]]
            events = pipeline_coverage.get_recent_events()
            pipeline_coverage._lazy_embed_events(events)

            # Both events should now have embeddings
            assert all(e.get("embedding") for e in events)
            mock_embed.assert_called_once()

    def test_partial_disk_lines_are_skipped(self, tmp_path):
        """Corrupt/partial JSON lines (from a process killed mid-write) are skipped."""
        path = tmp_path / ".pipeline_coverage.jsonl"
        # Write a mix of valid and corrupt lines
        valid_event = {
            "timestamp": time.time(),
            "user_id": "alice",
            "request": "valid",
            "matched_spoke": "knowledge",
            "outcome_status": "completed",
            "tool_call_count": 0,
            "duration_ms": 0,
            "delegations": [],
            "extra": {},
        }
        with open(path, "w") as f:
            f.write(json.dumps(valid_event) + "\n")
            f.write("{ broken json\n")  # corrupt line
            f.write(json.dumps(valid_event) + "\n")
            f.write("{ another partial")  # no newline (process killed mid-write)

        pipeline_coverage._file_path = path
        pipeline_coverage._events.clear()
        pipeline_coverage._initialized = False

        events = pipeline_coverage.get_recent_events()
        # 2 valid, 2 corrupt → should load 2
        assert len(events) == 2

    def test_maybe_prune_no_op_until_threshold(self, tmp_path):
        """maybe_prune() is a no-op until N turns have passed."""
        path = tmp_path / ".pipeline_coverage.jsonl"
        pipeline_coverage._file_path = path
        # Reset the counter
        pipeline_coverage._turns_since_prune = 0

        for _ in range(99):
            assert pipeline_coverage.maybe_prune() == 0

        # Add an old event so prune has something to remove
        pipeline_coverage._events.append({
            "timestamp": time.time() - 100 * 86_400,
            "request": "ancient", "matched_spoke": "direct",
        })
        # 100th call should trigger pruning
        result = pipeline_coverage.maybe_prune()
        # The ancient event was removed
        assert result == 1

        # Counter resets — next 99 should be no-ops again
        for _ in range(99):
            assert pipeline_coverage.maybe_prune() == 0

    def test_disk_file_size_smaller_without_embeddings(self, tmp_path):
        """Verify that on-disk events are MUCH smaller than in-memory events."""
        path = tmp_path / ".pipeline_coverage.jsonl"
        pipeline_coverage._file_path = path

        # Record an event with a large 1536-dim embedding
        big_embedding = [0.123456789] * 1536
        pipeline_coverage.record_turn(
            user_id="alice",
            request="A test request",
            matched_spoke="knowledge",
            embedding=big_embedding,
        )

        # In-memory event should have the embedding
        assert len(pipeline_coverage._events[0]["embedding"]) == 1536

        # On-disk should NOT
        with open(path) as f:
            disk_event = json.loads(f.read().strip())
        assert "embedding" not in disk_event

        # Disk event should be tiny (< 1KB)
        disk_size = path.stat().st_size
        assert disk_size < 1024  # vs ~15KB with embedding included

    def test_quality_failures_get_flagged_separately(self):
        """Spokes are matched but produce errors — different problem from gaps."""
        for i in range(45):
            pipeline_coverage.record_turn(
                request=f"good {i}", matched_spoke="knowledge",
                outcome_status="completed",
            )
        for i in range(5):
            pipeline_coverage.record_turn(
                request=f"failed knowledge request {i}",
                matched_spoke="knowledge",
                outcome_status="failed",
            )

        report = pipeline_coverage.get_coverage_report()
        assert report["fallback_rate"] == 0
        assert report["failure_rate"] == 0.10
        assert len(report["top_failures"]) == 5
        # Decision should focus on fixing existing spokes, not adding new ones
        # (only triggered when fallback rate is also low)
        assert "fixing" in report["decision_hint"] or "quality" in report["decision_hint"]


# ---------------------------------------------------------------------------
# Test mode (harness isolation)
# ---------------------------------------------------------------------------


class TestTestMode:
    """Test mode routes coverage events to a SEPARATE file so harness data
    never contaminates real user telemetry."""

    def test_set_test_mode_uses_separate_file(self, tmp_path):
        real_path = tmp_path / ".pipeline_coverage.jsonl"
        test_path = tmp_path / ".pipeline_coverage_harness.jsonl"
        pipeline_coverage._file_path = real_path

        # Record one event in normal mode
        pipeline_coverage.record_turn(
            request="real user request",
            matched_spoke="knowledge",
        )
        assert real_path.exists()
        with open(real_path) as f:
            assert len([line for line in f if line.strip()]) == 1

        # Enable test mode
        pipeline_coverage.set_test_mode(True, test_file=test_path)
        assert pipeline_coverage.is_test_mode() is True

        # Record an event — should land in the test file, NOT the real file
        pipeline_coverage.record_turn(
            request="harness request",
            matched_spoke="fallback",
        )
        assert test_path.exists()
        with open(test_path) as f:
            test_lines = [line for line in f if line.strip()]
        assert len(test_lines) == 1
        assert "harness request" in test_lines[0]

        # The real file should still have only the original event
        with open(real_path) as f:
            real_lines = [line for line in f if line.strip()]
        assert len(real_lines) == 1
        assert "real user request" in real_lines[0]

    def test_disabling_test_mode_routes_back_to_real_file(self, tmp_path):
        real_path = tmp_path / ".pipeline_coverage.jsonl"
        test_path = tmp_path / ".pipeline_coverage_harness.jsonl"
        pipeline_coverage._file_path = real_path

        pipeline_coverage.set_test_mode(True, test_file=test_path)
        pipeline_coverage.record_turn(request="harness", matched_spoke="fallback")

        pipeline_coverage.set_test_mode(False)
        assert pipeline_coverage.is_test_mode() is False

        pipeline_coverage.record_turn(request="real", matched_spoke="knowledge")

        with open(real_path) as f:
            real_lines = [line for line in f if line.strip()]
        with open(test_path) as f:
            test_lines = [line for line in f if line.strip()]

        # Real file got the post-disable event; test file kept only the harness event
        assert any("real" in line for line in real_lines)
        assert all("harness" not in line for line in real_lines)
        assert any("harness" in line for line in test_lines)

    def test_set_test_mode_clears_in_memory_buffer(self, tmp_path):
        pipeline_coverage._file_path = tmp_path / ".pipeline_coverage.jsonl"
        pipeline_coverage.record_turn(request="r1", matched_spoke="knowledge")
        pipeline_coverage.record_turn(request="r2", matched_spoke="knowledge")
        assert len(pipeline_coverage._events) == 2

        pipeline_coverage.set_test_mode(
            True, test_file=tmp_path / ".pipeline_coverage_harness.jsonl",
        )
        # Buffer cleared on toggle so harness starts from a clean slate
        assert pipeline_coverage._events == []

    def test_test_mode_get_recent_events_only_sees_test_data(self, tmp_path):
        real_path = tmp_path / ".pipeline_coverage.jsonl"
        test_path = tmp_path / ".pipeline_coverage_harness.jsonl"
        pipeline_coverage._file_path = real_path

        pipeline_coverage.record_turn(request="real", matched_spoke="knowledge")

        pipeline_coverage.set_test_mode(True, test_file=test_path)
        pipeline_coverage.record_turn(request="harness 1", matched_spoke="fallback")
        pipeline_coverage.record_turn(request="harness 2", matched_spoke="fallback")

        events = pipeline_coverage.get_recent_events()
        # Only the harness events should be visible while test mode is on
        assert len(events) == 2
        requests = {e["request"] for e in events}
        assert requests == {"harness 1", "harness 2"}

    def test_default_test_file_lives_in_workspace(self, tmp_path, monkeypatch):
        # Force settings.workspace_dir to point at tmp_path
        from prax.settings import settings
        monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))

        pipeline_coverage.set_test_mode(True)  # no explicit file
        assert pipeline_coverage._test_file_path is not None
        assert pipeline_coverage._test_file_path.name == ".pipeline_coverage_harness.jsonl"
        assert pipeline_coverage._test_file_path.parent == tmp_path
