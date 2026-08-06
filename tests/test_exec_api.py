"""The prax.execute(JobSpec) contract.

The point of the typed handoff is that a control plane never shells out and
Prax never guesses. These tests hold both halves of that: a malformed request
is refused *before* anything runs, and a refusal is never dressed up as a
degraded success.
"""
import json
import subprocess
import uuid

import pytest

from prax.exec_api import JobSpecError, execute, validate_job_spec


def _spec(**over):
    spec = {
        "schema_version": "1.0",
        "job_id": str(uuid.uuid4()),
        "experiment_id": str(uuid.uuid4()),
        "plan_rev": "1",
        "target": {"kind": "local"},
        "workdir": "/work",
        "command": ["python", "run.py"],
        "resources": {"cpu": "2", "memory": "2Gi", "gpu": 0, "timeout": "10m"},
    }
    spec.update(over)
    return spec


class FakeClient:
    """Stands in for the sandbox, matching how the real one actually behaves.

    Two shapes matter and both were learned from live runs:
      * `run_command` returns `subprocess.CompletedProcess`, not a dict.
      * Artifacts are collected by running `tar czf - . | base64` in the
        sandbox, so the fake answers that command with a real tar stream —
        an earlier fake served the file API instead, which is a channel the
        collector no longer uses.
    """

    def __init__(self, *, healthy=True, exit_code=0, files=None, raises=None,
                 mkdir_rc=0):
        self._healthy, self._exit = healthy, exit_code
        self._files = files or {}
        self._raises = raises
        self._mkdir_rc = mkdir_rc
        self.calls = []

    def health(self):
        return self._healthy

    def _tar_b64(self) -> str:
        import base64
        import io
        import tarfile

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name, data in self._files.items():
                info = tarfile.TarInfo(name=f"./{name}")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        return base64.b64encode(buf.getvalue()).decode()

    def run_command(self, cmd, cwd=None, env=None, timeout=None):
        self.calls.append({"cmd": cmd, "cwd": cwd, "env": env, "timeout": timeout})
        joined = " ".join(cmd)
        if "mkdir -p" in joined:
            return subprocess.CompletedProcess(args=cmd, returncode=self._mkdir_rc,
                                               stdout="", stderr="")
        if "tar czf" in joined:                      # the collection channel
            return subprocess.CompletedProcess(args=cmd, returncode=0,
                                               stdout=self._tar_b64(), stderr="")
        if self._raises:
            raise self._raises
        return subprocess.CompletedProcess(args=cmd, returncode=self._exit,
                                           stdout="ran\n", stderr="")



def _job_call(client):
    """The call that ran the job itself — not the mkdir or the tar collection."""
    for c in client.calls:
        joined = " ".join(c["cmd"])
        if "mkdir -p" not in joined and "tar czf" not in joined:
            return c
    raise AssertionError("no job command was issued")


class TestValidationRefusesBeforeRunning:
    def test_valid_spec_passes(self):
        assert validate_job_spec(_spec())["_timeout_s"] == 600

    @pytest.mark.parametrize("field", ["job_id", "experiment_id", "target",
                                       "workdir", "command", "resources"])
    def test_missing_required_field_is_named(self, field):
        spec = _spec()
        del spec[field]
        with pytest.raises(JobSpecError, match=field):
            validate_job_spec(spec)

    def test_wrong_schema_version_is_refused(self):
        with pytest.raises(JobSpecError, match="schema_version"):
            validate_job_spec(_spec(schema_version="2.0"))

    def test_non_uuid_job_id_is_refused(self):
        with pytest.raises(JobSpecError, match="job_id must be a UUID"):
            validate_job_spec(_spec(job_id="job-1"))

    def test_empty_command_is_refused(self):
        with pytest.raises(JobSpecError, match="command"):
            validate_job_spec(_spec(command=[]))

    def test_unpinned_input_hash_is_refused(self):
        """An unpinned input makes the run unreproducible."""
        spec = _spec(inputs=[{"uri": "hf://m", "content_hash": "latest",
                              "mount_path": "/inputs/m", "mode": "ro"}])
        with pytest.raises(JobSpecError, match="content_hash"):
            validate_job_spec(spec)

    def test_unimplemented_target_says_so_rather_than_pretending(self):
        """A caller must be able to tell 'not supported' from 'ran and failed'."""
        with pytest.raises(JobSpecError, match="not implemented"):
            validate_job_spec(_spec(target={"kind": "managed-k8s"}))

    def test_gpu_request_is_refused_not_silently_run_on_cpu(self):
        spec = _spec(resources={"cpu": "2", "memory": "2Gi", "gpu": 1})
        with pytest.raises(JobSpecError, match="no GPU scheduling"):
            validate_job_spec(spec)

    def test_bad_timeout_format_is_refused(self):
        spec = _spec(resources={"cpu": "2", "memory": "2Gi", "gpu": 0, "timeout": "ages"})
        with pytest.raises(JobSpecError, match="timeout"):
            validate_job_spec(spec)

    @pytest.mark.parametrize("value,seconds", [("30s", 30), ("10m", 600), ("2h", 7200)])
    def test_timeout_units(self, value, seconds):
        spec = _spec(resources={"cpu": "1", "memory": "1Gi", "gpu": 0, "timeout": value})
        assert validate_job_spec(spec)["_timeout_s"] == seconds


