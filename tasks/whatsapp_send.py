#!/usr/bin/env python3
"""
WhatsApp Web Automation via your REAL local Chrome profile (no QR login needed).
Attaches to Chrome over CDP (remote debugging port 9222).

How it works:
  1. If Chrome isn't running with debugging enabled, relaunch it with
     --remote-debugging-port=9222 using your real profile (~/.config/google-chrome).
  2. Playwright connects over CDP and drives web.whatsapp.com inside your
     actual browser — all your existing logins/cookies are available.

NOTE: Chrome must be fully closed before the first launch with the debug port
(otherwise the new launch joins the existing instance without the port).
"""

import sys
import time
from pathlib import Path

# Add project root directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from core.cdp_browser import CDP_URL, close_cdp_chrome, ensure_chrome_cdp, sync_profile
from core.task_contract import TaskResult


def _arg_str(args: dict[str, object], key: str, default: str = "") -> str:
    """Safely coerce a task arg to a non-None string."""
    value = args.get(key, default)
    return value if isinstance(value, str) else default


def _verify_outgoing_message(
    page,
    expected_text: str = "",
    timeout: float = 8.0,
    minimum_index: int = 0,
) -> dict[str, object] | None:
    """Verify a newly-created outgoing bubble and its bubble-scoped status icon."""
    deadline = time.monotonic() + timeout
    expected = " ".join(expected_text.split()).strip()
    while time.monotonic() < deadline:
        outgoing = page.locator("div.message-out")
        count = outgoing.count()
        lower_bound = max(minimum_index - 1, count - 8, -1)
        for index in range(count - 1, lower_bound, -1):
            bubble = outgoing.nth(index)
            try:
                bubble_text = " ".join((bubble.inner_text() or "").split()).strip()
                if expected:
                    message_texts = {
                        " ".join(text.split()).strip()
                        for text in bubble.locator("span.selectable-text").all_inner_texts()
                    }
                    if expected not in message_texts:
                        continue
                indicator = bubble.locator(
                    'span[data-icon="msg-check"], '
                    'span[data-icon="msg-dblcheck"], '
                    'span[data-icon="msg-dblcheck-ack"]'
                ).last
                if indicator.count() == 0 or not indicator.is_visible(timeout=500):
                    continue
                icon = indicator.get_attribute("data-icon") or "unknown"
                return {
                    "outgoing_text": expected or bubble_text,
                    "delivery_icon": icon,
                    "delivery_confirmed": True,
                }
            except PlaywrightError:
                continue
        time.sleep(0.25)
    return None


def setup() -> dict[str, object]:
    """Setup resources."""
    logger.info("Setting up WhatsApp automation (real Chrome profile via CDP)")
    return {}


