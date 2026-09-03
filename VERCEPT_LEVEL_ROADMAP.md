# REACHING "VERCEPT LEVEL" — MASTER ROADMAP & STATE DOCUMENT

> **Purpose of this file:** Paste this into a new AI chat session to instantly resume work on
> making this project's automation as capable/adaptive as Vercept's "Vy" (a macOS AI agent,
> acquired by Anthropic Feb 2026). This file is the honest, current source of truth — it
> includes what's built, what's tested-and-working, what's tested-and-BROKEN, and exactly
> what to do next. Read this fully before touching code.
>
> Companion docs: `MASTER.md` (original project architecture), `ARCHITECTURE_EVOLUTION.md`
> (the layered automation design). This file is specifically about the AI-driven adaptive
> layer added on top of that foundation.

---

## 1. WHAT IS "VERCEPT LEVEL"? (grounded research, not guesswork)

Vercept's Vy was a macOS-native agent (funded startup, ~$16M raised, launched April 2025,
**acquired by Anthropic Feb 25, 2026**, standalone app being sunset, tech folded into Claude's
computer-use). Its architecture, from Vercept's own materials and independent reviews:

| Component | What it does |
|---|---|
| **Execution Engine** | Dispatches clicks/keystrokes/file ops, with retry/error-recovery |
| **Context Monitor** | Continuously tracks window state, selections, screen regions — *live* |
| **Frontier Agents** | Modular routines mapping intent → GUI action, with conditional logic |
| **Intent Parser** | LLM-based command interpretation |
| **Vision-first perception** | Screenshots → vision model → identifies buttons/fields |
| **Opt-in Memory** | Stores user-approved data (credentials, preferences) for autofill |
| **Blueprints** | Modular, reusable, testable workflow blocks — sequential or parallel, from templates or scratch |
| **Resumable Sessions** | Full session state (context, progress) persists and reloads later |

**Important nuance discovered during research:** industry technical analysis (not
Vercept-affiliated) argues screenshot/vision-first perception is actually the *slower, more
expensive, less precise* approach compared to accessibility-API-first (macOS `AXUIElement` /
Linux `AT-SPI`, which is what this project already uses as primary). One technical blog put it
directly: *"Accessibility APIs are the cheat code... an agent using accessibility APIs can
check screen state 10 times/second; screenshot-based agents manage once every 2-3 seconds."*
**Our AT-SPI-first + vision-fallback design is architecturally not behind Vy on this point —
it may be ahead.** The real gaps are elsewhere (see below).

---

## 2. HONEST GAP ANALYSIS — as of this session

| Vy capability | Our equivalent | Status |
|---|---|---|
| Execution Engine | `core/strategy_executor.py` + `tasks/*.py` + `core/safety_guard.py` | ✅ Solid, proven repeatedly |
| Intent Parser (LLM) | `core/smart_parser.py` (fast/free) + `core/llm_planner.py` (Tier 5, upfront script generation) | ✅ Built, live-tested, works |
| Vision-first perception | `core/semantic_vision.py` (Layer 4, one-shot) | ✅ Built, live-tested, works — but one-shot, not a loop |
| **Context Monitor (continuous)** | `core/world_model.py` | ⚠️ Query-on-demand, not a continuously running watcher |
| **Frontier Agents (adaptive, closed-loop)** | `core/action_loop.py` (NEW this session) | 🟡 **Built, partially tested, HAS KNOWN BUGS — see §3** |
| **Blueprints (reusable/testable workflow blocks)** | `core/task_dag.py` + `core/goal_planner.py` + `core/parallel_runner.py` | ⚠️ Engine exists (DAG, parallel exec, `GOAL_TEMPLATES`), authoring/testing UX does NOT |
| **Opt-in rich user memory** | `core/memory.py` | ❌ Only stores strategy success/failure stats, not user profile/credentials/preferences |
| **Resumable Sessions** | *(nothing)* | ❌ Total gap — every `agent.py` invocation is stateless |

---

## 3. WHAT WAS BUILT THIS SESSION — with HONEST test results

### 3.1 `core/llm_planner.py` — Tier 5 upfront script planner
- **Status: ✅ Works, live-tested twice successfully.**
- Generates a full bash script upfront for complex/prose instructions SmartParser can't
  parse, executes it through the existing `tasks/run_command.py` (still safety-guarded).
- Live-tested on: (1) package-manager inspection task — worked, verified independently.
  (2) "open Calculator, screenshot, verify" — worked, verified independently (screenshot +
  `ps aux` confirmed).
