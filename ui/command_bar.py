#!/usr/bin/env python3
"""
Command Bar — a lightweight, always-available GTK popup bound to a global
hotkey (Ctrl+Alt+A, see install_desktop_integration.sh).

This upgrades the plain `zenity --entry` popup in scripts/agent_prompt.sh
(still kept as a minimal fallback) to satisfy the UX principles distilled
from Vercept Vy's public materials (see VERCEPT_LEVEL_ROADMAP.md and
https://similarlabs.com/p/vy-macos-ai-assistant):

  - Zero-friction, always-available entry point (one hotkey, one text field).
  - Live status while a command runs — stdout/stderr streamed into the
    window — instead of a silent black box until a final notification.
  - A recent-commands dropdown, so repeated tasks don't need to be retyped.
  - An explicit Yes/No confirmation gate before executing anything parsed
    into a "consequential" intent (send a message, delete a file, install
    software, power off/restart, or run a raw shell command) — mirroring
    Vy's consent protocol for real-world actions.
  - Click-away or Escape dismisses instantly with no side effects.

This is a UI layer only — it does not duplicate or bypass any parsing/
execution/safety logic. It calls straight into agent.run_command(), the
same single pipeline `python agent.py "<command>"` and ui/assistant.py use.

Usage:
    python3 ui/command_bar.py       # opens the popup directly
    scripts/command_bar.sh          # thin launcher — bind THIS to a hotkey

Note (Wayland): GTK cannot force absolute window position or "always on
top" under native Wayland (no such protocol) — the compositor decides
placement. The window still opens, focuses, and works correctly; it just
may not be pixel-centered the way it would be under X11.
"""
import contextlib
import io
import queue
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")  # must be pinned explicitly, or gi silently
# picks the newest installed Gdk (4.0 on systems with both toolkits present)
# and then crashes when Gtk 3.0 tries to require Gdk 3.0 for itself.
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

import agent  # noqa: E402  (import triggers agent._load_secrets_env())
from core.action_policy import (  # noqa: E402
    action_token,
    describe_action,
    requires_approval,
)
from core.llm_planner import (  # noqa: E402
    is_confidently_resolvable_open_app,
    llm_planner,
    looks_unreliable,
)
from core.memory import memory  # noqa: E402
from core.smart_parser import smart_parser  # noqa: E402
from core.structured_automation import StructuredPlan, describe_plan  # noqa: E402

HISTORY_NAMESPACE = "command_bar"
HISTORY_KEY = "history"
MAX_HISTORY = 20


def _load_history() -> list[str]:
    hist = memory.get(HISTORY_NAMESPACE, HISTORY_KEY)
    return list(hist) if isinstance(hist, list) else []


def _save_history(commands: list[str]) -> None:
    memory.set(HISTORY_NAMESPACE, HISTORY_KEY, commands[-MAX_HISTORY:])


class _QueueWriter(io.TextIOBase):
    """File-like object that pushes each write onto a thread-safe queue."""

    def __init__(self, q: "queue.Queue[str]") -> None:
        self._q = q

    def write(self, s: str) -> int:
        if s:
            self._q.put(s)
        return len(s)

    def flush(self) -> None:  # pragma: no cover - nothing to flush
        pass


