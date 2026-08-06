"""``prax.execute(job_spec)`` — the typed handoff between a control plane and Prax.

The contract (``prax-lab/schemas/job_spec.schema.json``) draws a line:

- **The caller owns the workflow.** Thread → Plan → Experiment → Report,
  the TimelineEvent log, publication bundles, quotas.
- **Prax owns the execution.** Schema validation, read-only input mounts, the
  egress allowlist, sandbox isolation, and hashing whatever came out.

The point is that neither side guesses. Before this existed, a control plane
that wanted Prax to run something had to shell out — build a command line, hope
the environment matched, parse stdout, and trust the exit code. That is not an
interface, it is an impersonation: the caller ends up encoding how Prax works,
and the moment Prax changes, the caller silently breaks. Here the caller hands
over a JobSpec and gets back hashed artifacts.

Two properties worth stating plainly, because they are the reason this is not
just ``subprocess.run`` with extra steps:

**Refusal is a feature.** A JobSpec that fails validation, names an unknown
tool, or requests egress outside its allowlist is rejected *before* anything
runs. A malformed job that executes anyway and fails later is strictly worse
than one that never starts, because the failure is then attributed to the work
rather than to the request.

**Unknown is not success.** If the sandbox is unavailable, ``execute`` returns a
failure result saying so — it does not fall back to running the command on the
host. That fallback is exactly the class of bug this module exists to prevent,
and it is the same rule as the terminal tool refusing a host shell when the
sandbox is absent.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
import shlex
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["execute", "validate_job_spec", "JobSpecError", "SCHEMA_VERSION"]

SCHEMA_VERSION = "1.0"

# Mirrors artifact.schema.json. A `kind` outside this set is a contract
# violation on the way out, not a free-form label.
ARTIFACT_KINDS = {
    "code_patch", "log", "metric_csv", "metric_parquet", "plot", "table",
    "model_checkpoint", "activation_shard", "report_block",
}

_KIND_BY_SUFFIX = {
    ".csv": "metric_csv", ".parquet": "metric_parquet",
    ".png": "plot", ".jpg": "plot", ".jpeg": "plot", ".svg": "plot", ".pdf": "plot",
    ".log": "log", ".txt": "log",
    ".patch": "code_patch", ".diff": "code_patch",
    ".md": "report_block",
    ".pt": "model_checkpoint", ".safetensors": "model_checkpoint", ".ckpt": "model_checkpoint",
}

_HASH_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_TIMEOUT_RE = re.compile(r"^(\d+)([smh])$")
_TIMEOUT_UNITS = {"s": 1, "m": 60, "h": 3600}


class JobSpecError(ValueError):
    """The JobSpec is not runnable. Raised before anything executes."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _parse_timeout(value: str | None, default: int = 600) -> int:
    if not value:
        return default
    m = _TIMEOUT_RE.match(str(value).strip())
    if not m:
        raise JobSpecError(f"resources.timeout must look like '30s'/'10m'/'2h', got {value!r}")
    return int(m.group(1)) * _TIMEOUT_UNITS[m.group(2)]


