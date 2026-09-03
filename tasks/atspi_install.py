"""
App Center GUI Installer — Pure GUI automation, no CLI fallback.

Flow:
  1.  Reuse running App Center OR launch fresh (saves ~20 s)
  2.  Search via AT-SPI set_text → keyboard fallback
  3.  Press Enter
  4.  Click app in results    ← event-driven wait (no fixed sleep)
  5.  Click Install button    ← event-driven wait
  6.  Auth dialog appears     ← event-driven wait (not 30 s fixed)
  7.  Type password + Enter
  8.  Poll snap list every 2 s until installed

Every step uses _await_node() — returns the instant the UI element appears
instead of sleeping a fixed number of seconds.  Typical setup time: ~6–10 s.

Password source (checked in order):
  1. args["password"]
  2. config/config.yaml → install.sudo_password
  3. AUTOMATION_SUDO_PASSWORD env var
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import evdev
from loguru import logger

from core.logger import finish, notify, start
from core.uinput_keyboard import VirtualKeyboard

sys.path.insert(0, "/usr/lib/python3/dist-packages")

from core.app_registry import normalize_package
from core.atspi_utils import do_action, find_node, set_text

# ── Constants ─────────────────────────────────────────────────────────────────
_APP_CENTER      = ["snap-store", "snap store", "ubuntu software",
                    "gnome software", "app center", "software"]
_POLKIT          = ["polkit", "authentication", "pkexec", "password", "authenticate"]
_INSTALL_TIMEOUT = 300          # 5 min max for large snap packages
_POLL_INTERVAL   = 0.25         # seconds between AT-SPI polls


# ── Core event-driven helper ──────────────────────────────────────────────────

def _await_node(app_frags: list[str],
                role=None,
                name_contains: str | None = None,
                timeout: float = 6.0) -> tuple[object | None, object | None]:
    """
    Poll the AT-SPI desktop every _POLL_INTERVAL seconds.
    Returns (app_root, matching_node) the instant the element appears.
    Returns (None, None) on timeout.

    This replaces ALL fixed time.sleep() calls — we never wait longer
    than the UI actually needs.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            import pyatspi
            desktop = pyatspi.Registry.getDesktop(0)
            for app in desktop:
                if app is None:
                    continue
                if not any(frag in (app.name or "").lower() for frag in app_frags):
                    continue
                # App found — now look for the specific node
                if role is None and name_contains is None:
                    return app, app          # caller just wanted the app root
                node = find_node(app, role=role, name_contains=name_contains)
                if node:
                    return app, node
        except Exception:
            pass
        time.sleep(_POLL_INTERVAL)
    return None, None


# ── Small utilities ───────────────────────────────────────────────────────────

def _load_password(args_password: str) -> str:
    if args_password:
        return args_password
    try:
        from config.config_loader import load_config
        pwd = load_config().get("install", {}).get("sudo_password", "")
        if pwd:
            return str(pwd)
    except Exception:
        pass
    return os.getenv("AUTOMATION_SUDO_PASSWORD", "")


def _is_installed(pkg: str) -> bool:
    r = subprocess.run(["snap", "list"], capture_output=True, text=True,
                       timeout=15, check=False)
    if r.returncode == 0 and pkg.lower() in r.stdout.lower():
        return True
    r2 = subprocess.run(["which", pkg], capture_output=True, text=True,
                        timeout=5, check=False)
    return r2.returncode == 0


def _is_app_center_running() -> bool:
    r = subprocess.run(["pgrep", "-f", "snap-store"],
                       capture_output=True, check=False)
    return r.returncode == 0


def _focus_app_center() -> None:
    for title in ["App Center", "snap store", "Ubuntu Software"]:
        subprocess.run(["wmctrl", "-a", title], capture_output=True, check=False)
    time.sleep(0.2)


def _screenshot(name: str) -> None:
    try:
        from core.logger import take_screenshot
        take_screenshot(name=name)
    except Exception:
        pass


# ── Step 1: Open / reuse App Center ──────────────────────────────────────────

