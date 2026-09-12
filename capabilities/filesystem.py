"""
capabilities/filesystem.py — Filesystem and Archive capabilities.

Covers NIKKI capability families: 8 (FILESYSTEM), 22 (ARCHIVES), 21 (STORAGE)
"""
from __future__ import annotations

import os
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap
from core.safety_guard import UnsafeActionError, assert_safe_to_delete


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _fs_list(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", ".")).expanduser().resolve()
            if not p.exists():
                return fail(f"Path does not exist: {p}")
            if not p.is_dir():
                return fail(f"Path is not a directory: {p}")
            items = []
            for entry in p.iterdir():
                items.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else 0,
                })
            return ok({"path": str(p), "items": items, "count": len(items)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.list", "List directory contents", Cap.READ, ("path",)), _fs_list)

    def _fs_inspect_file(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            if not p.exists():
                return fail(f"File does not exist: {p}")
            stat = p.stat()
            return ok({
                "path": str(p),
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "is_file": p.is_file(),
                "is_dir": p.is_dir(),
            })
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.inspect_file", "Inspect file metadata", Cap.READ, ("path",)), _fs_inspect_file)

    def _fs_create_file(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            content = args.get("content", "")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            return ok({"path": str(p), "created": True})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.create_file", "Create file with content", Cap.WRITE, ("path", "content")), _fs_create_file)

    def _fs_create_folder(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            return ok({"path": str(p), "created": True})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.create_folder", "Create folder", Cap.WRITE, ("path",)), _fs_create_folder)

    def _fs_delete_file(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            assert_safe_to_delete(p)
            if p.exists():
                p.unlink()
            return ok({"path": str(p), "deleted": True})
        except UnsafeActionError as e:
            return fail(f"Safety check blocked deletion: {e}")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.delete_file", "Delete file safely", Cap.DESTRUCTIVE, ("path",), requires_confirmation=True), _fs_delete_file)

    def _fs_delete_folder(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            assert_safe_to_delete(p, restrict_to_home=True)
            if p.exists() and p.is_dir():
                shutil.rmtree(p)
            return ok({"path": str(p), "deleted": True})
        except UnsafeActionError as e:
            return fail(f"Safety check blocked deletion: {e}")
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.delete_folder", "Delete folder recursively", Cap.DESTRUCTIVE, ("path",), requires_confirmation=True), _fs_delete_folder)

    def _fs_read(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            if not p.exists():
                return fail(f"File not found: {p}")
            content = p.read_text(encoding=args.get("encoding", "utf-8"), errors="replace")
            return ok({"path": str(p), "content": content})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.read", "Read file text content", Cap.READ, ("path",)), _fs_read)

    def _fs_write(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            content = args.get("content", "")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding=args.get("encoding", "utf-8"))
            return ok({"path": str(p), "bytes_written": len(content)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.write", "Write content to file", Cap.WRITE, ("path", "content")), _fs_write)

    def _fs_copy_file(args: dict[str, Any], state: Any = None) -> Any:
        try:
            src = Path(args.get("src", "")).expanduser().resolve()
            dst = Path(args.get("dst", "")).expanduser().resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            return ok({"src": str(src), "dst": str(dst)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.copy_file", "Copy file to destination", Cap.WRITE, ("src", "dst")), _fs_copy_file)

    def _fs_move_file(args: dict[str, Any], state: Any = None) -> Any:
        try:
            src = Path(args.get("src", "")).expanduser().resolve()
            dst = Path(args.get("dst", "")).expanduser().resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dst)
            return ok({"src": str(src), "dst": str(dst)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.move_file", "Move/rename file or directory", Cap.WRITE, ("src", "dst")), _fs_move_file)

    def _fs_exists(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            return ok({"path": str(p), "exists": p.exists()})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.exists", "Check if path exists", Cap.READ, ("path",)), _fs_exists)

    def _archive_create_zip(args: dict[str, Any], state: Any = None) -> Any:
        try:
            src = Path(args.get("source", "")).expanduser().resolve()
            dst = Path(args.get("destination", "")).expanduser().resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zipf:
                if src.is_file():
                    zipf.write(src, arcname=src.name)
                else:
                    for root, _, files in os.walk(src):
                        for file in files:
                            fp = Path(root) / file
                            zipf.write(fp, arcname=fp.relative_to(src.parent))
            return ok({"archive": str(dst)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("archive.create_zip", "Create zip archive", Cap.WRITE, ("source", "destination")), _archive_create_zip)

    def _archive_extract_zip(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            dst = Path(args.get("destination", ".")).expanduser().resolve()
            dst.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(p, 'r') as zipf:
                zipf.extractall(dst)
            return ok({"extracted_to": str(dst)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("archive.extract_zip", "Extract zip archive", Cap.WRITE, ("path", "destination")), _archive_extract_zip)

    # ── Missing fs.* capabilities ──────────────────────────────────────────

    def _fs_inspect_folder(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            if not p.exists():
                return fail(f"Folder does not exist: {p}")
            files = list(p.rglob("*"))
            return ok({
                "path": str(p),
                "total_items": len(files),
                "is_dir": p.is_dir(),
                "size_bytes": sum(f.stat().st_size for f in files if f.is_file()),
            })
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.inspect_folder", "Inspect folder metadata and size", Cap.READ, ("path",)), _fs_inspect_folder)

    def _fs_rename_file(args: dict[str, Any], state: Any = None) -> Any:
        try:
            src = Path(args.get("path", "")).expanduser().resolve()
            new_name = args.get("new_name", "")
            dst = src.parent / new_name
            src.rename(dst)
            return ok({"old": str(src), "new": str(dst)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.rename_file", "Rename file in same directory", Cap.WRITE, ("path", "new_name")), _fs_rename_file)

    def _fs_rename_folder(args: dict[str, Any], state: Any = None) -> Any:
        try:
            src = Path(args.get("path", "")).expanduser().resolve()
            new_name = args.get("new_name", "")
            dst = src.parent / new_name
            src.rename(dst)
            return ok({"old": str(src), "new": str(dst)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.rename_folder", "Rename folder in same directory", Cap.WRITE, ("path", "new_name")), _fs_rename_folder)

    def _fs_copy_folder(args: dict[str, Any], state: Any = None) -> Any:
        try:
            src = Path(args.get("src", "")).expanduser().resolve()
            dst = Path(args.get("dst", "")).expanduser().resolve()
            shutil.copytree(src, dst, dirs_exist_ok=True)
            return ok({"src": str(src), "dst": str(dst)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.copy_folder", "Copy entire folder to destination", Cap.WRITE, ("src", "dst")), _fs_copy_folder)

    def _fs_move_folder(args: dict[str, Any], state: Any = None) -> Any:
        try:
            src = Path(args.get("src", "")).expanduser().resolve()
            dst = Path(args.get("dst", "")).expanduser().resolve()
            shutil.move(str(src), str(dst))
            return ok({"src": str(src), "dst": str(dst)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.move_folder", "Move folder to destination", Cap.WRITE, ("src", "dst")), _fs_move_folder)

    def _fs_search_files(args: dict[str, Any], state: Any = None) -> Any:
        try:
            root = Path(args.get("path", ".")).expanduser().resolve()
            pattern = args.get("pattern", "*")
            results = [str(p) for p in root.rglob(pattern)][:200]
            return ok({"path": str(root), "pattern": pattern, "matches": results, "count": len(results)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.search_files", "Search files matching glob pattern recursively", Cap.READ, ("path", "pattern")), _fs_search_files)

    def _fs_find_file(args: dict[str, Any], state: Any = None) -> Any:
        try:
            root = Path(args.get("path", ".")).expanduser().resolve()
            name = args.get("name", "")
            matches = [str(p) for p in root.rglob(name)][:50]
            return ok({"name": name, "found": matches, "count": len(matches)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.find_file", "Find file by name recursively in folder", Cap.READ, ("path", "name")), _fs_find_file)

    def _fs_get_file_size(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            size = p.stat().st_size
            return ok({"path": str(p), "size_bytes": size, "size_kb": round(size / 1024, 2)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.get_file_size", "Get file size in bytes and KB", Cap.READ, ("path",)), _fs_get_file_size)

    def _fs_get_file_type(args: dict[str, Any], state: Any = None) -> Any:
        import mimetypes
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            mime_type, _ = mimetypes.guess_type(str(p))
            return ok({"path": str(p), "suffix": p.suffix, "mime_type": mime_type or "unknown"})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.get_file_type", "Get file type / MIME type", Cap.READ, ("path",)), _fs_get_file_type)

    def _fs_append(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            content = args.get("content", "")
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(content)
            return ok({"path": str(p), "appended_bytes": len(content)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("fs.append", "Append text content to existing file", Cap.WRITE, ("path", "content")), _fs_append)

    # ── Missing archive.* capabilities (tar) ──────────────────────────────

    def _archive_create_tar(args: dict[str, Any], state: Any = None) -> Any:
        try:
            src = Path(args.get("source", "")).expanduser().resolve()
            dst = Path(args.get("destination", "")).expanduser().resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            mode = "w:gz" if str(dst).endswith(".gz") else "w:bz2" if str(dst).endswith(".bz2") else "w"
            with tarfile.open(dst, mode) as tar:
                tar.add(src, arcname=src.name)
            return ok({"archive": str(dst)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("archive.create_tar", "Create tar/tar.gz archive", Cap.WRITE, ("source", "destination")), _archive_create_tar)

    def _archive_extract_tar(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            dst = Path(args.get("destination", ".")).expanduser().resolve()
            dst.mkdir(parents=True, exist_ok=True)
            with tarfile.open(p, "r:*") as tar:
                tar.extractall(dst)
            return ok({"extracted_to": str(dst)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("archive.extract_tar", "Extract tar/tar.gz archive", Cap.WRITE, ("path", "destination")), _archive_extract_tar)
