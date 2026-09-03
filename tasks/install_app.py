"""
Install applications via snap CLI with AT-SPI App Center attempt.

Decision flow (Klavaro pattern):
  1. Sanity-check: non-empty app_name and package_name
  2. Check already installed → return True immediately (no-op)
  3. Try AT-SPI App Center GUI path (best-effort, non-blocking)
  4. Guarantee: sudo snap install <pkg> via CLI (always runs)
  5. Verify: snap list confirms package present
  6. Abort with clear message if verify fails

Password: read from AUTOMATION_SUDO_PASSWORD env var — never hard-coded.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, "/usr/lib/python3/dist-packages")

from loguru import logger

from core.app_registry import normalize_package
from core.atspi_utils import do_action as _do_action
from core.atspi_utils import find_node as _find_node
from core.atspi_utils import set_text as _set_text
from core.atspi_utils import wait_for_app as _wait_for_app

_APP_CENTER_NAMES = ["snap-store", "snap store", "ubuntu software", "gnome software"]
_POLKIT_NAMES     = ["polkit", "authentication", "pkexec", "password"]


def _is_installed(pkg: str) -> bool:
    """Verify package presence via snap list or which."""
    r = subprocess.run(["snap", "list"], capture_output=True, text=True, timeout=10,
                       check=False)
    if r.returncode == 0 and pkg in r.stdout:
        return True
    r2 = subprocess.run(["which", pkg], capture_output=True, text=True, timeout=5,
                        check=False)
    return r2.returncode == 0


def _atspi_gui_path(app_name: str, password: str) -> bool:
    """
    Best-effort AT-SPI path through App Center.
    Not guaranteed — CLI step below is the safety net.
    """
    try:
        import pyatspi
    except ImportError:
        logger.debug("pyatspi unavailable — skipping AT-SPI GUI path")
        return False

    logger.info("AT-SPI ── Opening App Center…")
    subprocess.Popen(["snap-store"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    time.sleep(5)

    root = _wait_for_app(_APP_CENTER_NAMES, timeout=15)
    if not root:
        logger.warning("AT-SPI ── App Center not found")
        return False

    # Search
    search_node = None
    for role in [pyatspi.ROLE_ENTRY, pyatspi.ROLE_TEXT, pyatspi.ROLE_EDITBAR]:
        node = _find_node(root, role=role)
        if node:
            search_node = node
            break
    if not search_node:
        search_node = _find_node(root, name_contains="search")
    if not search_node:
        logger.warning("AT-SPI ── Search field not found")
        return False

    _do_action(search_node)
    time.sleep(0.4)
    _set_text(search_node, app_name)
    time.sleep(3)

    # Click result
    root = _wait_for_app(_APP_CENTER_NAMES, timeout=5) or root
    clicked = False
    for role in [pyatspi.ROLE_LIST_ITEM, pyatspi.ROLE_PUSH_BUTTON, pyatspi.ROLE_LABEL]:
        node = _find_node(root, role=role, name_contains=app_name.split()[0])
        if node:
            _do_action(node)
            time.sleep(3)
            clicked = True
            break
    if not clicked:
        logger.warning("AT-SPI ── App result not found")
        return False

    # Click Install
    root = _wait_for_app(_APP_CENTER_NAMES, timeout=5) or root
    for hint in ["install", "get"]:
        node = _find_node(root, role=pyatspi.ROLE_PUSH_BUTTON, name_contains=hint)
        if node:
            _do_action(node)
            time.sleep(3)
            break

    # Polkit auth
    if password:
        polkit = _wait_for_app(_POLKIT_NAMES, timeout=20)
        if polkit:
            pwd_node = _find_node(polkit, role=pyatspi.ROLE_PASSWORD_TEXT)
            if pwd_node:
                _do_action(pwd_node)
                time.sleep(0.3)
                _set_text(pwd_node, password)
                time.sleep(0.5)
            for hint in ["authenticate", "ok", "yes", "confirm"]:
                btn = _find_node(polkit, role=pyatspi.ROLE_PUSH_BUTTON, name_contains=hint)
                if btn:
                    _do_action(btn)
                    time.sleep(2)
                    break

    return True


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    app_name = str(args.get("app_name", "")).strip()
    password = str(args.get("password", "") or os.getenv("AUTOMATION_SUDO_PASSWORD", ""))
    pkg      = normalize_package(app_name, str(args.get("package_name", "")))

    # ── Sanity check ──────────────────────────────────────────────────────────
    if not app_name:
        raise ValueError("'app_name' is required")
    if not pkg:
        raise ValueError(f"Could not resolve package name for '{app_name}'")

    logger.info(f"╔══ Install: '{app_name}' → package '{pkg}' ══╗")

    # ── Already installed? ────────────────────────────────────────────────────
    if _is_installed(pkg):
        logger.info(f"✅ '{pkg}' already installed — nothing to do")
        return True

    # ── Try AT-SPI GUI path (best-effort) ─────────────────────────────────────
    try:
        _atspi_gui_path(app_name, password)
    except Exception as exc:
        logger.debug(f"AT-SPI GUI path raised: {exc} — continuing to CLI guarantee")

    # ── CLI guarantee: sudo snap install ─────────────────────────────────────
    logger.info(f"CLI ── sudo snap install {pkg}")
    if password:
        result = subprocess.run(
            f"echo {password!r} | sudo -S snap install {pkg}",
            shell=True, capture_output=True, text=True, timeout=300, check=False,
        )
    else:
        result = subprocess.run(
            ["sudo", "snap", "install", pkg],
            capture_output=True, text=True, timeout=300, check=False,
        )

    if result.returncode == 0:
        logger.info(f"CLI ── {result.stdout.strip() or 'OK'}")
    else:
        logger.warning(f"CLI ── stderr: {result.stderr.strip()}")

    # ── Verify installation ───────────────────────────────────────────────────
    time.sleep(2)
    if _is_installed(pkg):
        logger.info(f"╚══ ✅ VERIFIED: '{pkg}' installed successfully ══╝")
        return True
    else:
        logger.error(
            f"╚══ ❌ FAILED: '{pkg}' not found after install attempt.\n"
            f"    stderr: {result.stderr.strip()}\n"
            f"    Try manually: sudo snap install {pkg}"
        )
        return False


def cleanup(_resources: dict) -> None:
    pass


if __name__ == "__main__":
    _pwd = os.getenv("AUTOMATION_SUDO_PASSWORD", "")
    r = execute({"app_name": "vlc", "package_name": "vlc"}, {})
    print("Result:", r)