def _step1_open() -> object | None:
    logger.info("STEP 1 ── App Center …")

    # ── Reuse if already running (saves ~20 s) ────────────────────────────────
    if _is_app_center_running():
        logger.info("         snap-store already running — focusing ✅")
        _focus_app_center()
        root, _ = _await_node(_APP_CENTER, timeout=4)
        if root:
            logger.info(f"         Reused existing window: '{root.name}' ✅")
            return root
        logger.info("         Could not get AT-SPI root for running instance — restarting")

    # ── Fresh launch ──────────────────────────────────────────────────────────
    subprocess.run(["pkill", "-f", "snap-store"], capture_output=True, check=False)
    time.sleep(0.5)

    subprocess.Popen(
        ["snap-store"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    logger.info("         Launched snap-store — waiting for window …")

    # Poll wmctrl every 0.3 s (max 15 s)
    deadline = time.time() + 15
    while time.time() < deadline:
        r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, check=False)
        if any(w in r.stdout.lower() for w in ["snap store", "app center",
                                               "ubuntu software", "software"]):
            elapsed = 15 - (deadline - time.time())
            logger.info(f"         Window appeared in wmctrl ({elapsed:.1f} s) ✅")
            break
        time.sleep(0.3)

    # Short render settle then focus
    time.sleep(1.0)
    _focus_app_center()

    # Event-driven wait for AT-SPI root (up to 12 s)
    root, _ = _await_node(_APP_CENTER, timeout=12)
    if root:
        logger.info(f"         AT-SPI root: '{root.name}' ✅")
    else:
        logger.warning("         AT-SPI root not found — continuing anyway")
    return root


# ── Step 2: Search ────────────────────────────────────────────────────────────

def _step2_search(root: object, app_name: str, vk: VirtualKeyboard) -> None:
    logger.info(f"STEP 2 ── Searching for '{app_name}' …")
    import pyatspi

    # Try AT-SPI set_text
    search_node = None
    for role in [pyatspi.ROLE_ENTRY, pyatspi.ROLE_TEXT, pyatspi.ROLE_EDITBAR]:
        node = find_node(root, role=role)
        if node:
            search_node = node
            break
    if not search_node:
        search_node = find_node(root, name_contains="search")

    if search_node:
        do_action(search_node)
        time.sleep(0.2)
        if set_text(search_node, app_name):
            logger.info(f"         Typed via AT-SPI set_text ✅")
            vk.press_key(evdev.ecodes.KEY_ENTER, delay=0.08)
            logger.info("         Enter pressed ✅")
            return

    # Keyboard fallback
    logger.info("         Keyboard fallback …")
    _focus_app_center()
    vk.press_key(evdev.ecodes.KEY_LEFTCTRL, delay=0.05)
    time.sleep(0.05)
    vk.press_key(evdev.ecodes.KEY_F, delay=0.05)
    time.sleep(0.15)
    vk.press_key(evdev.ecodes.KEY_LEFTCTRL, delay=0.05)
    time.sleep(0.05)
    vk.press_key(evdev.ecodes.KEY_A, delay=0.05)
    time.sleep(0.05)
    vk.type_text(app_name, cpm=1200)
    time.sleep(0.15)
    vk.press_key(evdev.ecodes.KEY_ENTER, delay=0.08)
    logger.info(f"         Typed via keyboard + Enter ✅")


# ── Step 3: Click search result ───────────────────────────────────────────────

def _step3_click_result(app_name: str, vk: VirtualKeyboard) -> bool:
    logger.info(f"STEP 3 ── Clicking result for '{app_name}' …")
    import pyatspi

    first_word = app_name.split()[0]

    # Event-driven: wait until the result button appears (up to 10 s)
    for role in [pyatspi.ROLE_PUSH_BUTTON, pyatspi.ROLE_LIST_ITEM,
                 pyatspi.ROLE_LABEL, pyatspi.ROLE_ICON]:
        root, node = _await_node(_APP_CENTER, role=role,
                                 name_contains=first_word, timeout=10)
        if node:
            logger.info(f"         Found: role={node.getRoleName()} "
                        f"name='{node.name[:60]}' ✅")
            do_action(node)
            return True

    # Any node whose name contains the first word
    root, node = _await_node(_APP_CENTER, name_contains=first_word, timeout=5)
    if root and node:
        logger.info(f"         Found by name: '{node.name[:60]}'")
        do_action(node)
        return True

    # Keyboard Tab fallback
    logger.info("         Keyboard Tab fallback …")
    _focus_app_center()
    for _ in range(5):
        vk.press_key(evdev.ecodes.KEY_TAB, delay=0.08)
    vk.press_key(evdev.ecodes.KEY_ENTER, delay=0.08)
    return True


# ── Step 4: Click Install button ──────────────────────────────────────────────

def _step4_click_install(vk: VirtualKeyboard) -> bool:
    logger.info("STEP 4 ── Clicking Install …")
    import pyatspi

    # Event-driven: wait until Install button appears (up to 10 s)
    for hint in ["install", "get", "purchase"]:
        root, node = _await_node(_APP_CENTER, role=pyatspi.ROLE_PUSH_BUTTON,
                                 name_contains=hint, timeout=10)
        if node:
            logger.info(f"         Found: '{node.name}' ✅")
            do_action(node)
            logger.info("         Install clicked ✅")
            return True

    # Keyboard Tab fallback
    logger.info("         Keyboard Tab fallback …")
    _focus_app_center()
    for _ in range(6):
        vk.press_key(evdev.ecodes.KEY_TAB, delay=0.08)
    vk.press_key(evdev.ecodes.KEY_ENTER, delay=0.08)
    return True


# ── Step 5: Authenticate ──────────────────────────────────────────────────────

def _step5_authenticate(password: str, vk: VirtualKeyboard) -> bool:
    logger.info("STEP 5 ── Waiting for auth dialog …")
    import pyatspi

    # Event-driven: wait for polkit (up to 8 s instead of 30 s)
    polkit, _ = _await_node(_POLKIT, timeout=8)

    if polkit:
        logger.info(f"         Auth dialog: '{polkit.name}' ✅")

        # Find password field
        pwd_node = (find_node(polkit, role=pyatspi.ROLE_PASSWORD_TEXT)
                    or find_node(polkit, role=pyatspi.ROLE_ENTRY))

        if pwd_node:
            do_action(pwd_node)
            time.sleep(0.15)
            if not set_text(pwd_node, password):
                vk.type_text(password, cpm=1200)
        else:
            vk.type_text(password, cpm=1200)

        logger.info("         Password entered ✅")
        time.sleep(0.2)

        # Click Authenticate / OK
        for hint in ["authenticate", "ok", "yes", "confirm"]:
            btn = find_node(polkit, role=pyatspi.ROLE_PUSH_BUTTON,
                            name_contains=hint)
            if btn:
                logger.info(f"         Clicking '{btn.name}' ✅")
                do_action(btn)
                return True

    else:
        logger.info("         Polkit not in AT-SPI — typing password blind …")
        vk.type_text(password, cpm=1200)
        logger.info("         Password typed ✅")

    # Final Enter
    vk.press_key(evdev.ecodes.KEY_ENTER, delay=0.08)
    logger.info("         Enter pressed ✅")
    return True


# ── Step 6: Wait for install ──────────────────────────────────────────────────

def _step6_wait_install(pkg: str, timeout: int = _INSTALL_TIMEOUT) -> bool:
    logger.info(f"STEP 6 ── Polling snap list for '{pkg}' (max {timeout} s) …")
    deadline = time.time() + timeout
    elapsed  = 0
    while time.time() < deadline:
        if _is_installed(pkg):
            logger.info(f"         ✅ '{pkg}' in snap list! ({elapsed} s)")
            return True
        time.sleep(2)        # poll every 2 s (was 5 s)
        elapsed += 2
        if elapsed % 20 == 0:
            logger.info(f"         … still installing ({elapsed} s) …")
    logger.warning(f"         Timeout {timeout} s — '{pkg}' not yet installed")
    return False


# ── Main execute ──────────────────────────────────────────────────────────────

def setup() -> dict:
    return {}


def execute(args: dict, _resources: dict) -> bool:
    task_name = "atspi_install"
    start(task_name)
    vk = VirtualKeyboard()

    try:
        app_name = str(args.get("app_name", "")).strip()
        pkg      = normalize_package(app_name, str(args.get("package_name", "")))
        password = _load_password(str(args.get("password", "")))

        if not app_name:
            raise ValueError("'app_name' is required")
        if not pkg:
            raise ValueError(f"Could not resolve package name for '{app_name}'")
        if not password:
            logger.warning("No password configured — auth step will use keyboard blind-type. "
                           "Set install.sudo_password in config/config.yaml")

        logger.info("=" * 60)
        logger.info(f"  APP CENTER INSTALLER: '{app_name}'  (pkg: '{pkg}')")
        logger.info("=" * 60)

        # Already installed?
        if _is_installed(pkg):
            logger.info(f"✅ '{pkg}' already installed — done")
            notify(f"Already installed: {app_name}")
            finish("success", task_name)
            return True

        t0 = time.time()

        # Step 1 — Open / reuse App Center
        root = _step1_open()

        # Step 2 — Search
        if root:
            _step2_search(root, app_name, vk)
        else:
            # No AT-SPI root — type into whatever is focused
            _focus_app_center()
            vk.type_text(app_name, cpm=1200)
            vk.press_key(evdev.ecodes.KEY_ENTER, delay=0.08)

        _screenshot("1_search_results")

        # Step 3 — Click result  (event-driven — no fixed sleep)
        _step3_click_result(app_name, vk)
        _screenshot("2_app_detail")

        # Step 4 — Click Install  (event-driven — no fixed sleep)
        _step4_click_install(vk)

        # Step 5 — Authenticate  (event-driven — 8 s max, not 30 s)
        if password:
            _step5_authenticate(password, vk)
        else:
            logger.warning("STEP 5 skipped — no password set")

        _screenshot("3_auth_submitted")
        logger.info(f"         Setup done in {time.time()-t0:.1f} s")

        # Step 6 — Wait for installation
        installed = _step6_wait_install(pkg)

        _screenshot("4_result")

        if installed:
            notify(f"✅ Installed: {app_name}")
            logger.info(f"╚══ ✅ '{app_name}' installed in "
                        f"{time.time()-t0:.0f} s ══╝")
            finish("success", task_name)
            return True
        else:
            notify(f"❌ Install may have failed: {app_name}", critical=True)
            logger.error(f"╚══ ❌ '{pkg}' not in snap list after install flow ══╝")
            finish("error", task_name)
            return False

    except Exception as exc:
        import traceback
        logger.error(f"Install failed: {exc}\n{traceback.format_exc()}")
        finish("error", task_name, err=exc)
        return False

    finally:
        try:
            vk.close()
        except Exception:
            pass


def cleanup(_resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = execute({"app_name": "inkscape"}, {})
    print("Result:", r)