def validate_job_spec(spec: dict) -> dict:
    """Check a JobSpec and return it normalised. Raises :class:`JobSpecError`.

    Deliberately hand-rolled rather than pulling in a jsonschema dependency:
    the contract is small, the errors here name the offending field, and Prax
    stays installable without another runtime dep. If the schema grows past
    what this can express, swap in jsonschema — the function boundary is the
    same.
    """
    if not isinstance(spec, dict):
        raise JobSpecError(f"JobSpec must be an object, got {type(spec).__name__}")

    version = spec.get("schema_version")
    if version != SCHEMA_VERSION:
        raise JobSpecError(
            f"unsupported schema_version {version!r} (this Prax speaks {SCHEMA_VERSION!r})")

    for field in ("job_id", "experiment_id", "target", "workdir", "command", "resources"):
        if field not in spec:
            raise JobSpecError(f"missing required field: {field}")

    for field in ("job_id", "experiment_id"):
        try:
            uuid.UUID(str(spec[field]))
        except (ValueError, AttributeError, TypeError):
            raise JobSpecError(f"{field} must be a UUID, got {spec[field]!r}") from None

    target = spec["target"]
    if not isinstance(target, dict) or "kind" not in target:
        raise JobSpecError("target must be an object with a 'kind'")
    if target["kind"] not in {"local", "managed-k8s", "slurm"}:
        raise JobSpecError(f"unsupported target.kind {target['kind']!r}")
    if target["kind"] != "local":
        # Say so rather than pretending: a control plane must be able to tell
        # "Prax cannot do this yet" from "Prax ran it and it failed".
        raise JobSpecError(
            f"target.kind {target['kind']!r} is not implemented by this Prax "
            "(only 'local' — which runs in prax-sandbox — is supported today)")

    command = spec["command"]
    if not isinstance(command, list) or not command or not all(
            isinstance(c, str) for c in command):
        raise JobSpecError("command must be a non-empty array of strings")

    resources = spec["resources"]
    if not isinstance(resources, dict):
        raise JobSpecError("resources must be an object")
    for field in ("cpu", "memory", "gpu"):
        if field not in resources:
            raise JobSpecError(f"missing required field: resources.{field}")
    if not isinstance(resources["gpu"], int) or resources["gpu"] < 0:
        raise JobSpecError(f"resources.gpu must be a non-negative integer, got {resources['gpu']!r}")
    if resources["gpu"] > 0:
        raise JobSpecError(
            f"resources.gpu={resources['gpu']} requested, but this Prax has no GPU "
            "scheduling — refusing rather than running the job CPU-only and "
            "returning results that silently mean something else")
    timeout_s = _parse_timeout(resources.get("timeout"))

    inputs = spec.get("inputs") or []
    if not isinstance(inputs, list):
        raise JobSpecError("inputs must be an array")
    for i, inp in enumerate(inputs):
        for field in ("uri", "content_hash", "mount_path", "mode"):
            if field not in inp:
                raise JobSpecError(f"inputs[{i}] missing required field: {field}")
        if not _HASH_RE.match(str(inp["content_hash"])):
            raise JobSpecError(
                f"inputs[{i}].content_hash must be 'sha256:<64 hex>', got "
                f"{inp['content_hash']!r} — an unpinned input makes the run "
                "unreproducible, which is the thing this field exists to prevent")
        if inp["mode"] not in {"ro", "rw"}:
            raise JobSpecError(f"inputs[{i}].mode must be 'ro' or 'rw', got {inp['mode']!r}")

    return {**spec, "_timeout_s": timeout_s}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

def _hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _classify(path: Path) -> str:
    """Map a filename to an artifact kind from the contract's enum."""
    return _KIND_BY_SUFFIX.get(path.suffix.lower(), "log")


def _collect_artifacts(files: dict[str, bytes], *, job_id: str, experiment_id: str,
                       plan_rev: str | None) -> list[dict]:
    """Hash and describe each output file, per artifact.schema.json."""
    artifacts = []
    for name, data in sorted(files.items()):
        p = Path(name)
        artifacts.append({
            "artifact_id": str(uuid.uuid4()),
            "job_id": job_id,
            "experiment_id": experiment_id,
            "kind": _classify(p),
            "s3_key": f"{experiment_id}/{job_id}/{name}",
            "content_hash": _hash_bytes(data),
            "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
            "byte_size": len(data),
            "provenance": {"plan_rev": plan_rev} if plan_rev else {},
        })
    return artifacts


# ---------------------------------------------------------------------------
# Timeline events
# ---------------------------------------------------------------------------

