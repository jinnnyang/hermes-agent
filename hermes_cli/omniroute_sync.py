"""Best-effort refresh of the OmniRoute provider plugins (model/image/web).

Ships with the jinnnyang fork (B: install-flow + update auto-deploy). The plugin
implementation lives in a standalone repo; this refreshes the profile's copy after
``hermes update`` so the fork's users keep the latest plugin code without extra
steps. Never fails the update: plugin refresh is best-effort and only touches the
four omniroute directories under the profile's plugins/ root.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DEFAULT_PLUGIN_URL = "https://github.com/jinnnyang/omniroute-hermes-plugin.git"
PLUGIN_DIRS = (
    "model-providers/omniroute",
    "image_gen/omniroute",
    "web/omniroute",
    "_omniroute_common",
)


def sync_omniroute_plugins(root: Path, git_cmd) -> None:
    """Refresh the profile's omniroute plugin dirs from the standalone repo."""
    url = os.getenv("OMNIROUTE_PLUGIN_URL") or DEFAULT_PLUGIN_URL
    if not url:
        return
    from hermes_constants import get_hermes_home

    plugin_root = Path(get_hermes_home()) / "plugins"
    if not plugin_root.exists():
        return
    staged = Path(tempfile.mkdtemp(prefix="omniroute-", dir=str(plugin_root.parent)))
    try:
        subprocess.run(
            [*git_cmd, "clone", "--depth", "1", url, str(staged)],
            check=True, capture_output=True, text=True, timeout=120)
        src = staged / "plugins"
        if not src.is_dir():
            return
        for rel in PLUGIN_DIRS:
            src_dir = src / rel
            if not src_dir.is_dir():
                continue
            dst = plugin_root / rel
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src_dir, dst)
        print(f"  (omniroute plugins synced from {url})")
    except (subprocess.SubprocessError, OSError, TimeoutError) as exc:
        print(f"  (omniroute plugins sync skipped: {exc})")
    finally:
        shutil.rmtree(staged, ignore_errors=True)
