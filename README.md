# 🤖 Linux Desktop Automation System

A modular, extensible Python framework for automating GUI, browser, file, and system tasks on Ubuntu 24.04+. Works with both Wayland and X11 via XWayland.

- **Mouse/Keyboard control** using pyautogui, pynput, xdotool
- **Image & text detection** with OpenCV and pytesseract
- **Window management** and system command wrapper
- **Workflow chaining** (YAML) and scheduling (cron/systemd)
- **Logging with screenshots** for debugging
- **Telegram notifications** (optional)
- **Sample tasks**: browser visit, file organizer

---

## 🧩 Folder Structure
```
desktop-automation/
├── automation.py             # Main entry point; CLI runner
├── install.sh                 # Ubuntu dependencies + Python venv
├── README.md
├── core/                      # Core engine modules
│   ├── gui_controller.py     # Mouse/keyboard/ window actions (Wayland/X11)
│   ├── vision_engine.py      # Image detection + OCR
│   ├── system_utils.py       # System commands, file ops, notifications
│   └── logger.py             # Structured logs + screenshots
├── tasks/                     # Individual automation scripts
│   ├── __init__.py           # Auto-discovery loader
│   ├── open_browser_and_visit.py
│   └── organize_downloads.py
├── config/
│   ├── config.yaml           # Main config (GUI, vision, paths, logging)
│   ├── workflow.yaml         # Example workflows (task chains)
│   └── config_loader.py      # YAML loader with env var substitution
├── ui/
│   ├── __init__.py
│   └── cli.py                # Interactive CLI dash
└── logs/
    └── screenshots/           # Auto-saved per-action screenshots
```

---

## 🚀 Quick Start

1. **Clone / download** the repo:
   ```bash
   git clone https://github.com/yourrepo/desktop-automation
   cd desktop-automation
   ```

2. **Run install script** (creates Python venv and installs system deps):
   ```bash
   ./install.sh
   source .venv/bin/activate
   ```

3. **Verify environment**:
   ```bash
   python automation.py env
   ```

4. **List available tasks**:
   ```bash
   python automation.py list
   ```

5. **Run a task**:
   ```bash
   python automation.py run organize_downloads --args '{"delete_junk": true, "create_subfolders": {"Images": ["jpg","png"], "Documents": ["pdf","txt"]}}'
   ```

6. **Try the interactive CLI dashboard**:
   ```bash
   python ui/cli.py --menu
   ```

---

## 🛠 Adding Your Own Tasks

**Task skeleton** (save as `tasks/my_task.py`):

```python
#!/usr/bin/env python3
"""
Brief description of what this task does.
"""
def setup():
    """Prepare resources or open windows needed for task."""
    print("Setup: my_task")
    return {}

def execute(args: dict, resources: dict):
    """Main task logic."""
    gui = resources['gui']
    vision = resources['vision']
    # your automation here...
    return True

def cleanup(resources: dict):
    """Clean up temporary resources."""
    pass
```

- The system auto-discovers any `.py` file in `tasks/` with `setup`, `execute`, `cleanup`
- Tasks receive shared `resources` dict: contains `gui`, `vision`, `config`
- Use `setup()` to open terminals, browsers, or authenticate apps
- Use `execute()` for actions; arguments come from CLI JSON or workflow YAML
- Return `True` on success; raise exceptions on failure

---

## 📋 Example Commands

### Using Main Script

```bash
# Single task
python automation.py run open_browser_and_visit --args '{
  "url": "https://github.com",
  "screenshot": true,
  "close_after": true
}'

# Run a workflow defined in config/workflow.yaml
python automation.py workflow daily_cleanup

# List tasks
python automation.py list

# Print capability snapshot
python automation.py env
```

### Using CLI Dashboard

```bash
python ui/cli.py --menu
```

Interactive menu includes:
- List tasks
- Run tasks (interactive JSON argument editor)
- Run workflows
- Show logs
- Environment snapshot

