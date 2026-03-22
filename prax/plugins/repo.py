"""Plugin repository service — manages a separate git repo for agent-created plugins.

The agent writes plugins to a private git repository.  The user configures the
repo URL and an SSH deploy key (base64-encoded) via environment variables.  The
agent pushes to a configurable branch, and the user can later cherry-pick
plugins into the main repo.
"""
from __future__ import annotations

import base64
import json as _json
import logging
import os
import re
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


class PluginRepo:
    """Manages git operations for the plugin repository."""

    def __init__(
        self,
        repo_url: str,
        ssh_key_b64: str,
        branch: str = "plugins",
        local_path: str | None = None,
    ) -> None:
        self.repo_url = repo_url
        self.branch = branch
        self._ssh_key_b64 = ssh_key_b64
        self._local_path = Path(local_path or "./plugin_repo")
        self._ssh_key_file: str | None = None
        self._verified_private: bool | None = None  # cached visibility check

    @property
    def local_path(self) -> Path:
        return self._local_path

    @property
    def plugins_dir(self) -> Path:
        """Directory within the repo where plugins live."""
        return self._local_path / "plugins"

    @property
    def catalog_path(self) -> Path:
        return self._local_path / "CATALOG.md"

    def is_configured(self) -> bool:
        """Check if repo URL and SSH key are both set."""
        return bool(self.repo_url and self._ssh_key_b64)

    # ------------------------------------------------------------------
    # Repo visibility check
    # ------------------------------------------------------------------

    def _parse_repo_owner_name(self) -> tuple[str, str, str] | None:
        """Parse the repo URL to extract ``(host, owner, repo_name)``.

        Supports:
          ``git@github.com:user/repo.git``
          ``ssh://git@github.com/user/repo.git``
          ``https://github.com/user/repo.git``
        """
        url = self.repo_url.strip()

        # SSH shorthand: git@github.com:user/repo.git
        m = re.match(r"^git@([^:]+):([^/]+)/([^/]+?)(?:\.git)?$", url)
        if m:
            return m.group(1), m.group(2), m.group(3)

        # SSH or HTTPS URL
        m = re.match(
            r"^(?:https?|ssh)://(?:git@)?([^/]+)/([^/]+)/([^/]+?)(?:\.git)?$", url
        )
        if m:
            return m.group(1), m.group(2), m.group(3)

        return None

    def verify_private(self) -> bool:
        """Check that the repo is private by probing the host's public API.

        Returns ``True`` if the repo is confirmed private (or visibility cannot
        be determined).  Returns ``False`` if the repo is **public** — callers
        should refuse to push in that case.

        The result is cached for the lifetime of this object so the API is only
        hit once.
        """
        if self._verified_private is not None:
            return self._verified_private

        parsed = self._parse_repo_owner_name()
        if not parsed:
            logger.warning(
                "Could not parse repo URL %s — skipping visibility check",
                self.repo_url,
            )
            self._verified_private = True
            return True

        host, owner, name = parsed

        if "github.com" in host:
            api_url = f"https://api.github.com/repos/{owner}/{name}"
        elif "gitlab.com" in host:
            api_url = (
                f"https://gitlab.com/api/v4/projects/"
                f"{urllib.request.quote(f'{owner}/{name}', safe='')}"
            )
        else:
            logger.info(
                "Unknown git host %s — cannot verify repo visibility, "
                "refusing to push to be safe",
                host,
            )
            self._verified_private = False
            return False

        try:
            req = urllib.request.Request(
                api_url, headers={"User-Agent": "prax-plugin-repo"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read())

                if "github.com" in host:
                    is_private = data.get("private", False)
                elif "gitlab.com" in host:
                    is_private = data.get("visibility") != "public"
                else:
                    is_private = True

                if not is_private:
                    logger.error(
                        "Repo %s/%s on %s is PUBLIC — refusing to push",
                        owner, name, host,
                    )
                self._verified_private = is_private
                return is_private

        except urllib.error.HTTPError as e:
            if e.code == 404:
                # 404 = not visible without auth = private. This is what we want.
                logger.info("Repo %s/%s returned 404 (private) — OK", owner, name)
                self._verified_private = True
                return True
            logger.warning(
                "Visibility check failed (HTTP %d) for %s — refusing to push",
                e.code, api_url,
            )
            self._verified_private = False
            return False

        except Exception:
            logger.warning(
                "Could not verify repo visibility (network error) — refusing to push",
                exc_info=True,
            )
            self._verified_private = False
            return False

    # ------------------------------------------------------------------
    # SSH key management
    # ------------------------------------------------------------------

    def _write_ssh_key(self) -> str:
        """Decode the base64 SSH key and write to a temp file."""
        if self._ssh_key_file and os.path.exists(self._ssh_key_file):
            return self._ssh_key_file
        key_bytes = base64.b64decode(self._ssh_key_b64)
        fd, path = tempfile.mkstemp(prefix="plugin_repo_key_", suffix=".pem")
        os.write(fd, key_bytes)
        os.close(fd)
        os.chmod(path, 0o600)
        self._ssh_key_file = path
        return path

    def _git_env(self) -> dict[str, str]:
        """Return environment with GIT_SSH_COMMAND pointing to our deploy key."""
        key_path = self._write_ssh_key()
        env = os.environ.copy()
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {key_path} -o StrictHostKeyChecking=no "
            f"-o UserKnownHostsFile=/dev/null"
        )
        return env

    # ------------------------------------------------------------------
    # Git operations
    # ------------------------------------------------------------------

    def _run_git(
        self, *args: str, cwd: str | Path | None = None
    ) -> subprocess.CompletedProcess:
        """Run a git command with SSH key configured."""
        cmd = ["git"] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(cwd or self._local_path),
            env=self._git_env(),
            timeout=60,
        )
        if result.returncode != 0:
            logger.warning("git %s failed: %s", " ".join(args), result.stderr[:500])
        return result

    def ensure_cloned(self) -> bool:
        """Clone the repo if not present locally, or pull latest.

        Refuses to proceed if the repo is detected as public.
        """
        if not self.verify_private():
            logger.error("Refusing to clone — repo is public")
            return False

        if (self._local_path / ".git").exists():
            return self.pull()

        self._local_path.mkdir(parents=True, exist_ok=True)
        result = self._run_git(
            "clone", "-b", self.branch, self.repo_url, str(self._local_path),
            cwd=self._local_path.parent,
        )
        if result.returncode != 0:
            # Branch may not exist — clone default then create it.
            result = self._run_git(
                "clone", self.repo_url, str(self._local_path),
                cwd=self._local_path.parent,
            )
            if result.returncode != 0:
                logger.error("Failed to clone plugin repo: %s", result.stderr[:500])
                return False
            self._run_git("checkout", "-b", self.branch)

        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        return True

    def pull(self) -> bool:
        """Pull latest changes from the remote."""
        result = self._run_git("pull", "origin", self.branch)
        return result.returncode == 0

    def commit_and_push(self, message: str) -> dict:
        """Stage all changes, commit, and push to the remote branch.

        Refuses to push if the repo is detected as public.
        """
        if not self.verify_private():
            return {"error": "Refusing to push — repo is public. Only private repos are allowed."}

        self._run_git("add", "-A")

        status = self._run_git("status", "--porcelain")
        if not status.stdout.strip():
            return {"status": "no_changes"}

        result = self._run_git("commit", "-m", message)
        if result.returncode != 0:
            return {"error": f"Commit failed: {result.stderr[:300]}"}

        result = self._run_git("push", "origin", self.branch)
        if result.returncode != 0:
            # First push — set upstream.
            result = self._run_git("push", "-u", "origin", self.branch)
            if result.returncode != 0:
                return {"error": f"Push failed: {result.stderr[:300]}"}

        return {"status": "pushed", "branch": self.branch}

    def cleanup(self) -> None:
        """Remove the temporary SSH key file."""
        if self._ssh_key_file and os.path.exists(self._ssh_key_file):
            os.unlink(self._ssh_key_file)
            self._ssh_key_file = None


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_repo: PluginRepo | None = None
_repo_lock = threading.Lock()
_repo_initialized = False


def get_plugin_repo() -> PluginRepo | None:
    """Return the plugin repo singleton, or None if not configured."""
    global _repo, _repo_initialized

    if _repo_initialized:
        return _repo if (_repo and _repo.is_configured()) else None

    with _repo_lock:
        if _repo_initialized:
            return _repo if (_repo and _repo.is_configured()) else None

        from prax.settings import settings

        repo_url = getattr(settings, "plugin_repo_url", None) or ""
        ssh_key = getattr(settings, "ssh_key_b64", None) or getattr(settings, "plugin_repo_ssh_key_b64", None) or ""
        branch = getattr(settings, "plugin_repo_branch", None) or "plugins"
        local_path = getattr(settings, "plugin_repo_local_path", None) or "./plugin_repo"

        _repo = PluginRepo(
            repo_url=repo_url,
            ssh_key_b64=ssh_key,
            branch=branch,
            local_path=local_path,
        )
        _repo_initialized = True

        if _repo.is_configured():
            try:
                _repo.ensure_cloned()
            except Exception:
                logger.exception("Failed to initialize plugin repo")
            return _repo

        return None
