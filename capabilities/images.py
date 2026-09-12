from capabilities.base import Cap, fail, ok, register_cap, run_shell

try:
    from PIL import ExifTags, Image
except ImportError:
    Image = None

def install(registry, *, approve_all=False):
    @register_cap(registry, "image.open", Cap.LOW, "Open image in default viewer", approve_all)
    def open_image(path: str):
        return run_shell(["xdg-open", path])

    @register_cap(registry, "image.info", Cap.READ, "Get dimensions, format, mode", approve_all)
    def info(path: str):
        if Image:
            try:
                with Image.open(path) as img:
                    return ok(f"Format: {img.format}, Size: {img.size}, Mode: {img.mode}")
            except Exception as e:
                return fail(str(e))
        return run_shell(["identify", path])

    @register_cap(registry, "image.resize", Cap.WRITE, "Resize image", approve_all)
    def resize(path: str, output: str, width: int, height: int):
        if Image:
            try:
                with Image.open(path) as img:
                    resized = img.resize((width, height))
                    resized.save(output)
                    return ok(f"Resized to {output}")
            except Exception as e:
                return fail(str(e))
        return run_shell(["convert", path, "-resize", f"{width}x{height}", output])

    @register_cap(registry, "image.crop", Cap.WRITE, "Crop image", approve_all)
    def crop(path: str, output: str, x: int, y: int, width: int, height: int):
        if Image:
            try:
                with Image.open(path) as img:
                    cropped = img.crop((x, y, x + width, y + height))
                    cropped.save(output)
                    return ok(f"Cropped to {output}")
            except Exception as e:
                return fail(str(e))
        return run_shell(["convert", path, "-crop", f"{width}x{height}+{x}+{y}", output])

    @register_cap(registry, "image.rotate", Cap.WRITE, "Rotate image", approve_all)
    def rotate(path: str, output: str, degrees: int):
        if Image:
            try:
                with Image.open(path) as img:
                    rotated = img.rotate(degrees, expand=True)
                    rotated.save(output)
                    return ok(f"Rotated to {output}")
            except Exception as e:
                return fail(str(e))
        return run_shell(["convert", path, "-rotate", str(degrees), output])

    @register_cap(registry, "image.convert", Cap.WRITE, "Convert format", approve_all)
    def convert(path: str, output: str, format: str):
        if Image:
            try:
                # PIL needs canonical format names ("JPEG", not "jpg").
                pil_format = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG",
                              "webp": "WEBP", "bmp": "BMP", "gif": "GIF",
                              "tiff": "TIFF", "tif": "TIFF"}.get(format.lower(), format.upper())
                with Image.open(path) as img:
                    # RGBA can't be saved as JPEG — drop alpha first.
                    if pil_format == "JPEG" and img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    img.save(output, format=pil_format)
                    return ok(f"Converted to {output}")
            except Exception as e:
                return fail(str(e))
        return run_shell(["convert", path, output])

    @register_cap(registry, "image.compress", Cap.WRITE, "Compress/reduce quality", approve_all)
    def compress(path: str, output: str, quality: int):
        if Image:
            try:
                with Image.open(path) as img:
                    out_ext = output.lower().rsplit(".", 1)[-1] if "." in output else ""
                    pil_format = {"jpg": "JPEG", "jpeg": "JPEG"}.get(out_ext, "JPEG")
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    img.save(output, format=pil_format, quality=int(quality))
                    return ok(f"Compressed to {output}")
            except Exception as e:
                return fail(str(e))
        return run_shell(["convert", path, "-quality", str(quality), output])

    @register_cap(registry, "image.flip", Cap.WRITE, "Flip horizontally or vertically", approve_all)
    def flip(path: str, output: str, direction: str):
        if Image:
            try:
                with Image.open(path) as img:
                    if direction == "horizontal":
                        flipped = img.transpose(Image.FLIP_LEFT_RIGHT)
                    elif direction == "vertical":
                        flipped = img.transpose(Image.FLIP_TOP_BOTTOM)
                    else:
                        return fail("Invalid direction")
                    flipped.save(output)
                    return ok(f"Flipped to {output}")
            except Exception as e:
                return fail(str(e))
        if direction == "horizontal":
            return run_shell(["convert", path, "-flop", output])
        elif direction == "vertical":
            return run_shell(["convert", path, "-flip", output])
        return fail("Invalid direction")

    @register_cap(registry, "image.grayscale", Cap.WRITE, "Convert to grayscale", approve_all)
    def grayscale(path: str, output: str):
        if Image:
            try:
                with Image.open(path) as img:
                    gray = img.convert("L")
                    gray.save(output)
                    return ok(f"Grayscaled to {output}")
            except Exception as e:
                return fail(str(e))
        return run_shell(["convert", path, "-colorspace", "Gray", output])

    @register_cap(registry, "image.thumbnail", Cap.WRITE, "Create thumbnail", approve_all)
    def thumbnail(path: str, output: str, max_size: int):
        if Image:
            try:
                with Image.open(path) as img:
                    img.thumbnail((max_size, max_size))
                    img.save(output)
                    return ok(f"Thumbnail to {output}")
            except Exception as e:
                return fail(str(e))
        return run_shell(["convert", path, "-thumbnail", f"{max_size}x{max_size}", output])

    @register_cap(registry, "image.metadata", Cap.READ, "Read EXIF metadata", approve_all)
    def metadata(path: str):
        if Image:
            try:
                with Image.open(path) as img:
                    exif = img.getexif()
                    if exif:
                        data = {}
                        for tag_id, value in exif.items():
                            tag = ExifTags.TAGS.get(tag_id, tag_id)
                            data[tag] = value
                        return ok(str(data))
                    return ok("No EXIF data")
            except Exception as e:
                return fail(str(e))
        return run_shell(["identify", "-verbose", path])

    @register_cap(registry, "image.screenshot_region", Cap.READ, "Capture screen region", approve_all)
    def screenshot_region(x: int, y: int, width: int, height: int, output: str):
        return run_shell(["import", "-window", "root", "-crop", f"{width}x{height}+{x}+{y}", output])
