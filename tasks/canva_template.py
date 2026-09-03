"""
Template design automation: open Canva (or Figma / VistaCréate), search for a
template by type and/or aspect ratio, fill text placeholders, and optionally export.

Args:
    platform (str):      "canva" (default) | "figma" | "vistacreate"
    template (str):      Template search term, e.g. "Instagram Story" (default).
    ratio (str):         Aspect ratio filter, e.g. "4:5", "16:9", "1:1". Canva only.
    placeholders (dict): {"texts": [{"selector": "...", "value": "..."}]}
    export (bool):       Click the Download/Share button after editing (default False).
    screenshot (bool):   Save a screenshot of the final state (default True).
"""
import time

from loguru import logger

from core.app_registry import DESIGN_URLS
from core.logger import finish, notify, start


def setup() -> dict:
    from core.browser_manager import setup_shared_resources
    return setup_shared_resources(create_browser=True)


def execute(args: dict, resources: dict) -> bool:
    """Open a design platform, pick a template, fill placeholders, and export."""
    task_name = "canva_template"
    start(task_name)
    try:
        browser       = resources["browser"]
        platform      = args.get("platform", "canva").lower()
        template_type = args.get("template", "Instagram Story")
        ratio         = args.get("ratio", "")
        placeholders  = args.get("placeholders", {})

        # ── Sanity check ──────────────────────────────────────────────────────
        valid_platforms = {"canva", "figma", "vistacreate"}
        if platform not in valid_platforms:
            raise ValueError(
                f"Unknown platform {platform!r}. Valid: {sorted(valid_platforms)}"
            )
        if not template_type.strip():
            raise ValueError("'template' search term cannot be empty")

        logger.info("=" * 50)
        logger.info(f"  Platform:  {platform}")
        logger.info(f"  Template:  {template_type!r}")
        logger.info(f"  Ratio:     {ratio or 'any'}")
        logger.info("=" * 50)

        # Step-tracking: every meaningful step records its own outcome here so
        # the final status/return value reflects what actually happened,
        # instead of reporting "success" just because nothing raised.
        failed_steps: list[str] = []
        search_ok        = False
        template_opened  = False
        ratio_requested  = bool(platform == "canva" and ratio)
        ratio_ok         = False
        export_requested = bool(args.get("export", False))
        export_ok        = False
        placeholder_items = placeholders.get("texts", [])
        placeholder_fail_count = 0

        base_url = DESIGN_URLS.get(platform, DESIGN_URLS["canva"])
        logger.info(f"Opening {platform} → {base_url}")
        browser.goto(base_url)

        # ── Search for template ───────────────────────────────────────────────
        search_sel = (
            '[data-testid="SearchInput"], '
            'input[placeholder*="search" i], '
            'input[placeholder*="Search"], '
            '[aria-label*="search" i]'
        )
        try:
            # No preceding blind sleep needed here: this wait_for already
            # covers the page's initial load time (up to 8s) by polling real
            # DOM state instead of guessing a fixed duration.
            browser.wait_for(search_sel, timeout_ms=8_000)
            browser.type(search_sel, template_type)
            search_ok = True
            logger.info(f"Searched for template: {template_type!r}")
        except Exception as exc:
            logger.warning(f"Search input not found on {platform}: {exc}")
            failed_steps.append("search")
            # Direct URL fallback for Canva
            if platform == "canva":
                slug = template_type.lower().replace(" ", "-")
                browser.goto(f"https://www.canva.com/templates/{slug}/")
                # No blind sleep here either — the template-click loop below
                # waits on each candidate selector before clicking it.

        # ── Click first template result ───────────────────────────────────────
        for sel in [
            'a[href*="/templates/"]',
            '[class*="template"]',
            'div[role="link"]',
            'a[data-testid*="template"]',
        ]:
            try:
                browser.wait_for(sel, timeout_ms=3_000)
                browser.click(sel)
                logger.info(f"Template selected via selector: {sel!r}")
                template_opened = True
                # This is the riskiest wait in the file: the Canva editor's
                # boot time is highly variable. `[data-testid="editable-element"]`
                # is Canva's real attribute for an editable canvas node — it's
                # also the selector this file's own demo args (bottom of file)
                # target for placeholder fills, so its appearance is a
                # reasonably confident, non-invented signal the editor has
                # finished booting, rather than guessing a fixed duration.
                try:
                    browser.wait_for('[data-testid="editable-element"]', timeout_ms=8_000)
                    logger.info("Editor ready: editable element detected")
                except Exception:
                    # Some templates may not use that exact test id, or the
                    # editor may still be settling — fall back to a bounded
                    # blind wait rather than failing the whole task over it.
                    logger.debug("Editable-element not seen in time; bounded fallback wait")
                    time.sleep(3)
                break
            except Exception as exc:
                logger.debug(f"Template selector {sel!r} failed: {exc}")
                continue

        if not template_opened:
            failed_steps.append("template_open")

        # ── Aspect ratio filter (Canva-specific) ──────────────────────────────
        if ratio_requested:
            ratio_class = ratio.replace(":", "-")
            for sel in [
                f'[aria-label*="{ratio}"]',
                f'[data-testid*="{ratio}"]',
                f'[class*="{ratio_class}"]',
            ]:
                try:
                    browser.wait_for(sel, timeout_ms=2_000)
                    browser.click(sel)
                    # Canva has no documented selector confirming the results
                    # grid has finished re-rendering after a filter click, so
                    # this stays a bounded blind wait rather than an invented
                    # selector we're not confident about.
                    time.sleep(2)
                    logger.info(f"Applied ratio filter: {ratio}")
                    ratio_ok = True
                    break
                except Exception as exc:
                    logger.debug(f"Ratio selector {sel!r} failed: {exc}")
                    continue
            if not ratio_ok:
                failed_steps.append("ratio_filter")

        # ── Fill text placeholders ────────────────────────────────────────────
        for item in placeholder_items:
            sel = item.get("selector", "")
            val = item.get("value", "")
            if sel and val:
                try:
                    browser.wait_for(sel, timeout_ms=3_000)
                    browser.type(sel, val)
                    logger.info(f"Filled placeholder {sel!r} with {val!r}")
                except Exception as exc:
                    logger.warning(f"Placeholder fill failed {sel!r}: {exc}")
                    placeholder_fail_count += 1
                    failed_steps.append(f"placeholder:{sel}")

        # ── Export / download ─────────────────────────────────────────────────
        if export_requested:
            export_sel = (
                'button[aria-label*="Download" i], '
                'button[data-testid*="share"], '
                '[aria-label*="share" i]'
            )
            try:
                browser.wait_for(export_sel, timeout_ms=5_000)
                browser.click(export_sel)
                # Browser-native download dialogs aren't part of the page DOM,
                # so there's no reliable selector to confirm export actually
                # started — this remains a bounded blind wait.
                time.sleep(2)
                logger.info("Export triggered")
                export_ok = True
            except Exception as exc:
                logger.warning(f"Export step failed: {exc}")
                failed_steps.append("export")

        # ── Screenshot ────────────────────────────────────────────────────────
        ss_path = None
        if args.get("screenshot", True):
            ss_path = f"logs/screenshots/{platform}_template_{int(time.time())}.png"
            browser.screenshot(ss_path)
            logger.info(f"Screenshot saved: {ss_path}")

        # ── Determine the real outcome ────────────────────────────────────────
        # The core goal of this task is "find and open a template". Nothing
        # downstream (ratio filter, placeholder fills, export) matters if that
        # didn't happen, so it's the hard gate for True/"success"/"partial" vs
        # False/"error".
        placeholder_total = len(placeholder_items)
        if not template_opened:
            msg = (
                f"{platform.capitalize()} template task FAILED — could not open any "
                f"template for search {template_type!r} (failed steps: {failed_steps})"
            )
            logger.error(msg)
            notify(msg, critical=True)
            finish("error", task_name)
            return False

        issues = []
        if not search_ok:
            issues.append("search step failed (used slug-URL fallback instead)")
        if placeholder_fail_count:
            issues.append(
                f"{placeholder_fail_count} of {placeholder_total} placeholders could not be filled"
            )
        if ratio_requested and not ratio_ok:
            issues.append(f"ratio filter {ratio!r} could not be applied")
        if export_requested and not export_ok:
            issues.append("export/download step failed")

        if issues:
            status  = "partial"
            summary = f"{platform.capitalize()} template opened, but " + "; ".join(issues)
            logger.warning(summary)
        else:
            status  = "success"
            summary = f"{platform.capitalize()} template opened and all requested steps completed"
            logger.info(summary)

        if ss_path:
            summary += f" — {ss_path}"
        notify(summary)

        finish(status, task_name)
        return True

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    from core.browser_manager import cleanup_shared_resources
    cleanup_shared_resources(resources)
    notify("Template task completed")


if __name__ == "__main__":
    demo_args = {
        "platform":    "canva",
        "template":    "Instagram Story",
        "ratio":       "4:5",
        "export":      False,
        "screenshot":  True,
        "placeholders": {
            "texts": [
                {"selector": '[data-testid="editable-element"]:first-child', "value": "Hello World"},
            ]
        },
    }
    r = setup()
    execute(demo_args, r)
    cleanup(r)
