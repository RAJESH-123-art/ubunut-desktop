#!/usr/bin/env python3
"""Manage crash-recovery checkpoints without replaying uncertain actions."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.structured_automation import StructuredPlan, describe_plan
from core.structured_sessions import (
    abandon_session,
    list_sessions,
    load_session,
    resume_session,
)


def _approve(plan: StructuredPlan) -> bool:
    print("\nResolved plan:\n")
    print(describe_plan(plan))
    return input("\nExecute this exact plan? Type 'yes': ").strip().lower() == "yes"


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage structured automation sessions")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List incomplete sessions")
    list_parser.add_argument("--all", action="store_true", help="Include completed sessions")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect one session")
    inspect_parser.add_argument("session_id")

    resume_parser = subparsers.add_parser("resume", help="Resume a safe incomplete session")
    resume_parser.add_argument("session_id")
    resume_parser.add_argument("-y", "--yes", action="store_true")

    abandon_parser = subparsers.add_parser("abandon", help="Mark a session abandoned")
    abandon_parser.add_argument("session_id")

    args = parser.parse_args()
    try:
        if args.command == "list":
            print(json.dumps(list_sessions(include_completed=args.all), indent=2))
            return 0
        if args.command == "inspect":
            print(json.dumps(load_session(args.session_id), indent=2, default=str))
            return 0
        if args.command == "abandon":
            abandon_session(args.session_id)
            print(f"Abandoned structured session {args.session_id}")
            return 0

        session = load_session(args.session_id)
        if session.get("complete"):
            print("REFUSED: session is already complete or abandoned")
            return 2
        if session.get("uncertain"):
            print(
                "REFUSED: the interrupted consequential step has an uncertain outcome; "
                "inspect it and explicitly abandon the session instead of replaying it"
            )
            return 2
        result = resume_session(
            args.session_id,
            approve_all=bool(args.yes),
            approval_callback=None if args.yes else _approve,
        )
        print(result.message)
        return 0 if result.success else 1
    except (OSError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