def execute(args: dict[str, object], _resources: dict[str, object]) -> TaskResult:
    """
    Send a WhatsApp message using your real logged-in Chrome.

    Args:
        contact (str): Contact or group name to search (e.g. 'darling')
        phone (str, optional): Phone with country code (e.g. '919876543210') — skips search
        message (str): Message text (default 'hi')
    """
    contact = _arg_str(args, "contact").strip()
    phone = _arg_str(args, "phone").strip()
    message = _arg_str(args, "message", "hi").strip()

    if not contact and not phone:
        raise ValueError("Either 'contact' or 'phone' is required")

    if not message:
        raise ValueError("'message' cannot be empty")

    # Sanity check log — confirm what we're about to do before touching the browser
    logger.info("=" * 50)
    logger.info(f"  WhatsApp → to: {contact or f'+{phone}'}")
    logger.info(f"  Message:  {message!r}")
    logger.info("=" * 50)

    _ = ensure_chrome_cdp()
    _ = sync_profile  # imported for parity with the task registry; kept intentionally

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()

        try:
            if phone:
                clean = "".join(filter(str.isdigit, phone))
                logger.info(f"Opening direct chat for +{clean}")
                from urllib.parse import quote as _url_quote
                _ = page.goto(
                    f"https://web.whatsapp.com/send?phone={clean}&text={_url_quote(message, safe='')}",
                              wait_until="domcontentloaded", timeout=60000)
            else:
                logger.info("Opening WhatsApp Web...")
                _ = page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)

            # Wait for the app to load (already logged in via real profile)
            logger.info("Waiting for WhatsApp Web to load (using your logged-in session)...")
            _ = page.wait_for_selector('#side, [data-tab="3"], div[contenteditable="true"]', timeout=45000)
            time.sleep(3)

            # If searching by contact name
            if contact:
                logger.info(f"Searching for contact: '{contact}'")
                search_selectors = [
                    'input[aria-label*="Search" i]',
                    'input[data-tab="3"]',
                    'input[placeholder*="Search" i]',
                    'div[contenteditable="true"][data-tab="3"]',
                    '#side div[contenteditable="true"]',
                ]
                search_box = None
                for sel in search_selectors:
                    try:
                        elem = page.locator(sel).first
                        if elem.is_visible():
                            search_box = elem
                            break
                    except PlaywrightError:
                        continue
                if not search_box:
                    logger.error("Could not find WhatsApp search box")
                    return TaskResult(False, error="Could not find WhatsApp search box")

                _ = search_box.click()
                time.sleep(0.5)
                _ = search_box.fill(contact)
                time.sleep(2)  # let results appear

                # Verify the top result before selecting it. An unreadable or
                # ambiguous recipient fails closed rather than risking a send
                # to the wrong chat.
                result_name = ""
                for result_sel in [
                    '#pane-side span[dir="auto"][title]',
                    'div[aria-label="Search results."] span[title]',
                    '#pane-side [role="listitem"] span[title]',
                ]:
                    try:
                        elem = page.locator(result_sel).first
                        if elem.is_visible(timeout=1500):
                            result_name = (elem.get_attribute("title") or elem.inner_text() or "").strip()
                            if result_name:
                                break
                    except PlaywrightError:
                        continue

                if result_name:
                    matches = (
                        contact.lower() in result_name.lower()
                        or result_name.lower() in contact.lower()
                    )
                    if not matches:
                        logger.error(
                            f"⛔ Search result {result_name!r} does not match requested "
                            f"contact {contact!r} — refusing to send to avoid messaging "
                            f"the wrong person. Try a more specific contact name."
                        )
                        return TaskResult(False, error="Search result did not match requested contact")
                    logger.info(f"✅ Search result {result_name!r} matches requested contact {contact!r}")
                else:
                    logger.error(
                        "Could not read the search result name — refusing to send because "
                        "the recipient cannot be verified."
                    )
                    return TaskResult(False, error="Could not verify search result recipient")

                _ = page.keyboard.press("Enter")
                time.sleep(2)
                selected_chat = ""
                for header_sel in (
                    'header span[title][dir="auto"]',
                    'header [data-testid="conversation-info-header-chat-title"]',
                ):
                    try:
                        header = page.locator(header_sel).first
                        if header.is_visible(timeout=1500):
                            selected_chat = (
                                header.get_attribute("title") or header.inner_text() or ""
                            ).strip()
                            if selected_chat:
                                break
                    except PlaywrightError:
                        continue
                if not selected_chat or not (
                    contact.lower() in selected_chat.lower()
                    or selected_chat.lower() in contact.lower()
                ):
                    logger.error(
                        f"Selected chat {selected_chat!r} does not verify requested contact {contact!r}"
                    )
                    return TaskResult(False, error="Opened chat did not match requested recipient")

            delivery_evidence: dict[str, object] | None = None

            # If an image or media file is provided, send the media file
            media_path = _arg_str(args, "media_path").strip() or _arg_str(args, "image_path").strip()
            if media_path and not Path(media_path).is_file():
                return TaskResult(False, error=f"Requested media file does not exist: {media_path}")
            if media_path:
                logger.info(f"Attaching media file: {media_path}")
                # WhatsApp Web has file input elements (hidden in DOM or attached to clip icon)
                file_input = page.locator('input[type="file"]').first
                try:
                    file_input.set_input_files(media_path)
                except PlaywrightError:
                    # Click attach button first if input element needs activation
                    for attach_sel in ['span[data-icon="plus"]', 'span[data-icon="attach-menu-plus"]', 'button[aria-label*="Attach" i]']:
                        try:
                            page.locator(attach_sel).first.click()
                            time.sleep(1)
                            break
                        except PlaywrightError:
                            continue
                    file_input.set_input_files(media_path)

                time.sleep(2)  # Wait for image preview overlay to load

                # Optional caption message
                if message and message != "hi":
                    try:
                        caption_box = page.locator('div[contenteditable="true"][data-tab="10"], div[contenteditable="true"][aria-label*="caption" i]').first
                        if caption_box.is_visible():
                            caption_box.fill(message)
                            time.sleep(0.5)
                    except PlaywrightError:
                        pass

                # Only bubbles created after this point may prove this send.
                outgoing_before_send = page.locator("div.message-out").count()

                # Click Send button on image preview
                logger.info("Sending attached media...")
                send_btn_selectors = [
                    'span[data-icon="send"]',
                    'div[aria-label="Send"]',
                    'button[aria-label="Send"]',
                    'div[role="button"][aria-label="Send"]',
                ]
                sent = False
                for btn_sel in send_btn_selectors:
                    try:
                        btn = page.locator(btn_sel).first
                        if btn.is_visible():
                            btn.click()
                            sent = True
                            logger.info("Clicked send button on image preview")
                            break
                    except PlaywrightError:
                        continue

                if not sent:
                    logger.info("Pressing Enter to send image preview...")
                    page.keyboard.press("Enter")

                # SAFETY: only press Enter a second time if the preview is
                # STILL visible — previously this pressed Enter again
                # unconditionally "just in case," which risks a stray extra
                # action (e.g. a newline or duplicate send) if the first
                # Enter already worked and the UI moved on to the next
                # state. Checking first makes this a real safety check, not
                # a guess.
                time.sleep(1)
                try:
                    preview_still_open = page.locator(
                        'span[data-icon="send"], div[aria-label="Send"]'
                    ).first.is_visible(timeout=500)
                except PlaywrightError:
                    preview_still_open = False
                if preview_still_open:
                    logger.info("Send preview still visible — pressing Enter again")
                    try:
                        page.keyboard.press("Enter")
                    except PlaywrightError:
                        pass

                logger.info("Waiting for media message delivery evidence...")
                delivery_evidence = _verify_outgoing_message(
                    page,
                    message if message and message != "hi" else "",
                    timeout=10.0,
                    minimum_index=outgoing_before_send,
                )
                if delivery_evidence is None:
                    return TaskResult(
                        False,
                        error="Media send was not confirmed in an outgoing message container",
                    )
            else:
                # Standard text message
                logger.info("Locating message input box...")
                msg_selectors = [
                    'footer div[contenteditable="true"][data-tab="10"]',
                    'footer div[contenteditable="true"]',
                    'div[contenteditable="true"][data-tab="10"]',
                    'div[role="textbox"][aria-label*="message" i]',
                    'div[contenteditable="true"][aria-label*="Type a message"]',
                    'div[contenteditable="true"][data-placeholder*="message" i]',
                ]
                msg_box = None
                for sel in msg_selectors:
                    try:
                        elem = page.locator(sel).first
                        if elem.is_visible():
                            msg_box = elem
                            break
                    except PlaywrightError:
                        continue
                if not msg_box:
                    logger.error("Could not find message input box (chat may not have opened)")
                    return TaskResult(False, error="Could not find message input box")

                logger.info(f"Typing and sending: '{message}'")
                _ = msg_box.click()
                time.sleep(0.3)
                _ = msg_box.fill(message)
                time.sleep(0.5)
                outgoing_before_send = page.locator("div.message-out").count()
                _ = page.keyboard.press("Enter")
                time.sleep(2)

                delivery_evidence = _verify_outgoing_message(
                    page,
                    message,
                    timeout=8.0,
                    minimum_index=outgoing_before_send,
                )
                if delivery_evidence is None:
                    logger.error("Exact outgoing message bubble with delivery indicator was not found")
                    return TaskResult(
                        False,
                        error="Exact sent message and delivery indicator were not confirmed",
                    )
                logger.info(
                    f"✅ Message delivery confirmed ({delivery_evidence['delivery_icon']})"
                )

            # Confirmation screenshot
            ss_dir = Path(__file__).parent.parent / "logs" / "screenshots"
            ss_dir.mkdir(parents=True, exist_ok=True)
            ss_path = ss_dir / f"whatsapp_sent_{int(time.time())}.png"
            _ = page.screenshot(path=str(ss_path))
            recipient = contact or f"+{phone}"
            logger.info(f"✅ WhatsApp action complete to '{recipient}'! Screenshot: {ss_path}")
            return TaskResult(
                True,
                data={
                    "recipient": recipient,
                    "screenshot": str(ss_path),
                    "delivery": delivery_evidence or {},
                },
                evidence=[
                    {"kind": "recipient", "value": recipient},
                    {"kind": "message_delivery", **(delivery_evidence or {})},
                    {"kind": "screenshot", "path": str(ss_path)},
                ],
            )

        except (OSError, PlaywrightError) as exc:
            logger.error(f"WhatsApp automation failed: {exc}")
            return TaskResult(False, error=str(exc))
        finally:
            # Only close the tab we opened — leave the user's browser running
            try:
                _ = page.close()
            except PlaywrightError:
                pass


def cleanup(_resources: dict[str, object]) -> None:
    """Close the automation's CDP Chrome (tabs + window) after the task."""
    _ = close_cdp_chrome()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "darling"
    msg = sys.argv[2] if len(sys.argv) > 2 else "hi"
    _ = execute({"contact": target, "message": msg}, {})
