"""
Execute a raw shell command directly.

Decision flow:
  1. Sanity-check: command must be non-empty
  2. Run with subprocess.run(shell=True, timeout=30)
  3. Return True if returncode == 0
  4. Handle TimeoutExpired gracefully (log and return False)

Args:
    command (str): The shell command string to execute. Required.
    stdin (str): Optional text supplied to the command's standard input.
"""
import os
import signal
import subprocess

from loguru import logger

from core.logger import finish, notify, start
from core.safety_guard import UnsafeActionError, assert_safe_shell_command

_TIMEOUT = 30  # seconds
_MAX_TIMEOUT = 300
_MAX_LOG_CHARS = 20_000


def setup() -> dict:
    return {}


def execute(args: dict, resources: dict) -> bool:
    task_name = "run_command"
    start(task_name)
    try:
        command = str(args.get("command", "")).strip()
        timeout = float(args.get("timeout", _TIMEOUT) or _TIMEOUT)
        stdin_data = args.get("stdin")
        if stdin_data is not None:
            stdin_data = str(stdin_data)

        # ── Sanity check ──────────────────────────────────────────────────────
        # ── Sanity check ─────────────────────────────────────────
        if not command:
            raise ValueError("'command' is required")
        if not 0 < timeout <= _MAX_TIMEOUT:
            raise ValueError(f"'timeout' must be greater than 0 and at most {_MAX_TIMEOUT} seconds")
        if args.get("authorized") is not True:
            logger.error("Refusing raw shell execution without authorized=True")
            finish("error", task_name)
            return False

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

        process = subprocess.Popen(
            command,
            shell=True,
            stdin=subprocess.PIPE if stdin_data is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(input=stdin_data, timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the whole command process group, not only `/bin/sh`.
            # subprocess.run(..., timeout=...) can leave grandchildren alive;
            # a live test proved a timed-out Python child continued running
            # after this task claimed it had been killed.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=1)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            logger.warning(
                f"Command timed out after {timeout}s: {command!r}\n"
                "The process group was killed — increase timeout or split into shorter steps."
            )
            finish("error", task_name)
            return False

        result = subprocess.CompletedProcess(
            args=command,
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )

        if result.stdout:
            output = result.stdout.rstrip()
            logger.debug(f"stdout: {output[:_MAX_LOG_CHARS]}{'… [truncated]' if len(output) > _MAX_LOG_CHARS else ''}")
        if result.stderr:
            output = result.stderr.rstrip()
            logger.debug(f"stderr: {output[:_MAX_LOG_CHARS]}{'… [truncated]' if len(output) > _MAX_LOG_CHARS else ''}")

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
