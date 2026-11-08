"""``updates.track_current_branch``: an install follows its own checkout branch.

Install provenance is the checkout itself — ``origin`` is the repo it was cloned
from, the current branch is the branch it was installed at — so with the option on
(default) ``hermes update`` targets ``origin/<current branch>``: same repo, same
branch, no detour through the channel system's references. Real local ``file://``
origins and real clones, matching the fetch-helper regression tests: git behavior
is the thing under test, mocks would prove nothing.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli import update_cmd, update_cmd_check

git_cmd = ["git"]


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False,
    )


def _commit_all(repo: Path, msg: str) -> None:
    _run(repo, "add", "-A")
    _run(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", msg, "--allow-empty")


@pytest.fixture
def fork_checkout(tmp_path: Path) -> Path:
    """A fork-shaped checkout: origin has ``main`` + ``moirai``, the clone sits on ``moirai``."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    _run(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _run(tmp_path, "clone", "-q", f"file://{origin}", str(seed))
    _commit_all(seed, "init")
    assert _run(seed, "push", "-q", "origin", "HEAD:refs/heads/main", "HEAD:refs/heads/moirai").returncode == 0
    work = tmp_path / "work"
    _run(tmp_path, "clone", "-q", "--branch", "moirai", f"file://{origin}", str(work))
    return work


def test_tracked_branch_is_the_checkout_branch(fork_checkout: Path, monkeypatch, tmp_path: Path) -> None:
    """Tracking follows the checkout's own branch; every override restores the channel path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(update_cmd._m(), "PROJECT_ROOT", fork_checkout)

    # Default on: the update follows the branch the install was cloned at.
    assert update_cmd_check.track_current_branch(git_cmd) == "moirai"
    # Explicit --branch/--channel always win over tracking.
    assert update_cmd_check.track_current_branch(git_cmd, SimpleNamespace(branch="main"), branch_explicit=True) is None
    assert update_cmd_check.track_current_branch(git_cmd, SimpleNamespace(branch="main")) is None
    assert update_cmd_check.track_current_branch(git_cmd, SimpleNamespace(channel="stable")) is None
    # A detached checkout has no current branch to follow.
    assert _run(fork_checkout, "checkout", "-q", "--detach", "HEAD").returncode == 0
    assert update_cmd_check.track_current_branch(git_cmd) is None
    # updates.track_current_branch: false restores the standard channel behavior.
    assert _run(fork_checkout, "checkout", "-q", "moirai").returncode == 0
    (home / "config.yaml").write_text("updates:\n  track_current_branch: false\n", encoding="utf-8")
    assert update_cmd_check.track_current_branch(git_cmd) is None


def test_a_chosen_channel_wins_over_tracking(fork_checkout: Path, monkeypatch, tmp_path: Path) -> None:
    """A persisted ``--set-channel`` choice is this install's consent to a specific source;
    tracking applies only to installs riding their default channel."""
    from hermes_cli.update_channel import install_id

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(update_cmd._m(), "PROJECT_ROOT", fork_checkout)
    assert update_cmd_check.track_current_branch(git_cmd) == "moirai"

    record = {install_id(fork_checkout): {"path": str(fork_checkout), "channel": "stable"}}
    (home / "config.yaml").write_text(
        f"update:\n  installs:\n    {next(iter(record))}:\n"
        f"      path: {fork_checkout.as_posix()}\n      channel: stable\n", encoding="utf-8")
    assert update_cmd_check.track_current_branch(git_cmd) is None


def test_resolve_tracked_branch_overrides_the_default(fork_checkout: Path, monkeypatch, tmp_path: Path) -> None:
    """The apply path's wiring: ``(tracked, effective)`` with the tracked branch winning."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(update_cmd._m(), "PROJECT_ROOT", fork_checkout)

    assert update_cmd_check.resolve_tracked_branch(SimpleNamespace(), "main", git_cmd) == ("moirai", "moirai")
    (home / "config.yaml").write_text("updates:\n  track_current_branch: false\n", encoding="utf-8")
    assert update_cmd_check.resolve_tracked_branch(SimpleNamespace(), "main", git_cmd) == (None, "main")