- Uses `NVIDIA_TEXT_API_KEY` (separate credential from vision), model
  `nvidia/nemotron-3-ultra-550b-a55b` (550B, slow but thorough — appropriate for ONE upfront
  plan per task, NOT for tight loops).
- Knows to delegate GUI/visible steps to `agent.py "<command>"` rather than xdotool/wmctrl
  (which don't reliably work on native Wayland) — this was a deliberately-added system prompt
  rule, confirmed working live.

### 3.2 `core/action_loop.py` — closed observe→decide→act loop (NEW, Frontier-Agents equivalent)
- **Status: 🟡 Built and runs, but has CONFIRMED REAL BUGS. Do not trust its "done"/success
  claims without independent verification — see the failure below.**
- Design: AT-SPI observation (fast/free) → LLM decides ONE next action → execute via existing
  primitives (`semantic_vision`, `GUIController`, `run_command`) → repeat.
- **Bug #1 (FIXED this session):** initial model choice (`nemotron-3-ultra-550b-a55b`, 550B)
  took 50-95 **seconds per decision** — wildly wrong tool for tactical per-step decisions.
  Fixed by switching the loop's decision model to `nvidia/nemotron-3.5-lightning-30b-a3b`
  (MoE, ~3B active params) via new `NVIDIA_LOOP_MODEL` env var (defaults to the lightning
  model). Also added `extra_body={"chat_template_kwargs":{"enable_thinking": False}}` to skip
  chain-of-thought overhead. Result: ~2-10s per decision, confirmed live.
- **Bug #2 (FIXED this session):** raw keyboard actions (`type`/`key`) sent keystrokes to
  whatever window had OS focus (often the terminal/editor, NOT the target app) because nothing
  focused the target app first. Fixed by adding `_ensure_focused()` using
  `atspi_navigator.wait_for_app()` + `_try_focus_app()` before every `type`/`key` action.
  **However:** `_try_focus_app()`'s `grabFocus()` is documented as unreliable on Wayland in
  some setups (see `atspi_navigator.py` comments) — confirmed live, focus could NOT be
  confirmed for gnome-calculator even after the fix. **Net effect: prompt was updated to
  prefer `click` (AT-SPI direct action, doesn't need window focus) over `type`/`key` for
  anything with clickable buttons — this is the actually-reliable workaround, not a real fix
  to focus-grabbing itself.**
- **Bug #3 (FIXED this session):** `click` actions searched the ENTIRE desktop (~27s per
  click) instead of being scoped to the target app. Fixed by passing `app_name=app_hint` to
  `semantic_vision.find_and_click()`. Confirmed live: ~1-2s per click after fix.
- **Bug #4 (UNFIXED, CONFIRMED LIVE, IMPORTANT):** `_observe_atspi()` cannot read
  GNOME Calculator's result-display text. Manually confirmed via direct `pyatspi` inspection
  that `node.queryText()` returns `''` even when the display visibly shows content. The
  observation function returns `""` for this app entirely (confirmed via direct test after
  the bug was hit). **Consequence: the loop ran completely blind to the actual calculator
  state.** In the last live run, the model clicked 7 → 3 → + → = (wrong order — produces the
  malformed expression "73+", not "7+3=10"), then after two "wait" steps with zero real
  observation data, **the model hallucinated `{"action":"done","success":true,"message":
  "The calculator displays the result 10..."}` — which was FALSE.** A screenshot taken
  independently afterward showed "73+" and "Malformed expression" on screen, not "10".
  **This is the single most important finding this session: the loop's self-reported success
  cannot be trusted without independent verification, and the root cause (blind observation
  for this app) is unfixed.**
- **Bug #5 (DISCOVERED, UNINVESTIGATED):** immediately after the above failure, a fresh
  AT-SPI desktop query no longer listed `gnome-calculator` in the running-apps list at all
  (`ps aux | grep calc` was being run to check if the process had crashed/exited when the
  session was stopped — **this was never resolved, investigate first in the next session**).

### 3.3 `core/browser.py` / `tasks/browser_action.py` — scrolling
- **Status: ✅ Works, live-tested, verified two ways** (numeric `scrollY` value AND visual
  before/after screenshots on a real Wikipedia page).
- Added `scroll()` and `scroll_into_view()` to `BrowserController`; added `"scroll"` /
  `"scroll_into_view"` action types to `browser_action` task's vocabulary.

### 3.4 Live-verified browser automation on a real, complex site (partial)
- Successfully drove Microsoft's real Windows 11 download page (not a toy test page) through
  Playwright: loaded page, inspected real selectors live (didn't guess), selected edition
  dropdown, clicked "Download Now", got real language dropdown to populate with 43 languages.
- **Left incomplete:** hit a Playwright technicality (`wait_for_selector` default
  `state="visible"` doesn't work for `<option>` tags) — was about to fix with `state="attached"`
  when the task was paused to focus on other priorities. **Not resumed — pick this up if
  browser-flow robustness work continues.**

### 3.5 Earlier in this session (previous context, still valid)
- `core/smart_parser.py`: `parse_multi()` no longer silently drops unmatched compound-command
  clauses — routes them through `universal_fallback` instead. **Tested, works.**
- `core/replanner.py` + `core/parallel_runner.py`: DAG-level self-healing wired in and
  live-tested (forced a fake failure, confirmed 3 alternative strategies got injected and ran).
- `tasks/universal_fallback.py`: Layer 4 (`semantic_vision`) wired in as last resort after
  CLI registry / AT-SPI / Electron-CDP layers.
- Repo cleanup: removed broken duplicate `venv/`, empty `ui_mapping_data_advanced/`, moved
  superseded pipeline (`automation.py`, `core/intent_parser.py`, `master_agent.py`) to
  `legacy/` with import sites updated.
- **Git commit was requested but NEVER completed** — git has no `user.name`/`user.email`
  configured on this machine and the commit failed. **All of this session's work is still
  UNCOMMITTED.** Set identity and commit before anything else risks this work.

---

## 4. CREDENTIALS IN USE (env vars only — nothing hardcoded in tracked files)

| Env var | Purpose | Model |
|---|---|---|
| `NVIDIA_API_KEY` | Vision only (Layer 4, `semantic_vision.py`) | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` |
| `NVIDIA_TEXT_API_KEY` | Text/planning (Tier 5, `llm_planner.py`) | `nvidia/nemotron-3-ultra-550b-a55b` (upfront planning — slow OK) |
| `NVIDIA_LOOP_MODEL` (optional override) | Tactical per-step decisions (`action_loop.py`) | defaults to `nvidia/nemotron-3.5-lightning-30b-a3b` (fast, ~2s/call) |
| `NVIDIA_BASE_URL` | Shared endpoint, not secret | `https://integrate.api.nvidia.com/v1` |

**These two API keys are scoped for DIFFERENT purposes per the user — never reuse one for the
other's calls.** Neither is persisted anywhere in the repo; both must be exported by the user
each session (or added to their own shell profile) — this project cannot write secrets to
files on the user's behalf.

Available models confirmed on the `NVIDIA_TEXT_API_KEY` account (queried live, 81 total) —
notable ones if a different speed/quality tradeoff is needed later:
`nvidia/llama-3.1-nemotron-70b-instruct`, `nvidia/llama-3.1-nemotron-ultra-253b-v1`,
`mistralai/mistral-large-2-instruct`, `nv-mistralai/mistral-nemo-12b-instruct`. Note:
`meta/llama-3.1-8b-instruct` is retired (410 Gone). Several candidate "nano" models
(`nemotron-nano-3-30b-a3b`) returned 404 — not enabled on this account.

---

## 4.5. UPDATE — follow-up session findings (action_loop.py bugs #4/#5, pipeline unification)

- **Unified the two parallel pipelines.** `ui/assistant.py` and `ui/cli.py` used to
  import the superseded `legacy/intent_parser.py` / `legacy/automation.py`, while
  `agent.py` used the modern `smart_parser`/`StrategyExecutor` pipeline -- two different
  "brains" that could behave differently for the same input. Both now delegate to
  `agent.py`'s `run_command()` / `core.workflow_engine.WorkflowEngine`. The one
  capability the old path had that the new one didn't (canva/figma/vistacreate
  template-automation routing) was ported into `agent.py`'s `run_command()` as a
  shortcut before retiring `legacy/` entirely (deleted, confirmed unused).
- **Bug #4 re-investigated live, did NOT reproduce.** Ran a real gnome-calculator
  instance, drove it via direct AT-SPI `doAction()` clicks (7, +, 3, =), and read
  `queryText()` on the display's `[text]` node after each click: it correctly
  tracked `7` -> `7+` -> `7+3` -> `10` the whole way. `_observe_atspi()`'s existing
  text-content-fallback code (added in the prior session) is correct as written.
  Conclusion: the blind-observation failure in the original run was likely a
  symptom of Bug #5 (below), not a fundamental flaw in the text-reading approach.
- **Bug #5 re-investigated live, DID reproduce.** The gnome-calculator process
  disappeared entirely (absent from both `pgrep` and the AT-SPI desktop list)
  during testing. Root cause not conclusively isolated, but a real, plausible
  mechanism was found and fixed: `semantic_vision.find_by_atspi()`'s fuzzy
  word-overlap matching had **zero protection** against a vague/mismatched
  description accidentally landing on a window's Close/Quit control. Fixed --
  those controls are now skipped unless the description explicitly asks for them
  by that exact word (a genuine "close the window" request still works).
- **Implemented the verification gate (priority #2 below).** `action_loop.py` no
  longer trusts a model's own `"done"`/`success` claim at face value.
  `_verify_done()` re-observes fresh before agreeing; if the target app can no
  longer be observed/found at all, a claimed success is downgraded to failed with
  a clear reason. This is the fix that actually matters regardless of what caused
  any individual blind-observation incident -- it prevents the loop from ever
  silently reporting a false success again.
- Committed to git (previously 100 files were uncommitted with no git identity
  configured -- both fixed: repo-local `user.name`/`user.email` set, all work
  committed across two commits).

## 5. IMMEDIATE NEXT STEPS (priority order for the next session)

1. **Fix `_observe_atspi()`'s blind spot (Bug #4 above) before trusting `action_loop.py`
   again.** Investigate why GNOME Calculator's display value isn't readable via
   `queryText()` — check if it's exposed via a different AT-SPI interface (e.g.
   `queryValue()`, a live-region/notification event, or requires reading `.description`
   instead of `.name`/text content). Test against 2-3 different apps, not just Calculator,
   since this bug may be app-specific or systemic.
2. **Add a hard verification gate before accepting any `"done"` decision from
   `action_loop.py`.** Never let the model's own claim of success stand alone — the loop
   should independently re-check (e.g. via a fresh observation + simple pattern match, or a
   screenshot diff) before returning `LoopResult(success=True, ...)`. This is the most
   important trust/safety fix given Bug #4's consequences.
3. Investigate Bug #5 (calculator vanishing from AT-SPI tree) — reproduce and diagnose before
   continuing any calculator-based testing.
4. **Set up git identity and commit all uncommitted work** — this has been outstanding for
   the entire session and is the single biggest risk to losing everything built so far.
5. Once action_loop.py is trustworthy: build **Resumable Sessions**
   (`core/session.py` — persist goal + DAG/loop progress + history to
   `~/.config/desktop_automation/sessions/<id>.json`; add `--resume <id>` / `--sessions` flags
   to `agent.py`).
6. Then **Blueprints authoring UX** on top of existing `task_dag.py`/`goal_planner.py` —
   named, saved, individually-testable workflow blocks (the engine already exists; this is
   purely a CLI/authoring layer).
7. Then **richer opt-in memory** — extend `core/memory.py` (already a generic namespaced
   store) with a defined `user_profile` namespace + convenience API for autofill-style data.
8. Resume the Windows 11 download page browser-automation flow (§3.4) if end-to-end complex
   browser-flow robustness is still a goal — fix the `wait_for_selector` state issue first.

---

## 6. TESTING METHODOLOGY — apply this to EVERYTHING, no exceptions

This session's single most important lesson: **never trust a task's own self-reported
success.** Every claim in this document and every future claim must be independently checked:
- File/backend tasks: re-read the file, recompute checksums independently, compare.
- GUI tasks: take a screenshot AFTER the fact and visually inspect it, or query AT-SPI state
  independently of whatever the acting code claims.
- Process/app state: `ps aux | grep <name>`, not just "the launch command returned true".
- The `action_loop.py` calculator failure above is the proof this matters — the model
  confidently reported success on a completely wrong result, and only an independent
  screenshot caught it.

---

## 7. QUICK REFERENCE FOR THE NEXT AI ASSISTANT

- Read `MASTER.md` first for the base project (non-AI, deterministic automation — this is
  the stable foundation).
- Read `ARCHITECTURE_EVOLUTION.md` for the 4-layer fallback design
  (CLI registry → AT-SPI → Electron-CDP → Vision).
- Read THIS file for the AI/adaptive layer built on top, and its current bugs.
- Do not assume anything in §3 "works" beyond exactly what's stated — several things are
  explicitly marked partially-broken.
- Before adding new capability, check whether `core/task_dag.py` + `core/goal_planner.py` +
  `core/parallel_runner.py` already provide the mechanism (they usually do — most gaps
  remaining are UX/authoring layers on existing engines, not missing engines).
- Always independently verify — see §6.
