"""
capabilities/documents.py — Document capabilities (python-docx + LibreOffice fallback).

All results are real TaskResult objects with verified writes. The legacy
`.is_ok`/`.value` calls (which never existed on TaskResult/tuples) are gone.
"""
import os

from capabilities.base import Cap, fail, ok, register_cap, run_shell

try:
    import docx
except ImportError:
    docx = None


def install(registry, *, approve_all=False):
    @register_cap(registry, "doc.create", Cap.WRITE, "Create new Writer document (verified)", approve_all)
    def create(path: str | None = None):
        if path is None:
            return fail("Path is required")
        try:
            if docx and path.endswith(".docx"):
                document = docx.Document()
                document.save(path)
            else:
                with open(path, "w") as f:
                    f.write("")
            if not os.path.exists(path):
                return fail(f"creation of {path} not verified")
            return ok(f"Created {path} (verified)")
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "doc.open", Cap.LOW, "Open document in LibreOffice", approve_all)
    def open_doc(path: str):
        if not os.path.exists(path):
            return fail(f"File not found: {path}")
        code, _out, err = run_shell(["libreoffice", path], timeout=30)
        return ok(f"Opened {path}") if code in (0,) or not err else fail(err)

    @register_cap(registry, "doc.read", Cap.READ, "Extract text from document", approve_all)
    def read_doc(path: str):
        if not os.path.exists(path):
            return fail(f"File not found: {path}")
        if docx and path.endswith(".docx"):
            try:
                document = docx.Document(path)
                text = "\n".join([p.text for p in document.paragraphs])
                return ok(text)
            except Exception as e:
                return fail(str(e))
        # Non-docx: LibreOffice headless conversion fallback
        code, _out, err = run_shell(
            ["libreoffice", "--headless", "--convert-to", "txt", path, "--outdir", "/tmp"],
            timeout=60,
        )
        if code == 0:
            txt_path = f"/tmp/{os.path.splitext(os.path.basename(path))[0]}.txt"
            if os.path.exists(txt_path):
                try:
                    with open(txt_path, "r") as f:
                        text = f.read()
                    return ok(text)
                except Exception as e:
                    return fail(str(e))
        return fail(f"Failed to read document: {err or 'conversion produced no output'}")

    @register_cap(registry, "doc.write", Cap.WRITE, "Write/replace text (verified)", approve_all)
    def write_doc(path: str, content: str):
        try:
            if docx and path.endswith(".docx"):
                document = docx.Document()
                document.add_paragraph(content)
                document.save(path)
            else:
                with open(path, "w") as f:
                    f.write(content)
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                return fail(f"write to {path} not verified")
            return ok(f"Wrote to {path} (verified, {os.path.getsize(path)} bytes)")
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "doc.append", Cap.WRITE, "Append text to end (verified)", approve_all)
    def append_doc(path: str, content: str):
        try:
            if docx and path.endswith(".docx"):
                document = docx.Document(path) if os.path.exists(path) else docx.Document()
                document.add_paragraph(content)
                document.save(path)
            else:
                with open(path, "a") as f:
                    f.write(content)
            if not os.path.exists(path):
                return fail(f"append to {path} not verified")
            return ok(f"Appended to {path} (verified)")
        except Exception as e:
            return fail(str(e))

    @register_cap(registry, "doc.find_text", Cap.READ, "Find text in document", approve_all)
    def find_text(path: str, query: str):
        res = read_doc(path)
        if res.ok:  # TaskResult.ok property — real success check
            text = str(res.data) if res.data is not None else ""
            if query in text:
                return ok(f"Found '{query}' in {path}")
            return ok(f"'{query}' not found in {path}")
        return res

    @register_cap(registry, "doc.replace_text", Cap.WRITE, "Find & replace text (verified)", approve_all)
    def replace_text(path: str, find: str, replace: str):
        if docx and path.endswith(".docx"):
            try:
                document = docx.Document(path)
                count = 0
                for p in document.paragraphs:
                    if find in p.text:
                        p.text = p.text.replace(find, replace)
                        count += 1
                document.save(path)
                # verify replacement landed
                check = docx.Document(path)
                combined = "\n".join(p.text for p in check.paragraphs)
                if replace not in combined:
                    return fail("replacement not verified after save")
                return ok(f"Replaced {count} occurrence(s) in {path} (verified)")
            except Exception as e:
                return fail(str(e))
        return fail("Only docx replacement supported currently")

    @register_cap(registry, "doc.save", Cap.WRITE, "Verify document exists on disk", approve_all)
    def save_doc(path: str):
        # python-docx saves eagerly at write time; this cap now VERIFIES the
        # file exists instead of blindly claiming success.
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return ok(f"Verified {path} ({os.path.getsize(path)} bytes)")
        return fail(f"Document {path} does not exist or is empty")

    @register_cap(registry, "doc.export", Cap.WRITE, "Export to PDF/other format (verified)", approve_all)
    def export_doc(path: str, output_path: str, format: str):
        if not os.path.exists(path):
            return fail(f"File not found: {path}")
        outdir = os.path.dirname(output_path) or "."
        code, _out, err = run_shell(
            ["libreoffice", "--headless", "--convert-to", format, path, "--outdir", outdir],
            timeout=120,
        )
        if code != 0:
            return fail(err or f"export rc={code}")
        base = os.path.splitext(os.path.basename(path))[0]
        ext = format.split(":")[0] if ":" in format else format
        converted = os.path.join(outdir, f"{base}.{ext}")
        if os.path.exists(converted):
            return ok({"output": converted, "size_bytes": os.path.getsize(converted)})
        return fail(f"export reported success but {converted} not found")

    @register_cap(registry, "doc.print", Cap.COMMUNICATION, "Print document", approve_all)
    def print_doc(path: str, printer: str):
        if not os.path.exists(path):
            return fail(f"File not found: {path}")
        code, out, err = run_shell(["lp", "-d", printer, path], timeout=30)
        return ok({"printed": path, "printer": printer, "job": out.strip()}) if code == 0 else fail(err)

    @register_cap(registry, "doc.get_word_count", Cap.READ, "Count words", approve_all)
    def get_word_count(path: str):
        res = read_doc(path)
        if res.ok:
            count = len(str(res.data if res.data is not None else "").split())
            return ok(str(count))
        return res
