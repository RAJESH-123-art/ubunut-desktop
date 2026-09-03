"""
Type text using the keyboard via GUIController.

Decision flow:
  1. Sanity-check: text must be non-empty
  2. Instantiate GUIController
  3. Call type_text(text)
  4. Confirm success

Args:
    text (str): The text to type. Required — returns False if empty.
"""
from loguru import logger

from core.gui_controller import GUIController
from core.logger import finish, notify, start


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "type_text"
    start(task_name)
    try:
        text = str(args.get("text", ""))

        # ── Sanity check ──────────────────────────────────────────────────────
        if not text:
            logger.warning("'text' is empty — nothing to type")
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