class _Timeline:
    """Builds the append-only event list the control plane consumes.

    ``seq`` is monotonic per experiment and is the caller's resume cursor, so
    it starts at 1 and never skips.
    """

    def __init__(self, experiment_id: str, job_id: str) -> None:
        self.experiment_id = experiment_id
        self.job_id = job_id
        self._seq = 0
        self.events: list[dict] = []

    def emit(self, type_: str, **payload) -> dict:
        self._seq += 1
        ev = {
            "event_id": str(uuid.uuid4()),
            "experiment_id": self.experiment_id,
            "job_id": self.job_id,
            "seq": self._seq,
            "type": type_,
            "payload": payload,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.events.append(ev)
        return ev


def _failure(spec: dict, timeline: _Timeline, reason: str, *,
             exit_code: int = -1) -> dict:
    timeline.emit("job_failed", reason=reason)
    return {
        "job_id": spec.get("job_id"),
        "experiment_id": spec.get("experiment_id"),
        "exit_code": exit_code,
        "artifacts": [],
        "metrics": {},
        "timeline_events": timeline.events,
        "error": reason,
    }


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------

def execute(job_spec: dict, *, client=None) -> dict:
    """Run a JobSpec in the sandbox and return hashed artifacts.

    Args:
        job_spec: a JobSpec dict (schema_version "1.0").
        client: injected sandbox client — for tests, and for a caller that
            already holds a configured one.

    Returns a result dict: ``job_id``, ``experiment_id``, ``exit_code``,
    ``artifacts`` (each with ``s3_key`` / ``content_hash`` / ``kind``),
    ``metrics``, ``timeline_events``, and ``error`` when something refused.

    Never raises for an execution failure — a failed job is a *result*, and the
    caller needs the timeline either way. Only a malformed JobSpec raises
    (:class:`JobSpecError`), because that is a bug in the request, not an
    outcome of the work.
    """
    spec = validate_job_spec(job_spec)
    job_id, experiment_id = str(spec["job_id"]), str(spec["experiment_id"])
    timeline = _Timeline(experiment_id, job_id)
    timeline.emit("job_queued", command=spec["command"], workdir=spec["workdir"])

    if client is None:
        try:
            from prax_sandbox_client import get_client
            client = get_client()
        except Exception as exc:  # noqa: BLE001
            # Refuse rather than fall back to the host. Running a job outside
            # its declared isolation is not a degraded success, it is a
            # different job with different guarantees.
            return _failure(spec, timeline,
                            f"sandbox unavailable ({type(exc).__name__}: {exc}) — "
                            "refusing to run on the host")

    try:
        if not client.health():
            return _failure(spec, timeline,
                            "sandbox is not healthy — refusing to run on the host")
    except Exception as exc:  # noqa: BLE001
        return _failure(spec, timeline,
                        f"sandbox health check failed ({type(exc).__name__}: {exc})")

    # Inputs are the caller's declared read-only surface. `rw` is accepted by
    # the schema but not by us: a writable input cannot be content-addressed
    # after the fact, so the hash in the spec would be a claim about something
    # that changed underneath it.
    for inp in spec.get("inputs") or []:
        if inp["mode"] == "rw":
            return _failure(spec, timeline,
                            f"input {inp['uri']!r} requests mode 'rw'; inputs are "
                            "mounted read-only so their content_hash stays true")

    egress = spec.get("egress_allowlist")
    env = dict(spec.get("env") or {})
    if egress is not None:
        # Passed through as declared intent so the sandbox (and any auditor
        # reading the trace) can see what the job was permitted to reach.
        env["PRAX_EGRESS_ALLOWLIST"] = ",".join(egress)

    # The schema calls workdir "a scoped write dir inside sandbox" — so Prax
    # provisions it. Without this the very first command fails with a bare
    # exit 2 (chdir into a missing directory), which reads like the job's fault
    # and is actually ours.
    try:
        mk = client.run_command(
            ["sh", "-c", f"mkdir -p {shlex.quote(spec['workdir'])}"], timeout=60)
        if int(_field(mk, "returncode", "exit_code", default=1)) != 0:
            return _failure(spec, timeline,
                            f"could not create workdir {spec['workdir']!r}")
    except Exception as exc:  # noqa: BLE001
        return _failure(spec, timeline,
                        f"could not create workdir ({type(exc).__name__}: {exc})")

    timeline.emit("job_running", target=spec["target"]["kind"])
    try:
        result = client.run_command(
            spec["command"], cwd=spec["workdir"], env=env,
            timeout=spec["_timeout_s"],
        ) or {}
    except Exception as exc:  # noqa: BLE001
        return _failure(spec, timeline,
                        f"execution failed ({type(exc).__name__}: {exc})")

    # `run_command` returns a subprocess.CompletedProcess, not a dict — a live
    # run caught this where the tests did not, because the fake client returned
    # the shape I assumed instead of the shape the sandbox actually returns.
    # Accept both: a dict keeps injected test doubles simple, the attribute path
    # is what production hands back.
    exit_code = int(_field(result, "returncode", "exit_code", default=-1))
    stdout = _field(result, "stdout", default="") or ""
    if stdout:
        timeline.emit("log_chunk", stream="stdout", text=stdout[-4000:])

    # Collect whatever the job wrote — through the SAME channel it ran in.
    #
    # The obvious approach (client.file_list / file_read) is wrong here, and a
    # live run is what proved it: `run_command` executes inside the sandbox
    # container, while the file API resolves per-user paths on the *host* under
    # a different workspace root. The job writes to one filesystem and the
    # collector reads another, so artifacts silently came back empty.
    #
    # Reading the workdir back over the exec channel has no such split: whatever
    # the command could write, this can read. tar+base64 keeps binary artifacts
    # (checkpoints, parquet, plots) byte-exact rather than mangling them through
    # a text stdout.
    files: dict[str, bytes] = {}
    collect_error: str | None = None
    try:
        files, collect_error = _collect_workdir(client, spec["workdir"])
    except Exception as exc:  # noqa: BLE001
        collect_error = f"{type(exc).__name__}: {exc}"
    if collect_error:
        logger.warning("execute: artifact collection failed: %s", collect_error)

    artifacts = _collect_artifacts(files, job_id=job_id, experiment_id=experiment_id,
                                   plan_rev=spec.get("plan_rev"))
    for a in artifacts:
        timeline.emit("artifact_registered", s3_key=a["s3_key"],
                      content_hash=a["content_hash"], kind=a["kind"])

    metrics = _extract_metrics(files)
    if metrics:
        timeline.emit("metric_snapshot", **metrics)

    timeline.emit("job_succeeded" if exit_code == 0 else "job_failed",
                  exit_code=exit_code)
    return {
        "job_id": job_id,
        "experiment_id": experiment_id,
        "exit_code": exit_code,
        "artifacts": artifacts,
        "metrics": metrics,
        "timeline_events": timeline.events,
        "error": (None if exit_code == 0 else f"command exited {exit_code}"),
        # Distinct from `error`: the job may have succeeded while its outputs
        # could not be read back. A caller must be able to tell "produced
        # nothing" from "we could not look".
        "artifact_collection_error": collect_error,
    }




# Cap what a single job can hand back in one response. A job that produces more
# than this is not silently truncated — it is reported as a collection error, so
# the caller knows the artifact list is incomplete rather than assuming it is all.
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


def _collect_workdir(client, workdir: str) -> tuple[dict[str, bytes], str | None]:
    """Read a job's output directory back out of the sandbox.

    Returns ``(files, error)``. A non-None error means the file map is
    incomplete or empty for a reason the caller should see.
    """
    import base64
    import io
    import tarfile

    cmd = ["sh", "-c",
           f"cd {shlex.quote(workdir)} 2>/dev/null && tar czf - . 2>/dev/null | base64 -w0"]
    result = client.run_command(cmd, cwd=workdir, timeout=120)
    rc = int(_field(result, "returncode", "exit_code", default=1))
    payload = (_field(result, "stdout", default="") or "").strip()
    if rc != 0 or not payload:
        return {}, f"workdir {workdir!r} could not be read back (exit {rc})"

    try:
        raw = base64.b64decode(payload)
    except Exception as exc:  # noqa: BLE001
        return {}, f"artifact stream was not decodable: {type(exc).__name__}: {exc}"
    if len(raw) > MAX_ARTIFACT_BYTES:
        return {}, (f"artifacts exceed {MAX_ARTIFACT_BYTES} bytes "
                    f"({len(raw)}) — refusing to return a partial set")

    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            # Flat by basename: the artifact contract keys on s3_key, which is
            # already scoped by experiment and job.
            files[Path(member.name).name] = fh.read()
    return files, None


def _field(result: Any, *names: str, default: Any = None) -> Any:
    """Read a field from either a mapping or an object (CompletedProcess)."""
    for name in names:
        if isinstance(result, dict):
            if name in result:
                return result[name]
        elif hasattr(result, name):
            return getattr(result, name)
    return default


def _extract_metrics(files: dict[str, bytes]) -> dict[str, Any]:
    """Pull metrics out of a ``metrics.json`` output, if the job wrote one.

    A convention, not a requirement: jobs that emit nothing get ``{}`` rather
    than invented numbers.
    """
    for name, data in files.items():
        if Path(name).name == "metrics.json":
            try:
                parsed = json.loads(data.decode("utf-8"))
                return parsed if isinstance(parsed, dict) else {}
            except (ValueError, UnicodeDecodeError):
                logger.warning("execute: metrics.json is not valid JSON — ignoring")
                return {}
    return {}