class TestRefusalIsNeverASilentFallback:
    def test_missing_sandbox_refuses_rather_than_running_on_the_host(self, monkeypatch):
        """The bug this whole module exists to prevent."""
        import sys
        from types import ModuleType

        stub = ModuleType("prax_sandbox_client")

        def no_client():
            raise ImportError("prax_sandbox_client not installed")

        stub.get_client = no_client
        monkeypatch.setitem(sys.modules, "prax_sandbox_client", stub)

        r = execute(_spec())
        assert r["exit_code"] == -1
        assert "refusing to run on the host" in r["error"]
        assert r["artifacts"] == []
        assert r["timeline_events"][-1]["type"] == "job_failed"

    def test_unhealthy_sandbox_refuses(self):
        r = execute(_spec(), client=FakeClient(healthy=False))
        assert "refusing to run on the host" in r["error"]

    def test_execution_crash_is_a_result_not_an_exception(self):
        r = execute(_spec(), client=FakeClient(raises=RuntimeError("boom")))
        assert r["exit_code"] == -1
        assert "execution failed" in r["error"]
        assert r["timeline_events"]  # the caller still gets a timeline

    def test_rw_input_is_refused_because_the_hash_would_be_a_lie(self):
        spec = _spec(inputs=[{
            "uri": "s3://b/k", "content_hash": "sha256:" + "a" * 64,
            "mount_path": "/inputs/x", "mode": "rw"}])
        r = execute(spec, client=FakeClient())
        assert "read-only" in r["error"]


class TestSuccessfulRun:
    def test_command_and_workdir_reach_the_sandbox(self):
        c = FakeClient()
        execute(_spec(), client=c)
        job = _job_call(c)
        assert job["cmd"] == ["python", "run.py"]
        assert job["cwd"] == "/work"
        assert job["timeout"] == 600

    def test_egress_allowlist_is_passed_through_as_declared_intent(self):
        c = FakeClient()
        execute(_spec(egress_allowlist=["huggingface.co"]), client=c)
        assert _job_call(c)["env"]["PRAX_EGRESS_ALLOWLIST"] == "huggingface.co"

    def test_artifacts_are_hashed_and_classified(self):
        c = FakeClient(files={"results.csv": b"a,b\n1,2\n", "run.log": b"hello"})
        r = execute(_spec(), client=c)
        by_kind = {a["kind"]: a for a in r["artifacts"]}
        assert by_kind["metric_csv"]["content_hash"].startswith("sha256:")
        assert len(by_kind["metric_csv"]["content_hash"]) == len("sha256:") + 64
        assert by_kind["log"]["byte_size"] == 5

    def test_s3_key_is_scoped_to_experiment_and_job(self):
        spec = _spec()
        c = FakeClient(files={"out.csv": b"x"})
        r = execute(spec, client=c)
        assert r["artifacts"][0]["s3_key"] == (
            f"{spec['experiment_id']}/{spec['job_id']}/out.csv")

    def test_identical_bytes_hash_identically(self):
        a = execute(_spec(), client=FakeClient(files={"x.log": b"same"}))
        b = execute(_spec(), client=FakeClient(files={"x.log": b"same"}))
        assert a["artifacts"][0]["content_hash"] == b["artifacts"][0]["content_hash"]

    def test_metrics_json_is_surfaced(self):
        c = FakeClient(files={"metrics.json": json.dumps({"accuracy": 0.91}).encode()})
        r = execute(_spec(), client=c)
        assert r["metrics"] == {"accuracy": 0.91}

    def test_malformed_metrics_yields_empty_not_invented_numbers(self):
        c = FakeClient(files={"metrics.json": b"{not json"})
        assert execute(_spec(), client=c)["metrics"] == {}

    def test_nonzero_exit_is_reported_as_failure(self):
        r = execute(_spec(), client=FakeClient(exit_code=3))
        assert r["exit_code"] == 3
        assert "exited 3" in r["error"]
        assert r["timeline_events"][-1]["type"] == "job_failed"