def test_checkout_without_own_git_never_tracks(tmp_path: Path, monkeypatch) -> None:
    """A checkout without its own ``.git`` (a zip install) must not track: bare git
    walks up and names the enclosing repo's branch, and the update would follow a
    foreign branch."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    parent = tmp_path / "parent"
    assert _run(tmp_path, "init", "-q", "-b", "main", str(parent)).returncode == 0
    (parent / "child").mkdir()
    monkeypatch.setattr(update_cmd._m(), "PROJECT_ROOT", parent / "child")
    # The trap this guard exists for: bare git inside child names the parent's branch.
    assert _run(parent / "child", "branch", "--show-current").stdout.strip() == "main"
    assert update_cmd_check.track_current_branch(git_cmd) is None


def test_zip_fallback_refuses_a_tracked_non_main_branch(fork_checkout: Path, monkeypatch, tmp_path: Path,
                                                        capsys) -> None:
    """The Windows ZIP fallback re-read its branch from bare args, so a tracked non-main
    branch came back as ``main`` and the fork checkout was silently swapped for upstream
    main. The refusal that guards an explicit ``--branch`` must fire for a tracked branch
    too: ``completion_request`` carries the branch the caller actually targeted."""
    from hermes_cli.update_cmd_zip import _update_via_zip

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    with pytest.raises(SystemExit) as error:
        _update_via_zip(
            SimpleNamespace(), completion_request={"home": str(home), "branch": "moirai"})
    assert error.value.code == 1
    assert "not supported on the Windows ZIP-fallback update path" in capsys.readouterr().out


def test_tracked_main_skips_the_upstream_remote(fork_checkout: Path, tmp_path: Path) -> None:
    """A tracked ``main`` compares against ``origin/main`` (the repo it was installed from),
    not the ``upstream`` remote the channel default treats as canonical."""
    upstream = tmp_path / "upstream.git"
    up_seed = tmp_path / "up_seed"
    _run(tmp_path, "init", "-q", "--bare", "-b", "main", str(upstream))
    _run(tmp_path, "clone", "-q", f"file://{upstream}", str(up_seed))
    _commit_all(up_seed, "upstream tip")
    assert _run(up_seed, "push", "-q", "origin", "HEAD:refs/heads/main").returncode == 0
    assert _run(fork_checkout, "remote", "add", "upstream", f"file://{upstream}").returncode == 0

    # Default: main prefers upstream as the canonical reference.
    fetch_result, compare = update_cmd_check.fetch_compare_branch(git_cmd, fork_checkout, "main", [])
    assert fetch_result.returncode == 0, fetch_result.stderr
    assert compare == "upstream/main"

    # updates.track_current_branch: same repo, same branch — origin wins even for main.
    fetch_result, compare = update_cmd_check.fetch_compare_branch(
        git_cmd, fork_checkout, "main", [], prefer_upstream=False)
    assert fetch_result.returncode == 0, fetch_result.stderr
    assert compare == "origin/main"


def test_pinned_branch_skips_channel_resolution(fork_checkout: Path, monkeypatch, tmp_path: Path) -> None:
    """A pinned branch resolves to ``origin/<branch>`` without consulting the channel system."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    # If the channel system were consulted, this would fail the test.
    monkeypatch.setattr(
        "hermes_cli.source_releases.resolve_source_target",
        lambda *a, **k: pytest.fail("channel resolution must not run for a pinned branch"))
    request = {"home": str(home), "branch": "moirai"}
    selected = update_cmd_check.select_apply_target(
        SimpleNamespace(), "moirai", request, git_cmd=None, stop=lambda: None, branch_pinned=True)
    assert selected == ("origin/moirai", None, False, None)
    assert request["branch"] == "moirai"
