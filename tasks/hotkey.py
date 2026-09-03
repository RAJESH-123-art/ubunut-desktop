"""
Send keyboard hotkey combos using GUIController.

Decision flow:
  1. Sanity-check: keys string must be non-empty
  2. Split on spaces to get individual key names
  3. Single key  → gui.press(key)
  4. Multiple keys → gui.hotkey(*keys)

Args:
    keys (str): Space-separated key names, e.g. "ctrl c", "alt tab",
                "ctrl alt delete", or a single key like "enter".
                Returns False if no keys are provided.
"""
from loguru import logger

from core.gui_controller import GUIController
from core.logger import finish, notify, start


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "hotkey"
    start(task_name)
    try:
        keys_raw = str(args.get("keys", "")).strip()

        # ── Sanity check ──────────────────────────────────────────────────────
        if not keys_raw:
            logger.warning("'keys' is empty — no hotkey to send")
            finish("error", task_name)
            return False

        parts = keys_raw.split()
        label = " + ".join(parts)
        logger.info(f"Sending hotkey: {label}")

        gui = GUIController()

        if len(parts) == 1:
            gui.press(parts[0])
            logger.info(f"✅ Pressed key: {parts[0]!r}")
        else:
            gui.hotkey(*parts)
            logger.info(f"✅ Hotkey sent: {label}")

        notify(f"Hotkey: {label}")
        finish("success", task_name)
        return True

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"keys": "ctrl c"}, r)
    cleanup(r)
