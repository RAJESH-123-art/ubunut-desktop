"""
capabilities/data.py — Structured Data Processing capabilities (JSON/CSV/SQLite).

Covers NIKKI capability family: 30 (DATA PROCESSING)
"""
from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from capabilities.base import Cap, fail, ok, register_cap
from core.task_contract import TaskResult


def install(registry: Any, *, approve_all: bool = False) -> None:

    def _data_read_json(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ok({"data": data})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("data.read_json", "Read JSON file", Cap.READ, ("path",)), _data_read_json)

    def _data_read_csv(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            rows = []
            with open(p, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(dict(row))
            return ok({"rows": rows, "count": len(rows)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("data.read_csv", "Read CSV file into dict list", Cap.READ, ("path",)), _data_read_csv)

    def _data_query_sqlite(args: dict[str, Any], state: Any = None) -> Any:
        db_path = args.get("path", "")
        query = args.get("query", "")
        if not query:
            return fail("query is required")
        # READ-ONLY enforcement: strip params/whitespace and require a single
        # SELECT (or EXPLAIN/PRAGMA table_* read) statement. Anything else —
        # DROP/DELETE/UPDATE/INSERT/ATTACH — is refused in this read cap.
        normalized = " ".join(str(query).strip().rstrip(";").strip().split())
        first_word = normalized.split(None, 1)[0].upper() if normalized else ""
        if first_word not in {"SELECT", "WITH", "EXPLAIN", "VALUES"}:
            return fail(
                f"data.query_sqlite is read-only (first keyword {first_word!r} not allowed); "
                "use data.execute_sqlite (local_write) for mutations"
            )
        if any(kw in normalized.upper() for kw in ("; SELECT", "; DROP", "; DELETE", "; INSERT", "; UPDATE", "; ATTACH", "; DETACH")):
            return fail("multiple SQL statements are not allowed in one query")
        if str(db_path).startswith(("file:", "http:", "https:")):
            return fail("sqlite path must be a local file path")
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(normalized)
            rows = [dict(r) for r in cursor.fetchall()]
            conn.close()
            return ok({"rows": rows, "count": len(rows)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("data.query_sqlite", "Execute READ-ONLY SELECT query on SQLite DB", Cap.READ, ("path", "query")), _data_query_sqlite)

    def _data_write_json(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            data = args.get("data", {})
            indent = int(args.get("indent", 2))
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=indent)
            return ok({"path": str(p), "written": True})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("data.write_json", "Write structured data to JSON file", Cap.WRITE, ("path", "data")), _data_write_json)

    def _data_write_csv(args: dict[str, Any], state: Any = None) -> Any:
        try:
            p = Path(args.get("path", "")).expanduser().resolve()
            rows = args.get("rows") or args.get("data") or []
            if not rows:
                return fail("rows list is required")
            p.parent.mkdir(parents=True, exist_ok=True)
            # Accept BOTH list-of-dicts and list-of-lists (read_csv returns
            # dicts, but callers naturally pass matrix rows; round-trip must
            # work both ways).
            if isinstance(rows[0], dict):
                headers = list(rows[0].keys())
                with open(p, "w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=headers)
                    writer.writeheader()
                    writer.writerows(rows)
            else:
                with open(p, "w", encoding="utf-8", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerows(rows)
            return ok({"path": str(p), "rows_written": len(rows)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("data.write_csv", "Write list of dicts to CSV file", Cap.WRITE, ("path", "rows")), _data_write_csv)

    def _data_execute_sqlite(args: dict[str, Any], state: Any = None) -> Any:
        db_path = args.get("path", "")
        query = args.get("query", "")
        params = args.get("params", ())
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return ok({"affected_rows": affected, "executed": True})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("data.execute_sqlite", "Execute write/update SQL query on SQLite DB", Cap.WRITE, ("path", "query")), _data_execute_sqlite)

    def _data_convert_csv_to_json(args: dict[str, Any], state: Any = None) -> Any:
        csv_path = Path(args.get("csv_path", "")).expanduser().resolve()
        json_path = Path(args.get("json_path", str(csv_path.with_suffix(".json")))).expanduser().resolve()
        try:
            rows = []
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    rows.append(dict(r))
            json_path.parent.mkdir(parents=True, exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=2)
            return ok({"csv_path": str(csv_path), "json_path": str(json_path), "count": len(rows)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("data.convert_csv_to_json", "Convert CSV file directly into JSON format", Cap.WRITE, ("csv_path", "json_path")), _data_convert_csv_to_json)

    def _data_transform(args: dict[str, Any], state: Any = None) -> Any:
        """Local deterministic transforms on JSON-shaped data (no LLM)."""
        data = args.get("data")
        operation = str(args.get("operation", "")).lower()

        def _reject(msg: str) -> Any:
            return fail(msg)

        ops = {
            "keys": lambda d: list(d.keys()) if isinstance(d, dict) else _reject("keys requires an object"),
            "length": lambda d: len(d) if hasattr(d, "__len__") else _reject("length requires an array/object"),
            "unique": lambda d: sorted(set(d)) if isinstance(d, list) else _reject("unique requires an array"),
            "sort": lambda d: sorted(d) if isinstance(d, list) else _reject("sort requires an array"),
            "reverse": lambda d: list(reversed(d)) if isinstance(d, list) else _reject("reverse requires an array"),
            "first": lambda d: d[0] if isinstance(d, list) and d else _reject("empty array"),
            "last": lambda d: d[-1] if isinstance(d, list) and d else _reject("empty array"),
            "flatten": lambda d: [x for row in d for x in (row if isinstance(row, list) else [row])] if isinstance(d, list) else _reject("flatten requires an array"),
            "field": lambda d: [row.get(str(args.get("field_name", ""))) for row in d] if isinstance(d, list) and all(isinstance(r, dict) for r in d) else _reject("field requires an array of objects"),
        }
        if operation not in ops:
            return fail(f"unknown operation {operation!r}; supported: {sorted(ops)}")
        try:
            result = ops[operation](data)
            # inner _reject lambdas return a failed TaskResult — propagate it
            if isinstance(result, TaskResult):
                return result
            return ok({"operation": operation, "result": result})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("data.transform", "Local deterministic data transform (keys/length/unique/sort/reverse/first/last/flatten/field)", Cap.READ, ("data", "operation", "field_name")), _data_transform)

    def _data_dedupe(args: dict[str, Any], state: Any = None) -> Any:
        rows = args.get("rows", [])
        key = str(args.get("key", "")).strip()
        if not isinstance(rows, list):
            return fail("rows must be an array")
        seen = set()
        deduped = []
        for row in rows:
            if not isinstance(row, dict):
                deduped.append(row)
                continue
            k = str(row.get(key, "")) if key else json.dumps(row, sort_keys=True, default=str)
            if k in seen:
                continue
            seen.add(k)
            deduped.append(row)
        return ok({"rows": deduped, "count": len(deduped), "removed": len(rows) - len(deduped)})

    register_cap(registry, Cap("data.dedupe", "Remove duplicate rows (whole-row or by key field)", Cap.READ, ("rows", "key")), _data_dedupe)

    def _data_sort_rows(args: dict[str, Any], state: Any = None) -> Any:
        rows = args.get("rows", [])
        key = str(args.get("key", "")).strip()
        descending = bool(args.get("descending", False))
        if not isinstance(rows, list) or not rows:
            return fail("rows must be a non-empty array")
        if not all(isinstance(r, dict) for r in rows):
            return fail("sort_rows requires an array of objects")
        if not key:
            return fail("key is required (the column to sort by)")
        try:
            result = sorted(rows, key=lambda r: str(r.get(key, "")), reverse=descending)
            return ok({"rows": result, "count": len(result)})
        except Exception as e:
            return fail(str(e))

    register_cap(registry, Cap("data.sort_rows", "Sort array-of-objects rows by a key field", Cap.READ, ("rows", "key", "descending")), _data_sort_rows)

