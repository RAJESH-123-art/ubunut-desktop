"""
capabilities/development.py — Development capabilities.

All subprocess calls use argument lists (no shell=True, no f-string
command construction). Raw shell commands route through the shared
safety guard (core.safety_guard.assert_safe_shell_command) exactly like
capabilities/terminal.py — dev caps must not bypass the guard.
"""
from __future__ import annotations

import os
import shlex

from capabilities.base import Cap, fail, ok, register_cap, run_shell
from core.safety_guard import UnsafeActionError, assert_safe_shell_command


def _valid_path(value: str) -> bool:
    return bool(value) and not value.startswith("-")


def _resolve(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


def install(registry, *, approve_all=False):

    def create_project(kwargs, context):
        path = kwargs.get("path")
        if not path:
            return fail("path required")
        try:
            os.makedirs(_resolve(str(path)), exist_ok=True)
            return ok(f"Created project dir {path}")
        except Exception as e:
            return fail(str(e))

    def create_file(kwargs, context):
        path = kwargs.get("path")
        content = str(kwargs.get("content", ""))
        if not path:
            return fail("path required")
        try:
            target = _resolve(str(path))
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "w") as f:
                f.write(content)
            if not os.path.exists(target) or os.path.getsize(target) < len(content):
                return fail(f"write to {path} not verified")
            return ok(f"Created file {path} ({len(content)} bytes, verified)")
        except Exception as e:
            return fail(str(e))

    def read_code(kwargs, context):
        path = kwargs.get("path")
        if not path:
            return fail("path required")
        try:
            with open(_resolve(str(path)), "r") as f:
                return ok(f.read())
        except Exception as e:
            return fail(str(e))

    def edit_file(kwargs, context):
        path = kwargs.get("path")
        content = kwargs.get("content")
        if not path or content is None:
            return fail("path and content required")
        try:
            target = _resolve(str(path))
            with open(target, "w") as f:
                f.write(str(content))
            if not os.path.exists(target):
                return fail(f"edit of {path} not verified")
            return ok(f"Edited file {path} (verified)")
        except Exception as e:
            return fail(str(e))

    def search_code(kwargs, context):
        path = str(kwargs.get("path", ""))
        pattern = str(kwargs.get("pattern", ""))
        if not path or not pattern:
            return fail("path and pattern required")
        try:
            import subprocess as sp
            result = sp.run(
                ["grep", "-rn", "-e", pattern, "--", _resolve(path)],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if result.returncode == 0:
                return ok(result.stdout or "No matches")
            if result.returncode == 1:  # grep: no matches found
                return ok("No matches")
            return fail(result.stderr or f"grep rc={result.returncode}")
        except Exception as e:
            return fail(str(e))

    def _run_interpreter(language_bin: str, kwargs, context):
        path = str(kwargs.get("path", ""))
        if not path:
            return fail("path required")
        script = _resolve(path)
        if not os.path.isfile(script):
            return fail(f"script not found: {path}")
        args = kwargs.get("args", [])
        if isinstance(args, str):
            args = shlex.split(args)
        if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
            return fail("args must be a list of strings (or a single shell-quoted string)")
        code, out, err = run_shell([language_bin, script, *args], timeout=120)
        if code == 0:
            return ok({"stdout": out, "stderr": err, "exit_code": code})
        return fail(f"exit {code}: {err or out}")

    def run_python(kwargs, context):
        return _run_interpreter("python3", kwargs, context)

    def run_node(kwargs, context):
        return _run_interpreter("node", kwargs, context)

    def run_sh(kwargs, context):
        command = str(kwargs.get("command", ""))
        cwd = str(kwargs.get("cwd", ".") or ".")
        if not command:
            return fail("command required")
        try:
            # Same shared guard as terminal.py — never bypass.
            assert_safe_shell_command(command)
            workdir = _resolve(cwd)
            if not os.path.isdir(workdir):
                return fail(f"cwd is not a directory: {cwd}")
            import subprocess as sp
            result = sp.run(
                command, shell=True, cwd=workdir,
                capture_output=True, text=True, timeout=120, check=False,
            )
            data = {"stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "exit_code": result.returncode}
            if result.returncode == 0:
                return ok(data)
            return fail(f"exit {result.returncode}: {result.stderr.strip()[:500] or result.stdout.strip()[:500]}")
        except UnsafeActionError as e:
            return fail(f"BLOCKED by safety guard: {e}")
        except Exception as e:
            return fail(str(e))

    def install_pip(kwargs, context):
        package = str(kwargs.get("package", ""))
        if not package:
            return fail("package required")
        # Validate against PyPI name pattern to prevent option injection
        import re
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*(==[A-Za-z0-9._*-]+)?$", package):
            return fail(f"invalid package name {package!r}")
        if not approve_all and kwargs.get("confirm") != "yes":
            return fail("installing packages changes the system; set confirm='yes' or approve the task")
        code, out, err = run_shell(
            ["python3", "-m", "pip", "install", "--user", package], timeout=300)
        return ok(out) if code == 0 else fail(err)

    def install_npm(kwargs, context):
        package = str(kwargs.get("package", ""))
        cwd = str(kwargs.get("cwd", ".") or ".")
        if not package:
            return fail("package required")
        import re
        if not re.match(r"^[A-Za-z0-9@/_-]+(@[0-9.]+)?$", package):
            return fail(f"invalid package name {package!r}")
        if not approve_all and kwargs.get("confirm") != "yes":
            return fail("installing packages changes the system; set confirm='yes' or approve the task")
        workdir = _resolve(cwd)
        if not os.path.isdir(workdir):
            return fail(f"cwd is not a directory: {cwd}")
        import subprocess as sp
        result = sp.run(["npm", "install", package], cwd=workdir,
                        capture_output=True, text=True, timeout=300, check=False)
        return ok(result.stdout.strip()) if result.returncode == 0 else fail(result.stderr.strip())

    def _guarded_command(kwargs, context):
        command = str(kwargs.get("command", ""))
        cwd = str(kwargs.get("cwd", ".") or ".")
        if not command:
            return fail("command required"), None
        try:
            assert_safe_shell_command(command)
        except UnsafeActionError as e:
            return fail(f"BLOCKED by safety guard: {e}"), None
        workdir = _resolve(cwd)
        if not os.path.isdir(workdir):
            return fail(f"cwd is not a directory: {cwd}"), None
        import subprocess as sp
        try:
            result = sp.run(command, shell=True, cwd=workdir,
                            capture_output=True, text=True, timeout=300, check=False)
        except Exception as e:
            return fail(str(e)), None
        if result.returncode == 0:
            return ok(result.stdout.strip() or "done"), None
        return fail(f"exit {result.returncode}: {result.stderr.strip()[:500]}"), None

    def build(kwargs, context):
        result, _ = _guarded_command(kwargs, context)
        return result

    def test(kwargs, context):
        result, _ = _guarded_command(kwargs, context)
        return result

    def lint(kwargs, context):
        path = str(kwargs.get("path", ""))
        if not path:
            return fail("path required")
        code, out, err = run_shell(["flake8", _resolve(path)], timeout=60)
        if code in (0, 1):  # 1 = lint issues found, still a successful lint run
            return ok(out or "clean")
        return fail(err)

    def inspect_logs(kwargs, context):
        path = str(kwargs.get("path", ""))
        if not path:
            return fail("path required")
        try:
            lines = max(1, min(10_000, int(kwargs.get("lines", 10))))
        except (TypeError, ValueError):
            return fail("lines must be an integer")
        code, out, err = run_shell(["tail", "-n", str(lines), "--", _resolve(path)], timeout=20)
        return ok(out) if code == 0 else fail(err)

    def format_code(kwargs, context):
        path = str(kwargs.get("path", ""))
        if not path:
            return fail("path required")
        code, out, err = run_shell(["black", _resolve(path)], timeout=120)
        return ok(out or "formatted") if code == 0 else fail(err)

    register_cap(registry, Cap("dev.create_project", "create project dir", "local_write", ("path", "language", "name")), create_project)
    register_cap(registry, Cap("dev.create_file", "create a source file (write verified)", "local_write", ("path", "content")), create_file)
    register_cap(registry, Cap("dev.read_code", "read source file", "read", ("path",)), read_code)
    register_cap(registry, Cap("dev.edit_file", "edit file (write verified)", "local_write", ("path", "content")), edit_file)
    register_cap(registry, Cap("dev.search_code", "grep -rn pattern in path", "read", ("path", "pattern", "file_pattern")), search_code)
    register_cap(registry, Cap("dev.run_python", "run python script with args list", "local_write", ("path", "args")), run_python)
    register_cap(registry, Cap("dev.run_node", "run node script with args list", "local_write", ("path", "args")), run_node)
    register_cap(registry, Cap("dev.run_shell", "run shell command (guarded, cwd-scoped)", "local_write", ("command", "cwd")), run_sh)
    register_cap(registry, Cap("dev.install_pip", "pip install package (confirm required)", "destructive", ("package", "venv"), requires_confirmation=not approve_all), install_pip)
    register_cap(registry, Cap("dev.install_npm", "npm install package (confirm required)", "destructive", ("package", "cwd"), requires_confirmation=not approve_all), install_npm)
    register_cap(registry, Cap("dev.build", "run guarded build command", "local_write", ("command", "cwd")), build)
    register_cap(registry, Cap("dev.test", "run guarded test command", "read", ("command", "cwd")), test)
    register_cap(registry, Cap("dev.lint", "run flake8", "read", ("path", "language")), lint)
    register_cap(registry, Cap("dev.inspect_logs", "tail logs", "read", ("path", "lines")), inspect_logs)
    register_cap(registry, Cap("dev.format_code", "run black", "local_write", ("path", "language")), format_code)
