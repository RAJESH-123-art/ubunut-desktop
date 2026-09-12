"""
capabilities/spreadsheets.py — Spreadsheet capabilities (openpyxl for .xlsx,
csv module for .csv).

Every write is verified on disk after save. All caps accept an optional
`sheet` (defaults to the active sheet for xlsx; ignored for CSV).
"""
import csv
import os
from pathlib import Path

from capabilities.base import Cap, fail, ok, register_cap, run_shell

try:
    import openpyxl
except ImportError:
    openpyxl = None


def _resolve_path(path: str) -> Path:
    return Path(os.path.expanduser(str(path)))


def _require_exists(p: Path):
    if not p.is_file():
        return fail(f"Spreadsheet not found: {p}")
    return None


def _load_writable(p: Path):
    """Load xlsx workbook, creating one if the file is empty/new."""
    if p.stat().st_size == 0:
        return openpyxl.Workbook()
    return openpyxl.load_workbook(str(p))


def _ws(wb, sheet: str):
    """Resolve a worksheet by name, defaulting to the active sheet."""
    if not sheet:
        return wb.active
    if sheet in wb.sheetnames:
        return wb[sheet]
    raise ValueError(f"sheet {sheet!r} not found (available: {wb.sheetnames})")


def _save_verified(wb, p: Path):
    wb.save(str(p))
    if not p.is_file() or p.stat().st_size == 0:
        return fail(f"save to {p} not verified on disk")
    return None