class CommandBar(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="Desktop Agent")
        self.set_default_size(560, 90)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_resizable(False)
        self.get_style_context().add_class("command-bar")
        self._apply_css()

        self._history: list[str] = _load_history()
        self._running = False
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        # Guards against a spurious focus-out firing immediately after the
        # window first maps (observed on GNOME/Wayland) which would
        # otherwise self-close the popup before the user gets a chance to
        # type anything. Click-away-to-dismiss only arms after this delay.
        self._dismiss_armed = False
        GLib.timeout_add(400, self._arm_dismiss)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_border_width(14)
        self.add(outer)

        # ── Entry row ────────────────────────────────────────────────
        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        outer.pack_start(entry_row, False, False, 0)

        icon = Gtk.Label(label="\u26a1")
        entry_row.pack_start(icon, False, False, 0)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("What should I do?")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self._on_submit)
        entry_row.pack_start(self.entry, True, True, 0)

        self.history_combo = Gtk.ComboBoxText()
        entry_row.pack_start(self.history_combo, False, False, 0)
        self._refresh_history_combo()
        self.history_combo.connect("changed", self._on_history_pick)

        # ── Status line ──────────────────────────────────────────────
        self.status_label = Gtk.Label(label="")
        self.status_label.set_xalign(0)
        self.status_label.set_line_wrap(True)
        outer.pack_start(self.status_label, False, False, 0)

        # ── Live log view (hidden until a command is running) ────────
        self.log_buffer = Gtk.TextBuffer()
        self.log_view = Gtk.TextView(buffer=self.log_buffer)
        self.log_view.set_editable(False)
        self.log_view.set_cursor_visible(False)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_size_request(-1, 220)
        scroller.add(self.log_view)
        scroller.set_no_show_all(True)
        outer.pack_start(scroller, True, True, 0)
        self.log_scroller = scroller

        self.connect("key-press-event", self._on_key_press)
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("destroy", lambda *_: Gtk.main_quit())

    # ── Setup helpers ────────────────────────────────────────────────
    def _apply_css(self) -> None:
        css = b".command-bar { background-color: @theme_bg_color; border-radius: 10px; }"
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _refresh_history_combo(self) -> None:
        self.history_combo.remove_all()
        self.history_combo.append_text("Recent…" if self._history else "No recent commands")
        for cmd in reversed(self._history):
            self.history_combo.append_text(cmd)
        self.history_combo.set_active(0)

    # ── Event handlers ───────────────────────────────────────────────
    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if self._running:
                self.status_label.set_text("A command is still running; wait for it to finish before closing.")
                return True
            Gtk.main_quit()
            return True
        return False

    def _arm_dismiss(self) -> bool:
        self._dismiss_armed = True
        return False  # one-shot

    def _on_focus_out(self, *_args) -> bool:
        # Click-away dismiss, but never abandon a run that's already started,
        # and never fire before _arm_dismiss (see __init__ for why).
        if self._dismiss_armed and not self._running:
            Gtk.main_quit()
        return False

    def _on_history_pick(self, combo: Gtk.ComboBoxText) -> None:
        text = combo.get_active_text()
        if text and text not in ("Recent…", "No recent commands"):
            self.entry.set_text(text)
            self.entry.grab_focus()
            self.entry.set_position(-1)

    def _on_submit(self, _entry: Gtk.Entry) -> None:
        if self._running:
            return
        raw = self.entry.get_text().strip()
        if not raw:
            return

        intents = smart_parser.parse_multi(raw)
        fallback_params = {"raw_command": raw}
        needs_fallback_approval = not intents or (
            looks_unreliable(intents, raw) and not is_confidently_resolvable_open_app(intents)
        )
        # Complex goals are confirmed after the complete immutable plan exists,
        # not against an ambiguous parser guess or generic fallback token.
        defer_to_structured_plan = needs_fallback_approval and llm_planner.available()
        consequential = [] if defer_to_structured_plan else [
            i for i in intents if requires_approval(i.intent, i.params)
        ]
        approved_actions: set[str] = set()
        if consequential:
            summary_lines = [
                f"\u2022 {describe_action(i.intent, i.params)} — params: {i.params}"
                for i in consequential
            ]
            if needs_fallback_approval and not defer_to_structured_plan:
                summary_lines.append(f"\u2022 {describe_action('universal_fallback')} — command: {raw}")
            summary = "\n".join(summary_lines)
            if not self._confirm(summary):
                self.status_label.set_text("Cancelled.")
                return
            approved_actions = {action_token(i.intent, i.params) for i in consequential}
            if needs_fallback_approval and not defer_to_structured_plan:
                approved_actions.add(action_token("universal_fallback", fallback_params))
        elif needs_fallback_approval and not defer_to_structured_plan:
            summary = f"\u2022 {describe_action('universal_fallback')} — command: {raw}"
            if not self._confirm(summary):
                self.status_label.set_text("Cancelled.")
                return
            approved_actions.add(action_token("universal_fallback", fallback_params))

        self._start_run(raw, approved_actions)

    def _confirm(self, summary: str) -> bool:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text="This will perform a real action:",
        )
        dialog.format_secondary_text(summary)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.YES

    # ── Execution ────────────────────────────────────────────────────
    def _start_run(self, raw: str, approved_actions: set[str]) -> None:
        self._running = True
        self.entry.set_sensitive(False)
        self.history_combo.set_sensitive(False)
        self.status_label.set_text(f"Running: {raw}")
        self.log_buffer.set_text("")
        self.log_scroller.set_no_show_all(False)
        self.log_scroller.show_all()
        self.resize(560, 320)

        self._history = [c for c in self._history if c != raw] + [raw]
        _save_history(self._history)
        self._refresh_history_combo()

        threading.Thread(target=self._run_worker, args=(raw, approved_actions), daemon=False).start()
        GLib.timeout_add(80, self._drain_log)

    def _approve_structured_plan(self, plan: StructuredPlan) -> bool:
        """Synchronously ask on GTK's main thread while execution waits."""
        finished = threading.Event()
        decision = {"approved": False}

        def show_dialog() -> bool:
            decision["approved"] = self._confirm(describe_plan(plan))
            finished.set()
            return False

        GLib.idle_add(show_dialog)
        if not finished.wait(timeout=300):
            self._log_queue.put("\nPlan approval timed out.\n")
            return False
        return decision["approved"]

    def _run_worker(self, raw: str, approved_actions: set[str]) -> None:
        writer = _QueueWriter(self._log_queue)
        ok = False
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                ok = agent.run_command(
                    raw,
                    approved_actions=approved_actions,
                    structured_plan_approver=self._approve_structured_plan,
                )
        except Exception as exc:  # noqa: BLE001 - surface to the UI, never crash silently
            self._log_queue.put(f"\nEXCEPTION: {exc}\n")
        finally:
            self._log_queue.put(f"\n__DONE__{'1' if ok else '0'}\n")

    def _drain_log(self) -> bool:
        updated = False
        try:
            while True:
                chunk = self._log_queue.get_nowait()
                if chunk.startswith("\n__DONE__"):
                    self._finish_run(ok=chunk.strip().endswith("1"))
                    return False
                self.log_buffer.insert(self.log_buffer.get_end_iter(), chunk)
                updated = True
        except queue.Empty:
            pass
        if updated:
            self.log_view.scroll_to_iter(self.log_buffer.get_end_iter(), 0.0, False, 0.0, 0.0)
        return self._running

    def _finish_run(self, ok: bool) -> None:
        self._running = False
        self.entry.set_sensitive(True)
        self.history_combo.set_sensitive(True)
        self.entry.set_text("")
        self.status_label.set_text("\u2705 Done" if ok else "\u274c Failed — see log above")
        self.entry.grab_focus()


def main() -> None:
    win = CommandBar()
    win.show_all()
    win.log_scroller.hide()
    win.entry.grab_focus()
    Gtk.main()


if __name__ == "__main__":
    main()
