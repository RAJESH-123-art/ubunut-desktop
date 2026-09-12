"""
Generic browser step runner for sequences of Playwright actions.

Decision flow (Klavaro pattern):
  1. Sanity-check: actions list non-empty, each action has required fields
     → Abort before starting the browser if any action is malformed
  2. Execute actions sequentially
  3. Verify each action succeeded (goto: check URL, click: catch errors)
  4. Report which actions succeeded/failed

JSON structure:
{
  "actions": [
    {"type": "goto",  "url": "https://example.com"},
    {"type": "click", "selector": "button#submit"},
    {"type": "type",  "selector": "input#name", "text": "hello"},
    {"type": "wait",  "seconds": 2},
    {"type": "screenshot", "path": "logs/screenshots/result.png"},
    {"type": "scroll", "direction": "down", "amount_px": 600},
    {"type": "scroll_into_view", "selector": "button#submit"}
  ]
}
"""
import time
from typing import Any

from loguru import logger

from core.browser import brave
from core.logger import finish, notify, start

# Required fields per action type
_REQUIRED: dict[str, list[str]] = {
    "goto":              ["url"],
    "click":             ["selector"],
    "type":              ["selector", "text"],
    "wait":              [],
    "screenshot":        [],
    "scroll":            [],
    "scroll_into_view":  ["selector"],
    "upload":            ["selector", "path"],
    "download":          ["selector", "path"],
    "set_cookie":        ["cookie"],
    "handle_dialog":     ["selector"],
    "wait_for_load":     [],
}
_VALID_TYPES = set(_REQUIRED.keys())


def _validate_actions(actions: list[dict[str, Any]]) -> list[str]:
    """
    Validate all actions before executing any.
    Returns list of error strings (empty = all OK).
    Klavaro lesson: sanity-check ALL data before touching the UI.
    """
    errors = []
    for i, act in enumerate(actions):
        typ = act.get("type", "")
        if typ not in _VALID_TYPES:
            errors.append(
                f"Action {i}: unknown type {typ!r}. Valid: {sorted(_VALID_TYPES)}"
            )
            continue
        for field in _REQUIRED[typ]:
            if not act.get(field):
                errors.append(f"Action {i} ({typ}): missing required field '{field}'")
    return errors


def setup() -> dict:
    """Start Brave via Playwright."""
    b = brave(headless=False)
    b.start()
    time.sleep(2)
    return {"browser": b}


def execute(args: dict, resources: dict) -> bool:
    """Run a list of sequential browser actions."""
    task_name = "browser_action"
    start(task_name)
    try:
        browser = resources["browser"]
        actions: list[dict[str, Any]] = args.get("actions", [])

        # ── Sanity check: validate ALL actions before starting ─────────────────
        if not actions:
            raise ValueError("'actions' list is required and must not be empty")

        errors = _validate_actions(actions)
        if errors:
            raise ValueError(
                "Action validation failed — refusing to execute:\n"
                + "\n".join(f"  • {e}" for e in errors)
            )

        logger.info(f"Executing {len(actions)} browser action(s):")
        for i, act in enumerate(actions):
            logger.info(f"  {i+1}. {act.get('type')} {list(act.items())[1:]}")

        succeeded = failed = 0

        for i, act in enumerate(actions):
            typ = act.get("type")
            try:
                if typ == "goto":
                    url = act["url"]
                    logger.info(f"[{i+1}] goto → {url}")
                    browser.goto(url)
                    # Verify: browser.page.url should now contain the domain
                    current = browser.page.url
                    if not current or current == "about:blank":
                        logger.warning(f"  goto may have failed — current URL: {current}")
                    else:
                        logger.info(f"  ✅ navigated to: {current}")

                elif typ == "click":
                    sel = act["selector"]
                    logger.info(f"[{i+1}] click → {sel}")
                    browser.click(sel)
                    logger.info("  ✅ clicked")

                elif typ == "type":
                    sel  = act["selector"]
                    text = act["text"]
                    logger.info(f"[{i+1}] type → {sel!r} = {text!r}")
                    browser.type(sel, text)
                    logger.info("  ✅ typed")

                elif typ == "wait":
                    secs = float(act.get("seconds", act.get("sec", 2)))
                    logger.info(f"[{i+1}] wait → {secs}s")
                    time.sleep(secs)
                    logger.info(f"  ✅ waited {secs}s")

                elif typ == "screenshot":
                    path = act.get("path", f"logs/screenshots/browser_{int(time.time())}.png")
                    logger.info(f"[{i+1}] screenshot → {path}")
                    browser.screenshot(path)
                    logger.info(f"  ✅ screenshot: {path}")

                elif typ == "scroll":
                    direction = act.get("direction", "down")
                    amount = int(act.get("amount_px", act.get("amount", 600)))
                    sel = act.get("selector")
                    logger.info(f"[{i+1}] scroll → direction={direction} amount_px={amount} selector={sel}")
                    browser.scroll(direction=direction, amount_px=amount, selector=sel)
                    logger.info("  ✅ scrolled")

                elif typ == "scroll_into_view":
                    sel = act["selector"]
                    logger.info(f"[{i+1}] scroll_into_view → {sel}")
                    browser.scroll_into_view(sel)
                    logger.info("  ✅ scrolled into view")


                elif typ == "upload":
                    sel, path = act["selector"], act["path"]
                    browser.upload_file(sel, path)
                    logger.info(f"  ✅ uploaded file: {path}")

                elif typ == "download":
                    sel, path = act["selector"], act["path"]
                    browser.download_file(sel, path)
                    logger.info(f"  ✅ downloaded file: {path}")

                elif typ == "set_cookie":
                    cookie = act["cookie"]
                    browser.set_cookie(cookie)
                    names = {item["name"] for item in browser.get_cookies()}
                    if cookie["name"] not in names:
                        raise RuntimeError(f"Cookie {cookie['name']!r} was not stored")
                    logger.info(f"  ✅ cookie stored: {cookie['name']}")

                elif typ == "handle_dialog":
                    observed = browser.handle_dialog(
                        act["selector"],
                        accept=bool(act.get("accept", True)),
                        prompt_text=act.get("prompt_text"),
                    )
                    logger.info(f"  ✅ handled {observed.get('type')} dialog: {observed.get('message')}")

                elif typ == "wait_for_load":
                    state = act.get("state", "load")
                    browser.wait_for_load_state(state=state)
                    logger.info(f"  ✅ page reached load state: {state}")

                succeeded += 1

            except Exception as exc:
                logger.error(f"  ❌ Action {i+1} ({typ}) failed: {exc}")
                failed += 1
                if args.get("abort_on_error", False):
                    raise RuntimeError(
                        f"Action {i+1} ({typ}) failed and abort_on_error=True: {exc}"
                    ) from exc

        summary = f"Browser actions: {succeeded} succeeded, {failed} failed"
        logger.info(summary)
        notify(summary)
        finish("success" if failed == 0 else "partial", task_name)
        return failed == 0

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    """Close browser."""
    browser = resources.get("browser")
    if browser:
        try:
            browser.close()
        except Exception as exc:
            logger.debug(f"Browser close (non-fatal): {exc}")
    notify("Browser action task completed")


if __name__ == "__main__":
    demo = {
        "actions": [
            {"type": "goto",  "url": "https://github.com"},
            {"type": "wait",  "seconds": 2},
        ]
    }
    r = setup()
    execute(demo, r)
    cleanup(r)