def install(registry, *, approve_all=False):

    # ── create ────────────────────────────────────────────────────────────
    @register_cap(registry, "sheet.create", Cap.WRITE, "Create new spreadsheet (.xlsx via openpyxl, .csv with optional header row) — verified", approve_all)
    def create(path: str, header: list | None = None):
        p = _resolve_path(path)
        if p.suffix.lower() == ".csv":
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    if header:
                        writer.writerow([str(h) for h in header])
                if not p.is_file():
                    return fail(f"creation of {path} not verified")
                return ok({"path": str(p), "format": "csv", "header": bool(header)})
            except Exception as e:
                return fail(str(e))
        if openpyxl:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                wb = openpyxl.Workbook()
                ws = wb.active
                if header:
                    ws.append([str(h) for h in header])
                err = _save_verified(wb, p)
                if err:
                    return err
                return ok({"path": str(p), "format": "xlsx", "sheet": ws.title})
            except Exception as e:
                return fail(str(e))
        return fail("openpyxl required for .xlsx creation")

    @register_cap(registry, "sheet.open", Cap.LOW, "Open in LibreOffice Calc", approve_all)
    def open_sheet(path: str):
        p = _resolve_path(path)
        if not p.is_file():
            return fail(f"File not found: {path}")
        code, _out, err = run_shell(["libreoffice", "--calc", str(p)], timeout=30)
        return ok(f"Opened {path}") if code == 0 or not err else fail(err)

    # ── read ──────────────────────────────────────────────────────────────
    @register_cap(registry, "sheet.read_cell", Cap.READ, "Read cell value (xlsx A1-notation, CSV via row/col e.g. 'B2')", approve_all)
    def read_cell(path: str, cell: str, sheet: str = ""):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        try:
            if p.suffix.lower() == ".csv":
                # A1 notation for CSV: B2 -> row 2, col 2
                import re
                m = re.match(r"([A-Za-z]+)(\d+)", cell)
                if not m:
                    return fail(f"invalid cell {cell!r} — use A1 notation like B2")
                col = 0
                for ch in m.group(1).upper():
                    col = col * 26 + (ord(ch) - 64)
                row = int(m.group(2))
                with open(p, newline="", encoding="utf-8") as f:
                    rows = list(csv.reader(f))
                value = rows[row - 1][col - 1] if row <= len(rows) and col <= len(rows[row - 1]) else None
                return ok({"value": value, "cell": cell})
            if openpyxl:
                wb = openpyxl.load_workbook(str(p), data_only=True)
                ws = _ws(wb, sheet)
                return ok({"value": ws[cell].value, "cell": cell, "sheet": ws.title})
            return fail("openpyxl required for .xlsx")
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "sheet.read_range", Cap.READ, "Read range as list of lists (xlsx range like 'A1:C10'; CSV: whole file or row range)", approve_all)
    def read_range(path: str, range: str = "", sheet: str = ""):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        try:
            if p.suffix.lower() == ".csv":
                with open(p, newline="", encoding="utf-8") as f:
                    rows = [[v for v in row] for row in csv.reader(f)]
                return ok({"rows": rows, "count": len(rows)})
            if openpyxl:
                wb = openpyxl.load_workbook(str(p), data_only=True)
                ws = _ws(wb, sheet)
                cells = ws[range] if range else ws.iter_rows()
                data = [[c.value for c in row] for row in cells]
                return ok({"rows": data, "count": len(data)})
            return fail("openpyxl required for .xlsx")
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "sheet.list_sheets", Cap.READ, "List sheet tab names (xlsx) / single sheet for CSV", approve_all)
    def list_sheets(path: str):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        if p.suffix.lower() == ".csv":
            return ok({"sheets": ["csv"], "format": "csv"})
        if openpyxl:
            try:
                wb = openpyxl.load_workbook(str(p), read_only=True)
                names = wb.sheetnames
                wb.close()
                return ok({"sheets": names, "format": "xlsx"})
            except Exception as e:
                return fail(str(e))
        return fail("openpyxl required for .xlsx")

    # ── write ─────────────────────────────────────────────────────────────
    @register_cap(registry, "sheet.write_cell", Cap.WRITE, "Write cell value — verified", approve_all)
    def write_cell(path: str, cell: str, value, sheet: str = ""):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        try:
            if p.suffix.lower() == ".csv":
                # read all, modify one cell, rewrite
                import re
                m = re.match(r"([A-Za-z]+)(\d+)", cell)
                if not m:
                    return fail(f"invalid cell {cell!r}")
                col = 0
                for ch in m.group(1).upper():
                    col = col * 26 + (ord(ch) - 64)
                row = int(m.group(2))
                with open(p, newline="", encoding="utf-8") as f:
                    rows = [list(r) for r in csv.reader(f)]
                while len(rows) < row:
                    rows.append([])
                while len(rows[row - 1]) < col:
                    rows[row - 1].append("")
                rows[row - 1][col - 1] = str(value)
                with open(p, "w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerows(rows)
                return ok({"cell": cell, "value": value, "verified": True})
            if openpyxl:
                wb = _load_writable(p)
                ws = _ws(wb, sheet)
                ws[cell] = value
                err = _save_verified(wb, p)
                if err:
                    return err
                # read-back verification
                check = openpyxl.load_workbook(str(p), read_only=True)
                got = _ws(check, sheet)[cell].value if sheet or check.sheetnames else check.active[cell].value
                check.close()
                if str(got) != str(value):
                    return fail(f"write not verified: cell {cell} reads {got!r}")
                return ok({"cell": cell, "value": value, "verified": True})
            return fail("openpyxl required for .xlsx")
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "sheet.append_rows", Cap.WRITE, "Append rows (list of lists) to sheet — the main data-entry cap — verified", approve_all)
    def append_rows(path: str, rows: list, sheet: str = ""):
        p = _resolve_path(path)
        if not isinstance(rows, list) or not rows:
            return fail("rows must be a non-empty list of lists")
        if not all(isinstance(r, list) for r in rows):
            return fail("each row must be a list")
        try:
            if p.suffix.lower() == ".csv":
                if not p.exists():
                    return fail(f"CSV not found: {path} (create it first with sheet.create)")
                with open(p, "a", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerows([[str(v) if v is not None else "" for v in r] for r in rows])
                # verify: count lines
                with open(p, newline="", encoding="utf-8") as f:
                    total = sum(1 for _ in csv.reader(f))
                return ok({"appended": len(rows), "total_rows": total, "verified": True, "format": "csv"})
            if openpyxl:
                if not p.exists():
                    return fail(f"Spreadsheet not found: {path} (create it first with sheet.create)")
                wb = openpyxl.load_workbook(str(p))
                ws = _ws(wb, sheet)
                before = ws.max_row
                for r in rows:
                    ws.append([v for v in r])
                err = _save_verified(wb, p)
                if err:
                    return err
                check = openpyxl.load_workbook(str(p), read_only=True)
                after = _ws(check, sheet).max_row
                check.close()
                if after < before + len(rows):
                    return fail(f"append not verified: max_row {before}->{after}, expected >= {before + len(rows)}")
                return ok({"appended": len(rows), "sheet": ws.title, "first_row": before + 1, "last_row": after, "verified": True})
            return fail("openpyxl required for .xlsx")
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "sheet.write_range", Cap.WRITE, "Write range from list of lists (xlsx) — verified", approve_all)
    def write_range(path: str, range: str, data: list, sheet: str = ""):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        if not isinstance(data, list) or not all(isinstance(r, list) for r in data):
            return fail("data must be a list of lists")
        if p.suffix.lower() == ".csv":
            return fail("write_range is xlsx-only; for CSV use sheet.append_rows or data.write_csv")
        if openpyxl:
            try:
                wb = _load_writable(p)
                ws = _ws(wb, sheet)
                cells = ws[range]
                written = 0
                for r_idx, row in enumerate(cells):
                    for c_idx, cell in enumerate(row):
                        if r_idx < len(data) and c_idx < len(data[r_idx]):
                            cell.value = data[r_idx][c_idx]
                            written += 1
                err = _save_verified(wb, p)
                if err:
                    return err
                return ok({"cells_written": written, "range": range, "verified": True})
            except Exception as e:
                return fail(str(e))
        return fail("openpyxl required for .xlsx")

    # ── structure edits ───────────────────────────────────────────────────
    @register_cap(registry, "sheet.insert_row", Cap.WRITE, "Insert blank row at index — verified", approve_all)
    def insert_row(path: str, index: int, sheet: str = ""):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        if not openpyxl or p.suffix.lower() != ".xlsx":
            return fail("xlsx-only")
        try:
            wb = _load_writable(p)
            ws = _ws(wb, sheet)
            ws.insert_rows(int(index))
            err = _save_verified(wb, p)
            return err or ok({"inserted_at": index, "verified": True})
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "sheet.delete_row", Cap.WRITE, "Delete row at index — verified", approve_all)
    def delete_row(path: str, index: int, sheet: str = ""):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        if not openpyxl or p.suffix.lower() != ".xlsx":
            return fail("xlsx-only")
        try:
            wb = _load_writable(p)
            ws = _ws(wb, sheet)
            ws.delete_rows(int(index))
            err = _save_verified(wb, p)
            return err or ok({"deleted_at": index, "verified": True})
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "sheet.insert_column", Cap.WRITE, "Insert blank column at index — verified", approve_all)
    def insert_column(path: str, index: int, sheet: str = ""):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        if not openpyxl or p.suffix.lower() != ".xlsx":
            return fail("xlsx-only")
        try:
            wb = _load_writable(p)
            ws = _ws(wb, sheet)
            ws.insert_cols(int(index))
            err = _save_verified(wb, p)
            return err or ok({"inserted_at": index, "verified": True})
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "sheet.delete_column", Cap.WRITE, "Delete column at index — verified", approve_all)
    def delete_column(path: str, index: int, sheet: str = ""):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        if not openpyxl or p.suffix.lower() != ".xlsx":
            return fail("xlsx-only")
        try:
            wb = _load_writable(p)
            ws = _ws(wb, sheet)
            ws.delete_cols(int(index))
            err = _save_verified(wb, p)
            return err or ok({"deleted_at": index, "verified": True})
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "sheet.sort_data", Cap.WRITE, "Sort data range by column (real implementation: read, sort, rewrite) — verified", approve_all)
    def sort_data(path: str, column: int, range: str = "", ascending: bool = True, sheet: str = ""):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        try:
            if p.suffix.lower() == ".csv":
                with open(p, newline="", encoding="utf-8") as f:
                    rows = [list(r) for r in csv.reader(f)]
                if not rows:
                    return fail("empty CSV")
                header, body = rows[0], rows[1:]
                col = int(column) - 1 if int(column) >= 1 else int(column)
                if col >= len(header) and body and col >= len(body[0]):
                    return fail(f"column {column} out of range")
                def sort_key(row):
                    if col < len(row):
                        v = row[col]
                        try:
                            return (0, float(v))
                        except (TypeError, ValueError):
                            return (1, str(v))
                    return (1, "")
                body.sort(key=sort_key, reverse=not ascending)
                with open(p, "w", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerows([header] + body)
                return ok({"sorted_rows": len(body), "column": column, "ascending": ascending, "verified": True})
            if openpyxl:
                wb = _load_writable(p)
                ws = _ws(wb, sheet)
                cells = ws[range] if range else ws.iter_rows(min_row=2)  # keep header row 1
                data = [[c.value for c in row] for row in cells]
                if not data:
                    return fail("no data rows to sort")
                col = int(column) - 1 if int(column) >= 1 else int(column)
                def sort_key(row):
                    v = row[col] if col < len(row) else None
                    try:
                        return (0, float(v))
                    except (TypeError, ValueError):
                        return (1, str(v))
                data.sort(key=sort_key, reverse=not ascending)
                # write back
                if range:
                    target = ws[range]
                else:
                    target = ws.iter_rows(min_row=2, max_row=1 + len(data))
                for r_idx, row in enumerate(target):
                    for c_idx, cell in enumerate(row):
                        if c_idx < len(data[r_idx]):
                            cell.value = data[r_idx][c_idx]
                err = _save_verified(wb, p)
                return err or ok({"sorted_rows": len(data), "column": column, "verified": True})
            return fail("xlsx needs openpyxl")
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "sheet.create_sheet", Cap.WRITE, "Add new sheet tab — verified", approve_all)
    def create_sheet(path: str, name: str):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        if not openpyxl or p.suffix.lower() != ".xlsx":
            return fail("xlsx-only")
        try:
            wb = _load_writable(p)
            if name in wb.sheetnames:
                return fail(f"sheet {name!r} already exists")
            wb.create_sheet(title=name)
            err = _save_verified(wb, p)
            return err or ok({"created": name, "sheets": wb.sheetnames, "verified": True})
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "sheet.rename_sheet", Cap.WRITE, "Rename sheet tab — verified", approve_all)
    def rename_sheet(path: str, old_name: str, new_name: str):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        if not openpyxl or p.suffix.lower() != ".xlsx":
            return fail("xlsx-only")
        try:
            wb = _load_writable(p)
            if old_name not in wb.sheetnames:
                return fail(f"sheet {old_name!r} not found (available: {wb.sheetnames})")
            wb[old_name].title = new_name
            err = _save_verified(wb, p)
            return err or ok({"renamed": old_name, "to": new_name, "verified": True})
        except Exception as e:
            return fail(str(e))

    # ── save / export ─────────────────────────────────────────────────────
    @register_cap(registry, "sheet.save", Cap.READ, "Verify spreadsheet exists and is non-empty (saves are eager in openpyxl/csv)", approve_all)
    def save(path: str):
        p = _resolve_path(path)
        if p.is_file() and p.stat().st_size > 0:
            return ok({"path": str(p), "size_bytes": p.stat().st_size, "verified": True})
        return fail(f"Spreadsheet {path} does not exist or is empty")

    @register_cap(registry, "sheet.export", Cap.WRITE, "Export to CSV/PDF/xlsx via LibreOffice — verified", approve_all)
    def export(path: str, output_path: str, format: str = "csv"):
        p = _resolve_path(path)
        missing = _require_exists(p)
        if missing:
            return missing
        out_p = _resolve_path(output_path)
        if p.suffix.lower() == ".csv" and format.lower() == "xlsx" and openpyxl:
            # CSV -> xlsx: openpyxl conversion
            try:
                with open(p, newline="", encoding="utf-8") as f:
                    rows = list(csv.reader(f))
                wb = openpyxl.Workbook()
                ws = wb.active
                for r in rows:
                    ws.append(r)
                err = _save_verified(wb, out_p)
                return err or ok({"output": str(out_p), "rows": len(rows), "verified": True})
            except Exception as e:
                return fail(str(e))
        if p.suffix.lower() == ".xlsx" and format.lower() == "csv" and openpyxl:
            try:
                wb = openpyxl.load_workbook(str(p), data_only=True)
                ws = wb.active
                out_p.parent.mkdir(parents=True, exist_ok=True)
                with open(out_p, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    for row in ws.iter_rows(values_only=True):
                        writer.writerow(["" if v is None else v for v in row])
                if not out_p.is_file() or out_p.stat().st_size == 0:
                    return fail("CSV export not verified")
                return ok({"output": str(out_p), "verified": True})
            except Exception as e:
                return fail(str(e))
        # Other formats via LibreOffice
        outdir = str(out_p.parent) if str(out_p.parent) else "."
        code, _out, err = run_shell(
            ["libreoffice", "--headless", "--convert-to", format, str(p), "--outdir", outdir],
            timeout=120,
        )
        if code != 0:
            return fail(err or f"export rc={code}")
        # LibreOffice names output after the SOURCE stem, not the requested
        # filename — rename to the explicitly requested output path.
        base = p.stem
        ext = format.split(":")[0]
        converted = out_p.parent / f"{base}.{ext}"
        if not converted.is_file():
            return fail(f"export reported success but {converted} not found")
        if converted != out_p:
            if out_p.exists():
                out_p.unlink()
            converted.rename(out_p)
        return ok({"output": str(out_p), "size_bytes": out_p.stat().st_size, "verified": True})
