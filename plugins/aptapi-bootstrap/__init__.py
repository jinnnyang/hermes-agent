"""AptAPI bootstrap — installs OmniRoute plugins on first launch.

Checks whether the OmniRoute model provider already exists under the profile's
plugins/ root. If not, it fetches the latest aptapi-branch release from GitHub
(via git clone when available, otherwise a tarball download) and copies the
plugin directories into the profile.

After first install, the plugins' own self-update mechanism (24h TTL) takes
over. This bootstrap runs once per process, is fully best-effort, and never
blocks or fails hermes startup.

Env overrides:
  APTAPI_BOOTSTRAP_OFF   set to 1/true/yes to disable
  APTAPI_BOOTSTRAP_URL   override the plugin repo URL
  APTAPI_BOOTSTRAP_BRANCH override the branch (default: aptapi)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

REPO_URL = "https://github.com/jinnnyang/omniroute-hermes-plugin.git"
BRANCH = "aptapi"
TARBALL_URL = "https://github.com/jinnnyang/omniroute-hermes-plugin/archive/refs/heads/aptapi.tar.gz"

# Directories to copy from <cloned>/plugins/ to <profile>/plugins/
PLUGIN_DIRS = (
    "model-providers/omniroute",
    "image_gen/omniroute",
    "web/omniroute",
    "update-config",
    "update-skills",
    "_omniroute_common",
)

STAMP_NAME = ".aptapi-bootstrap.stamp"
CLONE_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 60

_guard = threading.Lock()
_done = False


def _warn(msg: str) -> None:
    print(f"[aptapi-bootstrap] {msg}", file=sys.stderr)


def _profile_plugins_root() -> Path | None:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home()) / "plugins"
    except Exception:
        home = os.environ.get("HERMES_HOME", "").strip()
        return Path(home) / "plugins" if home else None


def _disabled() -> bool:
    return os.environ.get("APTAPI_BOOTSTRAP_OFF", "").strip().lower() in ("1", "true", "yes")


def _repo_url() -> str:
    return os.environ.get("APTAPI_BOOTSTRAP_URL", "").strip() or REPO_URL


def _branch() -> str:
    return os.environ.get("APTAPI_BOOTSTRAP_BRANCH", "").strip() or BRANCH


def _try_git_clone(dest: Path) -> bool:
    git = shutil.which("git")
    if git is None:
        return False
    try:
        subprocess.run(
            [git, "clone", "--depth", "1", "--branch", _branch(), "--quiet",
             _repo_url(), str(dest)],
            check=True, capture_output=True, text=True, timeout=CLONE_TIMEOUT,
        )
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        _warn(f"git clone failed: {exc}")
        return False


def _try_tarball(dest: Path) -> bool:
    try:
        req = urllib.request.Request(TARBALL_URL, headers={"User-Agent": "hermes-aptapi-bootstrap"})
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            data = resp.read()
    except Exception as exc:
        _warn(f"tarball download failed: {exc}")
        return False
    tarball = dest.parent / "aptapi-plugins.tar.gz"
    try:
        tarball.write_bytes(data)
        with tarfile.open(tarball, "r:gz") as tf:
            tf.extractall(dest.parent)
        # extracted top-level dir is omniroute-hermes-plugin-<branch>/
        extracted = dest.parent / f"omniroute-hermes-plugin-{_branch()}"
        if extracted.is_dir():
            shutil.move(str(extracted), str(dest))
            return True
        _warn("tarball extracted but expected directory not found")
        return False
    except (OSError, tarfile.TarError) as exc:
        _warn(f"tarball extract failed: {exc}")
        return False
    finally:
        try:
            tarball.unlink(missing_ok=True)
        except OSError:
            pass


def _copy_plugins(src_plugins: Path, dst_plugins: Path) -> int:
    copied = 0
    dst_plugins.mkdir(parents=True, exist_ok=True)
    for rel in PLUGIN_DIRS:
        src = src_plugins / rel
        if not src.is_dir():
            continue
        dst = dst_plugins / rel
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
        copied += 1
    return copied


def bootstrap() -> None:
    global _done
    if _done:
        return
    with _guard:
        if _done:
            return
        _done = True

    if _disabled():
        return

    dst_root = _profile_plugins_root()
    if dst_root is None:
        return

    # Already installed?
    if (dst_root / "model-providers" / "omniroute").is_dir():
        return

    stamp = dst_root.parent / STAMP_NAME
    if stamp.exists():
        # A previous attempt left a stamp; don't retry on every launch.
        # The user can delete the stamp to force a retry.
        return

    _warn("first launch: installing OmniRoute plugins from aptapi branch...")
    staged = Path(tempfile.mkdtemp(prefix="aptapi-bootstrap-"))
    try:
        ok = _try_git_clone(staged)
        if not ok:
            ok = _try_tarball(staged)
        if not ok:
            _warn("could not fetch plugin repo; will retry next launch")
            return

        src_plugins = staged / "plugins"
        if not src_plugins.is_dir():
            _warn("fetched repo has no plugins/ directory")
            return

        n = _copy_plugins(src_plugins, dst_root)
        _warn(f"installed {n} OmniRoute plugin dir(s) (effective next launch)")
        stamp.write_text(f"time={time.time()}\n", encoding="utf-8")
    finally:
        shutil.rmtree(staged, ignore_errors=True)


# fire at import time
bootstrap()
