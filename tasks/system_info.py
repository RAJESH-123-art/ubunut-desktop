#!/usr/bin/env python3
"""
Task: system_info
Collects Ubuntu version, kernel, CPU, RAM, GPU, disk, display resolution.
Saves to /tmp/system_info.txt (or a custom path).
Does NOT collect: usernames, passwords, browser data, personal files.
"""
import subprocess
from pathlib import Path
from typing import Any
from loguru import logger


def _run(cmd: str) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return "N/A"


def execute(args: dict[str, Any], resources: dict[str, Any]) -> bool:
    save_path = args.get("save_path", "") or "/tmp/system_info.txt"
    logger.info(f"🖥️  Collecting system info → {save_path}")

    lines: list[str] = [
        "=" * 50,
        "  SYSTEM INFORMATION REPORT",
        "=" * 50,
    ]

    # Ubuntu version
    lines += ["", "[ Ubuntu Version ]",
              f"  {_run('lsb_release -d | cut -f2-')}"]

    # Kernel
    lines += ["", "[ Kernel Version ]",
              f"  {_run('uname -r')}"]

    # CPU
    cpu_model = _run("grep -m1 'model name' /proc/cpuinfo | cut -d: -f2").strip()
    cores     = _run("nproc")
    lines += ["", "[ CPU Model ]",
              f"  {cpu_model}",
              f"  Cores: {cores}"]

    # RAM
    ram = _run("free -h | awk '/^Mem:/ {print \"Total: \"$2\"  |  Used: \"$3\"  |  Free: \"$4}'")
    lines += ["", "[ RAM Capacity ]", f"  {ram}"]

    # GPU
    gpu = _run("lspci | grep -iE 'vga|3d|display' | sed 's/.*: //'")
    lines += ["", "[ GPU Model ]",
              *(f"  {g}" for g in (gpu.splitlines() or ["N/A"]))]

    # Disk
    disk = _run("df -h --output=source,size,used,avail,pcent,target | grep -v tmpfs | grep -v loop | head -4")
    lines += ["", "[ Disk Capacity ]",
              *(f"  {d}" for d in disk.splitlines())]

    # Display resolution
    res = _run("xrandr 2>/dev/null | grep ' connected' | grep -oP '\\d+x\\d+' | head -1")
    if not res:
        res = "(Wayland — use display settings to check)"
    lines += ["", "[ Display Resolution ]", f"  {res}"]

    lines += [
        "", "=" * 50,
        "  NOTE: No usernames, passwords, browser data,",
        "  or personal files collected — hardware only.",
        "=" * 50,
    ]

    report = "\n".join(lines)

    # Save
    try:
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        Path(save_path).write_text(report + "\n")
        logger.info(f"✅ Saved to {save_path}")
    except Exception as e:
        logger.error(f"Could not save: {e}")

    print(report)
    print(f"\n📄 Saved → {save_path}")
    return True
