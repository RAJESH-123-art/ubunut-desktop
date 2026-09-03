#!/usr/bin/env python3
"""
Assistant interface: parse natural language commands and execute automations.

This is a thin CLI wrapper around agent.py's run_command() -- the single,
modern automation pipeline (SmartParser -> cli_registry -> AT-SPI -> AI
tiers, with self-healing StrategyExecutor underneath). It intentionally does
NOT parse or dispatch anything itself anymore: it previously duplicated that
work through the now-retired legacy/intent_parser.py pipeline, which meant
this entry point and `agent.py "<command>"` could behave differently for the
same input. See VERCEPT_LEVEL_ROADMAP.md for why that was a problem.

Kept as a separate entry point for convenience (e.g. --file batch mode).

Examples:
- open brave browser
- open canva design with Instagram Story template
- click login button then type admin in username field
"""

import argparse
import os
import sys

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import run_command


def main() -> None:
    parser = argparse.ArgumentParser(description="Automation Assistant")
    _ = parser.add_argument("command", nargs="?", help="Natural language command")
    _ = parser.add_argument("--file", help="Read commands from a file (one per line)")
    args = parser.parse_args()

    commands: list[str] = []
    file_val = getattr(args, "file", None)
    file_arg = file_val if isinstance(file_val, str) else None
    if file_arg:
        try:
            with open(file_arg, "r") as f:
                commands = [line.strip() for line in f if line.strip()]
        except OSError as e:
            print(f"Failed to read file: {e}")
            return

    cmd_val = getattr(args, "command", None)
    command_arg = cmd_val if isinstance(cmd_val, str) else None
    if command_arg:
        commands.insert(0, command_arg)

    if not commands:
        parser.print_help()
        return

    overall_ok = True
    for txt in commands:
        print(f"\n=== {txt} ===")
        ok = run_command(txt)
        overall_ok = overall_ok and ok

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
