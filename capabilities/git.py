"""
capabilities/git.py — Git capabilities (arg-list subprocess, no shell injection).

Every command runs as a subprocess argument list. Values never pass through
a shell, so a crafted branch name or message cannot inject commands.
"""
from __future__ import annotations

import re

from capabilities.base import Cap, fail, ok, register_cap, run_shell

_REF_RE = re.compile(r"^[A-Za-z0-9_./-]+$")  # branch/tag names, HEAD~2, refs/...


def _valid_ref(value: str) -> bool:
    return bool(value) and bool(_REF_RE.match(value)) and ".." not in value


def _valid_path(value: str) -> bool:
    return bool(value) and not value.startswith("-")


def install(registry, *, approve_all=False):

    def git_status(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        if not _valid_path(cwd): return fail("invalid cwd")
        code, out, err = run_shell(["git", "-C", cwd, "status"], timeout=20)
        return ok(out) if code == 0 else fail(err or f"git status rc={code}")

    def git_init(kwargs, context):
        path = str(kwargs.get("path", "."))
        if not _valid_path(path): return fail("invalid path")
        code, out, err = run_shell(["git", "init", path], timeout=20)
        return ok(out) if code == 0 else fail(err)

    def git_clone(kwargs, context):
        url = str(kwargs.get("url", ""))
        dest = str(kwargs.get("destination", "") or ".")
        if not url: return fail("url required")
        if not (url.startswith(("https://", "http://", "git://", "ssh://")) or url.endswith(".git")):
            return fail("url must be an https/git/ssh URL")
        if not _valid_path(dest): return fail("invalid destination")
        code, out, err = run_shell(["git", "clone", url, dest], timeout=120)
        return ok(out) if code == 0 else fail(err)

    def git_add(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        files = str(kwargs.get("files", "."))
        if not _valid_path(cwd): return fail("invalid cwd")
        if not _valid_path(files): return fail("invalid files")
        code, out, err = run_shell(["git", "-C", cwd, "add", "--", files], timeout=20)
        return ok(out) if code == 0 else fail(err)

    def git_commit(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        msg = str(kwargs.get("message", ""))
        if not msg: return fail("message required")
        if not _valid_path(cwd): return fail("invalid cwd")
        code, out, err = run_shell(["git", "-C", cwd, "commit", "-m", msg], timeout=30)
        return ok(out) if code == 0 else fail(err)

    def git_push(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        remote = str(kwargs.get("remote", "origin"))
        branch = str(kwargs.get("branch", "main"))
        if not all((_valid_path(cwd), _valid_ref(remote), _valid_ref(branch))):
            return fail("invalid cwd/remote/branch")
        code, out, err = run_shell(["git", "-C", cwd, "push", remote, branch], timeout=60)
        return ok(out) if code == 0 else fail(err)

    def git_pull(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        remote = str(kwargs.get("remote", "origin"))
        branch = str(kwargs.get("branch", "main"))
        if not all((_valid_path(cwd), _valid_ref(remote), _valid_ref(branch))):
            return fail("invalid cwd/remote/branch")
        code, out, err = run_shell(["git", "-C", cwd, "pull", remote, branch], timeout=60)
        return ok(out) if code == 0 else fail(err)

    def git_fetch(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        if not _valid_path(cwd): return fail("invalid cwd")
        code, out, err = run_shell(["git", "-C", cwd, "fetch"], timeout=60)
        return ok(out) if code == 0 else fail(err)

    def git_branch_list(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        if not _valid_path(cwd): return fail("invalid cwd")
        code, out, err = run_shell(["git", "-C", cwd, "branch", "-a"], timeout=20)
        return ok(out) if code == 0 else fail(err)

    def git_branch_create(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        name = str(kwargs.get("name", ""))
        if not name: return fail("name required")
        if not _valid_ref(name): return fail("invalid branch name")
        if not _valid_path(cwd): return fail("invalid cwd")
        code, out, err = run_shell(["git", "-C", cwd, "branch", name], timeout=20)
        return ok(out) if code == 0 else fail(err)

    def git_checkout(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        branch = str(kwargs.get("branch", ""))
        if not branch: return fail("branch required")
        if not _valid_ref(branch): return fail("invalid branch name")
        if not _valid_path(cwd): return fail("invalid cwd")
        code, out, err = run_shell(["git", "-C", cwd, "checkout", branch], timeout=30)
        return ok(out) if code == 0 else fail(err)

    def git_merge(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        branch = str(kwargs.get("branch", ""))
        if not branch: return fail("branch required")
        if not _valid_ref(branch): return fail("invalid branch name")
        if not _valid_path(cwd): return fail("invalid cwd")
        code, out, err = run_shell(["git", "-C", cwd, "merge", branch], timeout=30)
        return ok(out) if code == 0 else fail(err)

    def git_diff(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        file_path = str(kwargs.get("file", "") or "")
        if not _valid_path(cwd): return fail("invalid cwd")
        cmd = ["git", "-C", cwd, "diff"]
        if file_path:
            if not _valid_path(file_path): return fail("invalid file")
            cmd += ["--", file_path]
        code, out, err = run_shell(cmd, timeout=30)
        return ok(out) if code == 0 else fail(err)

    def git_log(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        try:
            limit = str(max(1, min(500, int(kwargs.get("limit", 10)))))
        except (TypeError, ValueError):
            return fail("limit must be an integer")
        if not _valid_path(cwd): return fail("invalid cwd")
        code, out, err = run_shell(["git", "-C", cwd, "log", "-n", limit], timeout=30)
        return ok(out) if code == 0 else fail(err)

    def git_stash(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        action = str(kwargs.get("action", "list"))
        if action not in ("list", "pop", "apply", "drop"):  # 'push' needs a message arg; keep allowlist strict
            return fail("action must be one of list/pop/apply/drop")
        if not _valid_path(cwd): return fail("invalid cwd")
        code, out, err = run_shell(["git", "-C", cwd, "stash", action], timeout=30)
        return ok(out) if code == 0 else fail(err)

    def git_reset(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        mode = str(kwargs.get("mode", "--soft"))
        ref = str(kwargs.get("ref", "HEAD"))
        # Destructive by default policy: --hard/--mixed discard working-tree
        # changes. Require explicit confirmation unless pre-approved.
        if not approve_all and kwargs.get("confirm") != "yes":
            return fail("git.reset is destructive; set confirm='yes' or approve the task")
        if mode not in ("--soft", "--mixed", "--hard"):
            return fail("mode must be --soft, --mixed, or --hard")
        if not _valid_ref(ref): return fail("invalid ref")
        if not _valid_path(cwd): return fail("invalid cwd")
        code, out, err = run_shell(["git", "-C", cwd, "reset", mode, ref], timeout=30)
        return ok(out) if code == 0 else fail(err)

    def git_tag(kwargs, context):
        cwd = str(kwargs.get("cwd", "."))
        name = str(kwargs.get("name", ""))
        msg = str(kwargs.get("message", "") or "")
        if not name: return fail("name required")
        if not _valid_ref(name): return fail("invalid tag name")
        if not _valid_path(cwd): return fail("invalid cwd")
        cmd = ["git", "-C", cwd, "tag"]
        if msg:
            cmd += ["-a", name, "-m", msg]
        else:
            cmd += [name]
        code, out, err = run_shell(cmd, timeout=20)
        return ok(out) if code == 0 else fail(err)

    register_cap(registry, Cap("git.status", "git status", "read", ("cwd",)), git_status)
    register_cap(registry, Cap("git.init", "git init", "local_write", ("path",)), git_init)
    register_cap(registry, Cap("git.clone", "git clone repository from URL", "local_write", ("url", "destination")), git_clone)
    register_cap(registry, Cap("git.add", "git add files", "local_write", ("cwd", "files")), git_add)
    register_cap(registry, Cap("git.commit", "git commit with message", "local_write", ("cwd", "message")), git_commit)
    register_cap(registry, Cap("git.push", "git push to remote", "external", ("cwd", "remote", "branch")), git_push)
    register_cap(registry, Cap("git.pull", "git pull from remote", "local_write", ("cwd", "remote", "branch")), git_pull)
    register_cap(registry, Cap("git.fetch", "git fetch", "read", ("cwd",)), git_fetch)
    register_cap(registry, Cap("git.branch_list", "git branch -a", "read", ("cwd",)), git_branch_list)
    register_cap(registry, Cap("git.branch_create", "git branch create", "local_write", ("cwd", "name")), git_branch_create)
    register_cap(registry, Cap("git.checkout", "git checkout branch", "local_write", ("cwd", "branch")), git_checkout)
    register_cap(registry, Cap("git.merge", "git merge branch", "local_write", ("cwd", "branch")), git_merge)
    register_cap(registry, Cap("git.diff", "git diff", "read", ("cwd", "file")), git_diff)
    register_cap(registry, Cap("git.log", "git log -n limit", "read", ("cwd", "limit")), git_log)
    register_cap(registry, Cap("git.stash", "git stash (list/pop/apply/drop)", "local_write", ("cwd", "action")), git_stash)
    register_cap(registry, Cap("git.reset", "git reset (destructive; confirm required; default --soft)", "destructive", ("cwd", "mode", "ref"), requires_confirmation=not approve_all), git_reset)
    register_cap(registry, Cap("git.tag", "git tag (annotated if message given)", "local_write", ("cwd", "name", "message")), git_tag)