class TestTimeline:
    def test_seq_is_monotonic_from_one(self):
        r = execute(_spec(), client=FakeClient(files={"a.log": b"x"}))
        seqs = [e["seq"] for e in r["timeline_events"]]
        assert seqs == list(range(1, len(seqs) + 1))

    def test_lifecycle_types_are_from_the_contract(self):
        r = execute(_spec(), client=FakeClient(files={"a.csv": b"x"}))
        allowed = {"job_queued", "job_running", "log_chunk", "artifact_registered",
                   "metric_snapshot", "decision_required", "decision_answered",
                   "job_succeeded", "job_failed", "experiment_paused",
                   "experiment_stopped"}
        assert {e["type"] for e in r["timeline_events"]} <= allowed

    def test_every_event_carries_the_experiment_id_cursor_fields(self):
        spec = _spec()
        r = execute(spec, client=FakeClient())
        for e in r["timeline_events"]:
            assert e["experiment_id"] == spec["experiment_id"]
            assert e["job_id"] == spec["job_id"]
            assert e["created_at"]


class TestTopLevelExport:
    def test_execute_is_importable_from_prax(self):
        import prax
        assert callable(prax.execute)

    def test_import_prax_does_not_pull_in_the_agent_stack(self):
        """`import prax` must stay cheap — the lazy export is the point."""
        import subprocess
        import sys
        code = ("import prax, sys; "
                "assert 'langchain' not in sys.modules, 'agent stack imported'; "
                "print('ok')")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, timeout=120)
        assert out.returncode == 0, out.stderr[-500:]


class TestResultShapeMatchesProduction:
    """The sandbox returns CompletedProcess; injected doubles may return a dict."""

    def test_completed_process_is_read_correctly(self):
        r = execute(_spec(), client=FakeClient(exit_code=0))
        assert r["exit_code"] == 0
        assert r["error"] is None

    def test_nonzero_completed_process(self):
        assert execute(_spec(), client=FakeClient(exit_code=7))["exit_code"] == 7

    def test_dict_shape_still_works_for_test_doubles(self):
        class DictClient(FakeClient):
            def run_command(self, cmd, cwd=None, env=None, timeout=None):
                joined = " ".join(cmd)
                if "mkdir -p" in joined:
                    return {"exit_code": 0, "stdout": ""}
                if "tar czf" in joined:
                    return {"exit_code": 0, "stdout": self._tar_b64()}
                return {"exit_code": 5, "stdout": "x"}

        assert execute(_spec(), client=DictClient())["exit_code"] == 5


class TestArtifactCollectionIsHonest:
    """'Produced nothing' and 'we could not look' are different results."""

    def test_collection_failure_is_reported_not_swallowed(self):
        class BrokenFS(FakeClient):
            def run_command(self, cmd, cwd=None, env=None, timeout=None):
                if "tar czf" in " ".join(cmd):
                    return subprocess.CompletedProcess(args=cmd, returncode=2,
                                                       stdout="", stderr="no such dir")
                return super().run_command(cmd, cwd=cwd, env=env, timeout=timeout)

        r = execute(_spec(), client=BrokenFS())
        assert r["artifacts"] == []
        assert "could not be read back" in r["artifact_collection_error"]
        # The job itself still succeeded — that must not be overwritten.
        assert r["exit_code"] == 0

    def test_clean_run_reports_no_collection_error(self):
        r = execute(_spec(), client=FakeClient(files={"a.log": b"x"}))
        assert r["artifact_collection_error"] is None

    def test_collection_reads_the_declared_workdir(self):
        """Collection must target the job's own workdir, not a shared root."""
        c = FakeClient(files={"a.log": b"x"})
        execute(_spec(workdir="/work/job-7"), client=c)
        tar_call = [x for x in c.calls if "tar czf" in " ".join(x["cmd"])][0]
        assert "/work/job-7" in " ".join(tar_call["cmd"])


class TestWorkdirIsProvisioned:
    """The schema calls workdir 'a scoped write dir' — Prax creates it.

    Without this the first command fails with a bare exit 2 (chdir into a
    missing directory), which reads like the job's fault and is actually ours.
    Found by a live run.
    """

    def test_workdir_is_created_before_the_command(self):
        c = FakeClient()
        execute(_spec(workdir="/work/job-1"), client=c)
        assert "mkdir -p" in " ".join(c.calls[0]["cmd"])
        assert "/work/job-1" in " ".join(c.calls[0]["cmd"])

    def test_failure_to_create_workdir_is_reported_not_run_anyway(self):
        class NoMkdir(FakeClient):
            def run_command(self, cmd, cwd=None, env=None, timeout=None):
                self.calls.append({"cmd": cmd, "cwd": cwd, "env": env,
                                   "timeout": timeout})
                return subprocess.CompletedProcess(args=cmd, returncode=1,
                                                   stdout="", stderr="denied")

        r = execute(_spec(), client=NoMkdir())
        assert "could not create workdir" in r["error"]
        assert r["exit_code"] == -1