### Using Python API

```python
from automation import run_task, run_workflow

result = run_task("open_browser_and_visit", {"url": "https://example.com", "screenshot": True})
run_workflow("daily_cleanup")
```

---

## ⚙️ Configuration

Edit `config/config.yaml` (or set `AUTOMATION_CONFIG` env var). Important sections:

- **gui**: safe_mode confidence timers screenshot_on_action
- **vision**: ocr_language tesseract_path template_match_threshold
- **logging**: level file rotation console_output
- **notifications**: desktop timeout telegram bot_token/chat_id
- **system**: session_type_check shell terminal_command
- **tasks**: default_timeout default_retries pause_on_error

YAML supports `${VAR}` environment variable substitution.

---

## 🔁 Workflow Chains

Example `config/workflow.yaml` already contains sample workflows (daily_cleanup, browser_evening, batch_process, system_check). To run a custom workflow:

```bash
python automation.py workflow my_workflow_name --file config/workflow.yaml
```

Workflow features:
- Sequential task lists
- Per-task arguments
- Retries
- on_error handling (ignore/notify/abort)
- Workflow timeout
- Continue or abort on failures

---

## 📝 Logging & Screenshots

- Every `log_action` captures a timestamped screenshot in `logs/screenshots/`
- Structured JSON lines in `logs/automation.log` (rotate 10 MB, 7 days)
- Use `take_screenshot(name)` manually for custom images

Notifications:
- `notify()` desktop (notify-send)
- `telegram()` (requires bot_token/chat_id env)

---

## 🛡️ Safety & Permissions

- On Wayland, only XWayland windows are manageable; ensure the target app runs with XWayland
- The install script sets up `xdotool`, `wmctrl`, `notify-send` for window control
- Python venv avoids polluting system packages

---

## 🙋‍♂️ Troubleshooting

- **xdotool not found**: Ensure Ubuntu 24.04 has `xdotool wmctrl notify-send`. Run install script or: `sudo apt install xdotool wmctrl libnotify-bin`.
- **Task not found**: Check `tasks/` directory for your file; verify Python can import.
- **Permissions issue**: Don't run the whole automation as root. Individual `subprocess` calls with sudo can be used where needed.

---

## 🧱 Extending the System

### New core modules

Edit modules under `core/` and expose via `core/__init__.py`. Common patterns:
- System integrations (pulseaudio, dconf)
- Browser automation via Playwright
- OCR models, AI/LLM wrappers

### Custom UI

Add modules under `ui/`: optional lightweight GUI (Tkinter/PyQt) for task selection.

### Scheduler (cron/systemd)

Example systemd timer (run daily at 23:00):

```ini
# /etc/systemd/system/automation-daily.timer
[Unit]
Description=Daily Automation

[Timer]
OnCalendar=*-*-* 23:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# /etc/systemd/system/automation-daily.service
[Unit]
Description=Run daily automation workflow
Type=oneshot
WorkingDirectory=/home/user/desktop-automation
ExecStart=/usr/bin/python3 automation.py workflow daily_cleanup
Environment=XDG_SESSION_TYPE=wayland
```

Enable with: `systemctl enable --now automation-daily.timer`

---

## 📓 Developer Notes

- The project enforces `setup/execute/cleanup` to guarantee cleanup after errors.
- Use `log_action` for debuggable logs. Automatic screenshots are taken unless `take_shoot=False`.
- `GUIController` abstracts Wayland/X11. On Wayland, it relies heavily on `xdotool`.
- Configuration can be overridden per-command line `AUTOMATION_CONFIG=/custom/path.yaml`.

---

## 🤝 Contributing

- Add new tasks to `tasks/` with docstring & required methods
- Update README with examples
- Keep config backward-compatible

---

## 📄 License

MIT

---

### Happy Automating! 🎉  

For questions, file an issue or see `ui/cli.py --menu` to explore.