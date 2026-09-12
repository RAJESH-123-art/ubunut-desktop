"""
capabilities/remote.py — SSH/SFTP remote execution via paramiko.

Auth order: password arg → SSH agent → ~/.ssh/id_* keys.
All connections are explicit and closed in finally blocks — no daemonizing,
no inherited-pipe hazards.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap


def _connect(args: dict) -> tuple[Any, str]:
    """Return (client, error). Caller must close client if not error."""
    import paramiko

    host = str(args.get("host", "")).strip()
    port = int(args.get("port", 22))
    user = str(args.get("user", "")).strip()
    password = args.get("password")
    if not host:
        return None, "'host' is required"
    if not user:
        return None, "'user' is required"
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        kwargs: dict[str, Any] = {
            "hostname": host, "port": port, "username": user, "timeout": 15,
        }
        if password:
            kwargs["password"] = str(password)
            kwargs["allow_agent"] = False
            kwargs["look_for_keys"] = False
        client.connect(**kwargs)
        return client, ""
    except Exception as exc:
        client.close()
        return None, f"cannot connect to {user}@{host}:{port}: {str(exc)[:200]}"


def install(registry: Any, *, approve_all: bool = False) -> None:

    def exec_cmd(args: dict, state: Any) -> Any:
        command = str(args.get("command", "")).strip()
        if not command:
            return fail("'command' is required")
        timeout = int(args.get("timeout", 60))
        client, err = _connect(args)
        if err:
            return fail(err)
        try:
            _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            rc = stdout.channel.recv_exit_status()
            out = stdout.read().decode(errors="replace")
            err_out = stderr.read().decode(errors="replace")
            return ok({
                "host": args.get("host"), "command": command,
                "exit_code": rc, "stdout": out[:20000], "stderr": err_out[:5000],
            })
        except Exception as exc:
            return fail(f"exec failed: {str(exc)[:200]}")
        finally:
            client.close()

    def upload_file(args: dict, state: Any) -> Any:
        local = str(args.get("local_path", "") or args.get("path", "")).strip()
        remote = str(args.get("remote_path", "")).strip()
        if not local:
            return fail("'local_path' is required")
        if not remote:
            return fail("'remote_path' is required")
        lp = Path(local).expanduser()
        if not lp.is_file():
            return fail(f"local file not found: {local}")
        client, err = _connect(args)
        if err:
            return fail(err)
        try:
            sftp = client.open_sftp()
            try:
                sftp.put(str(lp), remote)
                rstat = sftp.stat(remote)
                return ok({
                    "uploaded": str(lp), "remote": remote,
                    "size_bytes": rstat.st_size, "verified": True,
                })
            finally:
                sftp.close()
        except Exception as exc:
            return fail(f"upload failed: {str(exc)[:200]}")
        finally:
            client.close()

    def download_file(args: dict, state: Any) -> Any:
        remote = str(args.get("remote_path", "")).strip()
        local = str(args.get("local_path", "") or args.get("path", "")).strip()
        if not remote:
            return fail("'remote_path' is required")
        if not local:
            return fail("'local_path' is required")
        client, err = _connect(args)
        if err:
            return fail(err)
        try:
            sftp = client.open_sftp()
            try:
                sftp.get(remote, str(Path(local).expanduser()))
                lp = Path(local).expanduser()
                if not lp.is_file() or lp.stat().st_size == 0:
                    return fail(f"download not verified at {local}")
                return ok({
                    "downloaded": remote, "local": str(lp),
                    "size_bytes": lp.stat().st_size, "verified": True,
                })
            finally:
                sftp.close()
        except Exception as exc:
            return fail(f"download failed: {str(exc)[:200]}")
        finally:
            client.close()

    def status(args: dict, state: Any) -> Any:
        """Quick reachability check — verifies auth without running anything."""
        client, err = _connect(args)
        if err:
            return ok({"reachable": False, "error": err})
        try:
            transport = client.get_transport()
            active = bool(transport and transport.is_active())
            return ok({"reachable": active, "host": args.get("host"),
                        "user": args.get("user")})
        finally:
            client.close()

    register_cap(registry, Cap(
        name="ssh.exec",
        description="run a command on a remote host over SSH (auth: password/agent/keys)",
        side_effect="external", inputs=("host", "user", "command", "password", "port"),
    ), exec_cmd)

    register_cap(registry, Cap(
        name="ssh.upload_file",
        description="upload a local file to a remote host via SFTP",
        side_effect="external", inputs=("host", "user", "local_path", "remote_path", "password"),
    ), upload_file)

    register_cap(registry, Cap(
        name="ssh.download_file",
        description="download a remote file to local via SFTP",
        side_effect="external", inputs=("host", "user", "remote_path", "local_path", "password"),
    ), download_file)

    register_cap(registry, Cap(
        name="ssh.status",
        description="check SSH reachability and auth for a remote host",
        side_effect="read", inputs=("host", "user", "password", "port"),
    ), status)
