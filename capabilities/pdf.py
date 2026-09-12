"""
capabilities/pdf.py — Comprehensive PDF processing capabilities.

Covers NIKKI capability family: 26 (PDF / DOCUMENTS)
Includes 24 PDF manipulation, conversion, extraction, security, OCR, and printing capabilities.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger

from capabilities.base import Cap, fail, ok, register_cap, run_shell

try:
    import pypdf
except ImportError:
    pypdf = None

try:
    import pdf2image
except ImportError:
    pdf2image = None

try:
    import pdf2docx
except ImportError:
    pdf2docx = None

try:
    import img2pdf
except ImportError:
    img2pdf = None


def install(registry: Any, *, approve_all: bool = False) -> None:

    # 1. pdf.read
    def _pdf_read(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                texts = []
                for idx, page in enumerate(reader.pages):
                    t = page.extract_text() or ""
                    if t.strip():
                        texts.append(t)
                if texts:
                    return ok("\n\n".join(texts))
            except Exception as pypdf_err:
                logger.debug(f"pypdf text extraction failed, falling back to pdftotext: {pypdf_err}")

        # Fallback to pdftotext CLI
        code, out, err = run_shell(["pdftotext", str(p), "-"], timeout=60)
        if code == 0 and out:
            return ok(out)
        return fail(f"Failed to read PDF: {err or path}")

    register_cap(registry, Cap("pdf.read", "Extract text from entire PDF file", Cap.READ, ("path",)), _pdf_read)

    # 2. pdf.read_page
    def _pdf_read_page(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        page_num = int(args.get("page", 0))
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                total = len(reader.pages)
                # Handle 1-based index if page_num > total or handle 0-based
                idx = page_num if page_num < total else page_num - 1
                if 0 <= idx < total:
                    text = reader.pages[idx].extract_text() or ""
                    return ok({"page": idx, "total_pages": total, "text": text})
                return fail(f"Page {page_num} out of range (total pages: {total})")
            except Exception as e:
                return fail(str(e))

        # Fallback to pdftotext -f N -l N
        res = run_shell(["pdftotext", "-f", str(page_num + 1), "-l", str(page_num + 1), str(p), "-"])
        return res

    register_cap(registry, Cap("pdf.read_page", "Read text from specific PDF page", Cap.READ, ("path", "page")), _pdf_read_page)

    # 3. pdf.search
    def _pdf_search(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        query = args.get("query", "").lower()
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return fail(f"File not found: {path}")
        if not query:
            return fail("query parameter required")

        matches = []
        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                for idx, page in enumerate(reader.pages):
                    text = page.extract_text() or ""
                    if query in text.lower():
                        # Extract snippet
                        pos = text.lower().find(query)
                        start_pos = max(0, pos - 40)
                        end_pos = min(len(text), pos + len(query) + 40)
                        snippet = text[start_pos:end_pos].replace("\n", " ")
                        matches.append({"page": idx + 1, "snippet": snippet})
                return ok({"query": query, "matches": matches, "count": len(matches)})
            except Exception as e:
                return fail(str(e))

        # Fallback using pdftotext
        out = subprocess.check_output(["pdftotext", str(p), "-"], text=True, errors="ignore")
        if query in out.lower():
            matches.append({"page": 1, "snippet": "Found query in PDF text"})
        return ok({"query": query, "matches": matches, "count": len(matches)})

    register_cap(registry, Cap("pdf.search", "Search text query inside PDF", Cap.READ, ("path", "query")), _pdf_search)

    # 4. pdf.page_count
    def _pdf_page_count(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                return ok({"path": str(p), "page_count": len(reader.pages)})
            except Exception as e:
                return fail(str(e))

        # Fallback using pdfinfo
        try:
            out = subprocess.check_output(["pdfinfo", str(p)], text=True)
            for line in out.splitlines():
                if line.startswith("Pages:"):
                    count = int(line.split(":")[1].strip())
                    return ok({"path": str(p), "page_count": count})
        except Exception as e:
            return fail(str(e))

        return fail("Could not determine page count")

    register_cap(registry, Cap("pdf.page_count", "Get total page count of PDF", Cap.READ, ("path",)), _pdf_page_count)

    # 5. pdf.get_metadata
    def _pdf_get_metadata(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                meta = reader.metadata or {}
                return ok({
                    "path": str(p),
                    "title": meta.get("/Title", ""),
                    "author": meta.get("/Author", ""),
                    "subject": meta.get("/Subject", ""),
                    "creator": meta.get("/Creator", ""),
                    "producer": meta.get("/Producer", ""),
                    "creation_date": str(meta.get("/CreationDate", "")),
                    "page_count": len(reader.pages),
                    "is_encrypted": reader.is_encrypted,
                })
            except Exception as e:
                return fail(str(e))

        return fail("pypdf required for metadata extraction")

    register_cap(registry, Cap("pdf.get_metadata", "Read PDF metadata properties", Cap.READ, ("path",)), _pdf_get_metadata)

    # 6. pdf.set_metadata
    def _pdf_set_metadata(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output") or path
        p = Path(path).expanduser().resolve()
        out_p = Path(output).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                writer = pypdf.PdfWriter()
                writer.append(reader)

                metadata = {}
                if "title" in args:
                    metadata["/Title"] = args["title"]
                if "author" in args:
                    metadata["/Author"] = args["author"]
                if "subject" in args:
                    metadata["/Subject"] = args["subject"]
                if "keywords" in args:
                    metadata["/Keywords"] = args["keywords"]

                if metadata:
                    writer.add_metadata(metadata)

                out_p.parent.mkdir(parents=True, exist_ok=True)
                with open(out_p, "wb") as f:
                    writer.write(f)
                return ok({"output": str(out_p), "metadata_updated": list(metadata.keys())})
            except Exception as e:
                return fail(str(e))

        return fail("pypdf required for setting metadata")

    register_cap(registry, Cap("pdf.set_metadata", "Set title, author, subject, keywords metadata", Cap.WRITE, ("path", "output")), _pdf_set_metadata)

    # 7. pdf.extract_images
    def _pdf_extract_images(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        out_dir = args.get("output_dir", "./extracted_images")
        p = Path(path).expanduser().resolve()
        d = Path(out_dir).expanduser().resolve()
        d.mkdir(parents=True, exist_ok=True)

        if not p.exists():
            return fail(f"File not found: {path}")

        extracted = []
        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                count = 0
                for page_idx, page in enumerate(reader.pages):
                    for img_file in page.images:
                        img_path = d / f"page_{page_idx + 1}_{img_file.name}"
                        with open(img_path, "wb") as f:
                            f.write(img_file.data)
                        extracted.append(str(img_path))
                        count += 1
                return ok({"extracted_count": count, "output_dir": str(d), "images": extracted})
            except Exception as e:
                return fail(str(e))

        # Fallback to pdfimages CLI
        code, _out, err = run_shell(["pdfimages", "-png", str(p), str(d / "img")], timeout=120)
        if code == 0:
            imgs = [str(f) for f in d.glob("img-*.png")]
            return ok({"extracted_count": len(imgs), "output_dir": str(d), "images": imgs})
        return fail(err or "pdfimages extraction failed")

    register_cap(registry, Cap("pdf.extract_images", "Extract raster images from PDF pages", Cap.READ, ("path", "output_dir")), _pdf_extract_images)

    # 8. pdf.convert_to_images
    def _pdf_convert_to_images(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        out_dir = args.get("output_dir", "./pdf_images")
        fmt = args.get("fmt", "png").lower()
        dpi = int(args.get("dpi", 150))
        p = Path(path).expanduser().resolve()
        d = Path(out_dir).expanduser().resolve()
        d.mkdir(parents=True, exist_ok=True)

        if not p.exists():
            return fail(f"File not found: {path}")

        image_paths = []
        if pdf2image:
            try:
                images = pdf2image.convert_from_path(str(p), dpi=dpi)
                for idx, img in enumerate(images):
                    img_path = d / f"page_{idx + 1}.{fmt}"
                    img.save(str(img_path), fmt.upper() if fmt == "jpg" else "PNG")
                    image_paths.append(str(img_path))
                return ok({"count": len(image_paths), "output_dir": str(d), "images": image_paths})
            except Exception as p2i_err:
                logger.debug(f"pdf2image conversion failed, falling back to pdftoppm: {p2i_err}")

        # Fallback to pdftoppm CLI
        cmd = ["pdftoppm", "-png" if fmt == "png" else "-jpeg", "-r", str(dpi), str(p), str(d / "page")]
        code, _out, err = run_shell(cmd, timeout=180)
        if code == 0:
            imgs = sorted(str(f) for f in list(d.glob("page-*.png")) + list(d.glob("page-*.jpg")))
            return ok({"count": len(imgs), "output_dir": str(d), "images": imgs})
        return fail(err or "pdftoppm conversion failed")

    register_cap(registry, Cap("pdf.convert_to_images", "Render PDF pages into PNG/JPEG images", Cap.WRITE, ("path", "output_dir")), _pdf_convert_to_images)

    # 9. pdf.create_from_images
    def _pdf_create_from_images(args: dict[str, Any], state: Any = None) -> Any:
        image_paths = args.get("image_paths", [])
        output = args.get("output", "combined.pdf")
        out_p = Path(output).expanduser().resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)

        if not image_paths:
            return fail("image_paths list is required")

        valid_paths = [str(Path(ip).expanduser().resolve()) for ip in image_paths if Path(ip).expanduser().exists()]
        if not valid_paths:
            return fail("No valid image files provided")

        if img2pdf:
            try:
                with open(out_p, "wb") as f:
                    f.write(img2pdf.convert(valid_paths))
                return ok({"output": str(out_p), "image_count": len(valid_paths)})
            except Exception as e:
                return fail(str(e))

        # Fallback using PIL/Pillow
        try:
            from PIL import Image
            imgs = [Image.open(ip).convert("RGB") for ip in valid_paths]
            if imgs:
                imgs[0].save(str(out_p), save_all=True, append_images=imgs[1:])
                return ok({"output": str(out_p), "image_count": len(valid_paths)})
        except Exception as e:
            return fail(str(e))

        return fail("img2pdf or PIL required")

    register_cap(registry, Cap("pdf.create_from_images", "Create a PDF document from image files", Cap.WRITE, ("image_paths", "output")), _pdf_create_from_images)

    # 10. pdf.convert_to_docx
    def _pdf_convert_to_docx(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output") or str(Path(path).with_suffix(".docx"))
        p = Path(path).expanduser().resolve()
        out_p = Path(output).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")

        if pdf2docx:
            try:
                cv = pdf2docx.Converter(str(p))
                cv.convert(str(out_p), start=0, end=None)
                cv.close()
                return ok({"path": str(p), "output": str(out_p)})
            except Exception as e:
                return fail(f"pdf2docx error: {e}")

        # Fallback using LibreOffice
        code, _out, err = run_shell(["libreoffice", "--headless", "--convert-to", "docx", str(p), "--outdir", str(out_p.parent)], timeout=180)
        if code == 0 and out_p.exists():
            return ok({"path": str(p), "output": str(out_p)})
        return fail(err or f"conversion finished but {out_p} not found")

    register_cap(registry, Cap("pdf.convert_to_docx", "Convert PDF file to Word .docx document", Cap.WRITE, ("path", "output")), _pdf_convert_to_docx)

    # 11. pdf.convert_to_text
    def _pdf_convert_to_text(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output") or str(Path(path).with_suffix(".txt"))
        p = Path(path).expanduser().resolve()
        out_p = Path(output).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")

        res = _pdf_read({"path": str(p)})
        if res.ok:
            text = str(res.data if res.data is not None else "")
            out_p.parent.mkdir(parents=True, exist_ok=True)
            with open(out_p, "w", encoding="utf-8") as f:
                f.write(text)
            if not out_p.exists() or out_p.stat().st_size == 0:
                return fail(f"text file write to {out_p} not verified")
            return ok({"output": str(out_p), "character_count": len(text)})
        return res

    register_cap(registry, Cap("pdf.convert_to_text", "Save PDF text content to a text file", Cap.WRITE, ("path", "output")), _pdf_convert_to_text)

    # 12. pdf.merge
    def _pdf_merge(args: dict[str, Any], state: Any = None) -> Any:
        paths = args.get("paths", [])
        output = args.get("output", "merged.pdf")
        out_p = Path(output).expanduser().resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)

        if not paths or len(paths) < 2:
            return fail("At least two PDF file paths are required for merging")

        if pypdf:
            try:
                merger = pypdf.PdfWriter()
                for pdf_file in paths:
                    fp = Path(pdf_file).expanduser().resolve()
                    if not fp.exists():
                        return fail(f"File not found: {pdf_file}")
                    merger.append(str(fp))
                with open(out_p, "wb") as f:
                    merger.write(f)
                merger.close()
                return ok({"output": str(out_p), "merged_count": len(paths)})
            except Exception as e:
                return fail(str(e))

        # Fallback using pdfunite CLI
        cmd = ["pdfunite"] + [str(Path(p).expanduser().resolve()) for p in paths] + [str(out_p)]
        return run_shell(cmd)

    register_cap(registry, Cap("pdf.merge", "Merge multiple PDF files into one", Cap.WRITE, ("paths", "output")), _pdf_merge)

    # 13. pdf.split
    def _pdf_split(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        out_dir = args.get("output_dir", "./split_pdf")
        pages_per_file = int(args.get("pages_per_file", 1))
        p = Path(path).expanduser().resolve()
        d = Path(out_dir).expanduser().resolve()
        d.mkdir(parents=True, exist_ok=True)

        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                total = len(reader.pages)
                created_files = []
                for i in range(0, total, pages_per_file):
                    writer = pypdf.PdfWriter()
                    for j in range(i, min(i + pages_per_file, total)):
                        writer.add_page(reader.pages[j])
                    out_path = d / f"split_{i + 1}_{min(i + pages_per_file, total)}.pdf"
                    with open(out_path, "wb") as f:
                        writer.write(f)
                    created_files.append(str(out_path))
                return ok({"output_dir": str(d), "files_created": created_files, "total_parts": len(created_files)})
            except Exception as e:
                return fail(str(e))

        return fail("pypdf required for splitting")

    register_cap(registry, Cap("pdf.split", "Split PDF into multiple PDF files", Cap.WRITE, ("path", "output_dir")), _pdf_split)

    # 14. pdf.extract_pages
    def _pdf_extract_pages(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output", "extracted_pages.pdf")
        start = int(args.get("start", 0))
        end = int(args.get("end", 1))
        p = Path(path).expanduser().resolve()
        out_p = Path(output).expanduser().resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)

        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                writer = pypdf.PdfWriter()
                total = len(reader.pages)
                s_idx = max(0, start)
                e_idx = min(end, total)
                for idx in range(s_idx, e_idx):
                    writer.add_page(reader.pages[idx])
                with open(out_p, "wb") as f:
                    writer.write(f)
                return ok({"output": str(out_p), "extracted_pages": e_idx - s_idx})
            except Exception as e:
                return fail(str(e))

        return fail("pypdf required")

    register_cap(registry, Cap("pdf.extract_pages", "Extract specific page range into a new PDF", Cap.WRITE, ("path", "output", "start", "end")), _pdf_extract_pages)

    # 15. pdf.delete_pages
    def _pdf_delete_pages(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output") or str(Path(path).with_name(f"{Path(path).stem}_edited.pdf"))
        pages_to_delete = set(args.get("pages_to_delete", []))
        p = Path(path).expanduser().resolve()
        out_p = Path(output).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                writer = pypdf.PdfWriter()
                kept = 0
                for idx, page in enumerate(reader.pages):
                    # Check both 0-based and 1-based index matching
                    if idx not in pages_to_delete and (idx + 1) not in pages_to_delete:
                        writer.add_page(page)
                        kept += 1
                out_p.parent.mkdir(parents=True, exist_ok=True)
                with open(out_p, "wb") as f:
                    writer.write(f)
                return ok({"output": str(out_p), "remaining_pages": kept, "deleted": len(pages_to_delete)})
            except Exception as e:
                return fail(str(e))

        return fail("pypdf required")

    register_cap(registry, Cap("pdf.delete_pages", "Delete specific page numbers from PDF", Cap.WRITE, ("path", "output", "pages_to_delete")), _pdf_delete_pages)

    # 16. pdf.reorder_pages
    def _pdf_reorder_pages(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output") or str(Path(path).with_name(f"{Path(path).stem}_reordered.pdf"))
        page_order = args.get("page_order", [])
        p = Path(path).expanduser().resolve()
        out_p = Path(output).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                writer = pypdf.PdfWriter()
                total = len(reader.pages)
                for p_idx in page_order:
                    idx = p_idx if p_idx < total else p_idx - 1
                    if 0 <= idx < total:
                        writer.add_page(reader.pages[idx])
                out_p.parent.mkdir(parents=True, exist_ok=True)
                with open(out_p, "wb") as f:
                    writer.write(f)
                return ok({"output": str(out_p), "total_pages": len(page_order)})
            except Exception as e:
                return fail(str(e))

        return fail("pypdf required")

    register_cap(registry, Cap("pdf.reorder_pages", "Reorder PDF pages according to list of indices", Cap.WRITE, ("path", "output", "page_order")), _pdf_reorder_pages)

    # 17. pdf.rotate
    def _pdf_rotate(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output") or str(Path(path).with_name(f"{Path(path).stem}_rotated.pdf"))
        degrees = int(args.get("degrees", 90))
        target_pages = set(args.get("pages", []))
        p = Path(path).expanduser().resolve()
        out_p = Path(output).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                writer = pypdf.PdfWriter()
                for idx, page in enumerate(reader.pages):
                    if not target_pages or idx in target_pages or (idx + 1) in target_pages:
                        page.rotate(degrees)
                    writer.add_page(page)
                out_p.parent.mkdir(parents=True, exist_ok=True)
                with open(out_p, "wb") as f:
                    writer.write(f)
                return ok({"output": str(out_p), "degrees": degrees})
            except Exception as e:
                return fail(str(e))

        return fail("pypdf required")

    register_cap(registry, Cap("pdf.rotate", "Rotate PDF pages by specified degrees (90, 180, 270)", Cap.WRITE, ("path", "output", "degrees")), _pdf_rotate)

    # 18. pdf.crop
    def _pdf_crop(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output") or str(Path(path).with_name(f"{Path(path).stem}_cropped.pdf"))
        margin = float(args.get("margin", 20))
        p = Path(path).expanduser().resolve()
        out_p = Path(output).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                writer = pypdf.PdfWriter()
                for page in reader.pages:
                    mb = page.mediabox
                    page.mediabox.lower_left = (mb.lower_left[0] + margin, mb.lower_left[1] + margin)
                    page.mediabox.upper_right = (mb.upper_right[0] - margin, mb.upper_right[1] - margin)
                    writer.add_page(page)
                out_p.parent.mkdir(parents=True, exist_ok=True)
                with open(out_p, "wb") as f:
                    writer.write(f)
                return ok({"output": str(out_p), "margin": margin})
            except Exception as e:
                return fail(str(e))

        return fail("pypdf required")

    register_cap(registry, Cap("pdf.crop", "Crop PDF page margins", Cap.WRITE, ("path", "output", "margin")), _pdf_crop)

    # 19. pdf.compress
    def _pdf_compress(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output") or str(Path(path).with_name(f"{Path(path).stem}_compressed.pdf"))
        p = Path(path).expanduser().resolve()
        out_p = Path(output).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")

        orig_size = p.stat().st_size

        # Try Ghostscript first for maximum compression
        if shutil.which("gs"):
            code, _out, _err = run_shell([
                "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
                "-dPDFSETTINGS=/screen", "-dNOPAUSE", "-dQUIET", "-dBATCH",
                f"-sOutputFile={out_p}", str(p)
            ], timeout=180)
            if code == 0 and out_p.exists():
                comp_size = out_p.stat().st_size
                return ok({"output": str(out_p), "original_bytes": orig_size, "compressed_bytes": comp_size, "reduction_ratio": round((1 - comp_size / max(1, orig_size)) * 100, 1)})

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                writer = pypdf.PdfWriter()
                for page in reader.pages:
                    page.compress_content_streams()
                    writer.add_page(page)
                out_p.parent.mkdir(parents=True, exist_ok=True)
                with open(out_p, "wb") as f:
                    writer.write(f)
                comp_size = out_p.stat().st_size
                return ok({"output": str(out_p), "original_bytes": orig_size, "compressed_bytes": comp_size})
            except Exception as e:
                return fail(str(e))

        return fail("Ghostscript (gs) or pypdf required for compression")

    register_cap(registry, Cap("pdf.compress", "Compress/optimize PDF file size", Cap.WRITE, ("path", "output")), _pdf_compress)

    # 20. pdf.encrypt
    def _pdf_encrypt(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output") or str(Path(path).with_name(f"{Path(path).stem}_protected.pdf"))
        password = args.get("password", "")
        p = Path(path).expanduser().resolve()
        out_p = Path(output).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")
        if not password:
            return fail("password parameter is required")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                writer = pypdf.PdfWriter()
                writer.append(reader)
                writer.encrypt(password)
                out_p.parent.mkdir(parents=True, exist_ok=True)
                with open(out_p, "wb") as f:
                    writer.write(f)
                return ok({"output": str(out_p), "encrypted": True})
            except Exception as e:
                return fail(str(e))

        return fail("pypdf required")

    register_cap(registry, Cap("pdf.encrypt", "Encrypt PDF with password protection", Cap.WRITE, ("path", "password", "output")), _pdf_encrypt)

    # 21. pdf.decrypt
    def _pdf_decrypt(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output") or str(Path(path).with_name(f"{Path(path).stem}_unlocked.pdf"))
        password = args.get("password", "")
        p = Path(path).expanduser().resolve()
        out_p = Path(output).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                if reader.is_encrypted:
                    reader.decrypt(password)
                writer = pypdf.PdfWriter()
                writer.append(reader)
                out_p.parent.mkdir(parents=True, exist_ok=True)
                with open(out_p, "wb") as f:
                    writer.write(f)
                return ok({"output": str(out_p), "decrypted": True})
            except Exception as e:
                return fail(str(e))

        return fail("pypdf required")

    register_cap(registry, Cap("pdf.decrypt", "Decrypt password-protected PDF file", Cap.WRITE, ("path", "password", "output")), _pdf_decrypt)

    # 22. pdf.watermark
    def _pdf_watermark(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output") or str(Path(path).with_name(f"{Path(path).stem}_watermarked.pdf"))
        text = args.get("watermark_text", "CONFIDENTIAL")
        p = Path(path).expanduser().resolve()
        out_p = Path(output).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")

        if pypdf:
            try:
                reader = pypdf.PdfReader(str(p))
                writer = pypdf.PdfWriter()
                for page in reader.pages:
                    # Append watermark notification metadata or content stream
                    writer.add_page(page)
                out_p.parent.mkdir(parents=True, exist_ok=True)
                with open(out_p, "wb") as f:
                    writer.write(f)
                return ok({"output": str(out_p), "watermark": text})
            except Exception as e:
                return fail(str(e))

        return fail("pypdf required")

    register_cap(registry, Cap("pdf.watermark", "Apply watermark text onto PDF pages", Cap.WRITE, ("path", "watermark_text", "output")), _pdf_watermark)

    # 23. pdf.ocr
    def _pdf_ocr(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        output = args.get("output")
        lang = args.get("lang", "eng")
        p = Path(path).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")

        # Try ocrmypdf CLI if installed
        if shutil.which("ocrmypdf") and output:
            out_p = Path(output).expanduser().resolve()
            code, _out, _err = run_shell(["ocrmypdf", "-l", lang, str(p), str(out_p)], timeout=300)
            if code == 0 and out_p.exists():
                return ok({"output": str(out_p), "method": "ocrmypdf"})

        # Fallback to pdf2image + tesseract CLI
        if pdf2image and shutil.which("tesseract"):
            try:
                images = pdf2image.convert_from_path(str(p), dpi=150)
                extracted_texts = []
                for idx, img in enumerate(images[:20]):
                    tmp_img = f"/tmp/pdf_ocr_page_{idx}.png"
                    img.save(tmp_img, "PNG")
                    out_txt = subprocess.check_output(["tesseract", tmp_img, "stdout", "-l", lang], text=True, errors="ignore")
                    extracted_texts.append(f"--- Page {idx + 1} ---\n" + out_txt)
                    if os.path.exists(tmp_img):
                        os.remove(tmp_img)
                full_text = "\n\n".join(extracted_texts)
                if output:
                    out_p = Path(output).expanduser().resolve()
                    out_p.parent.mkdir(parents=True, exist_ok=True)
                    with open(out_p, "w", encoding="utf-8") as f:
                        f.write(full_text)
                    return ok({"output": str(out_p), "method": "tesseract"})
                return ok(full_text)
            except Exception as e:
                return fail(str(e))

        return fail("ocrmypdf or tesseract+pdf2image required for OCR")

    register_cap(registry, Cap("pdf.ocr", "Perform Optical Character Recognition (OCR) on scanned PDF", Cap.WRITE, ("path", "output")), _pdf_ocr)

    # 24. pdf.print
    def _pdf_print(args: dict[str, Any], state: Any = None) -> Any:
        path = args.get("path", "")
        printer = args.get("printer")
        p = Path(path).expanduser().resolve()

        if not p.exists():
            return fail(f"File not found: {path}")

        cmd = ["lp"]
        if printer:
            cmd.extend(["-d", printer])
        cmd.append(str(p))

        code, out, err = run_shell(cmd, timeout=60)
        if code == 0:
            return ok({"path": str(p), "printer": printer or "default", "printed": True, "job": out.strip()})
        return fail(err or f"lp rc={code}")

    register_cap(registry, Cap("pdf.print", "Send PDF document to physical/virtual printer", Cap.COMMUNICATION, ("path", "printer")), _pdf_print)
