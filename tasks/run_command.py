"""
Execute a raw shell command directly.

Decision flow:
  1. Sanity-check: command must be non-empty
  2. Run with subprocess.run(shell=True, timeout=30)
  3. Return True if returncode == 0
  4. Handle TimeoutExpired gracefully (log and return False)

Args:
    command (str): The shell command string to execute. Required.
"""
import subprocess

from loguru import logger

from core.logger import finish, notify, start
from core.safety_guard import UnsafeActionError, assert_safe_shell_command

_TIMEOUT = 30  # seconds


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "run_command"
    start(task_name)
    try:
        command = str(args.get("command", "")).strip()
        timeout = int(args.get("timeout", _TIMEOUT) or _TIMEOUT)

        # ── Sanity check ──────────────────────────────────────────────────────
        # ── Sanity check ─────────────────────────────────────────
        if not command:
            raise ValueError("'command' is required")

        # ── Safety check ─ refuse known-catastrophic patterns before running ──
        # This is the raw shell escape hatch, most likely to receive a
        # parser/LLM-constructed string — a cheap, high-value net against
        # e.g. "rm -rf ~" reaching subprocess.run() unchecked.
        try:
            assert_safe_shell_command(command)
        except UnsafeActionError as exc:
            logger.error(f"⛔ {exc}")
            notify(f"Blocked dangerous command: {command[:60]}", critical=True)
            finish("error", task_name, err=exc)
            return False

        logger.info(f"Running shell command: {command!r}")

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                f"Command timed out after {timeout}s: {command!r}\n"
                "The process was killed — increase timeout or split into shorter steps."
            )
            finish("error", task_name)
            return False

        if result.stdout:
            logger.debug(f"stdout: {result.stdout.rstrip()}")
        if result.stderr:
            logger.debug(f"stderr: {result.stderr.rstrip()}")

        if result.returncode == 0:
            logger.info(f"✅ Command succeeded (rc=0): {command!r}")
            notify(f"Ran: {command[:60]}{'…' if len(command) > 60 else ''}")
            finish("success", task_name)
            return True

        logger.warning(f"Command exited with code {result.returncode}: {command!r}")
        finish("error", task_name)
        return False

    except Exception as exc:
        finish("error", task_name, err=exc)
        raise


def cleanup(resources: dict) -> None:
    pass


if __name__ == "__main__":
    r = setup()
    execute({"command": "echo 'hello from run_command'"}, r)
    cleanup(r)
