"""
Type text using the keyboard via GUIController.

Decision flow:
  1. Sanity-check: text must be non-empty
  2. If app_name is given, best-effort focus that app first -- WITHOUT this,
     keystrokes go to whatever window currently has focus, which has been
     confirmed live to silently land in the wrong window (e.g. a terminal
     or editor instead of the intended target app). See
     core/action_loop.py's _ensure_focused() for the same fix applied there.
  3. Instantiate GUIController
  4. Call type_text(text)
  5. Confirm success

Args:
    text (str): The text to type. Required — returns False if empty.
    app_name (str, optional): App to focus before typing (e.g. "calculator"
        from "type '12*7=' into calculator"). Best-effort; typing still
        proceeds even if the app can't be found/focused.
"""
from loguru import logger

from core.gui_controller import GUIController
from core.logger import finish, notify, start


def setup() -> dict:
    return {}


def _try_focus(app_name: str) -> bool:
    """Best-effort: bring `app_name` to foreground. Never raises."""
    if not app_name:
        return False
    try:
        from core.atspi_navigator import _try_focus_app, wait_for_app
        app = wait_for_app([app_name], timeout=2.0)
        if app is None:
            logger.warning(f"type_text: app {app_name!r} not found/running — typing into current focus instead")
            return False
        focused = _try_focus_app(app)
        logger.info(f"type_text: focus {app_name!r} {'succeeded' if focused else 'failed'}")
        return focused
    except Exception as exc:
        logger.debug(f"type_text: focus attempt for {app_name!r} raised: {exc}")
        return False


def execute(args: dict, resources: dict) -> bool:
    task_name = "type_text"
    start(task_name)
    try:
        text = str(args.get("text", ""))
        app_name = str(args.get("app_name", "")).strip()

        # ── Sanity check ──────────────────────────────────────────────────────
        if not text:
            logger.warning("'text' is empty — nothing to type")
            finish("error", task_name)
            return False

        if app_name and not _try_focus(app_name):
            logger.error(f"Refusing to type because target app {app_name!r} could not be focused")
            finish("error", task_name)
            return False

        logger.info(f"Typing {len(text)} character(s)…")

        gui = GUIController()
        gui.type_text(text)

        logger.info(f"✅ Typed: {text!r}")
        notify(f"Typed text ({len(text)} chars)")
        finish("success", task_name)
        return True

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"text": "Hello, world!"}, r)
    cleanup(r)
