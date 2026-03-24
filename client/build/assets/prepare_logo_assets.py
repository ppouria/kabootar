#!/usr/bin/env python3
from __future__ import annotations

import base64
import re
from collections import deque
from io import BytesIO
from pathlib import Path

from PIL import Image

try:
    from cairosvg import svg2png
except Exception:
    svg2png = None

ROOT = Path(__file__).resolve().parents[3]
SOURCE_SVG = Path(__file__).resolve().with_name("kabootar.svg")
FRONTEND_STATIC = ROOT / "client" / "frontend" / "static"
WINDOWS_ICON = ROOT / "client" / "build" / "windows" / "kabootar.ico"
LINUX_ICON = ROOT / "client" / "build" / "linux" / "kabootar.png"
MACOS_ICON = ROOT / "client" / "build" / "macos" / "kabootar.icns"
ANDROID_RES = ROOT / "client" / "android" / "app" / "src" / "main" / "res"
ANDROID_SIZES = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}


def _read_svg() -> str:
    if not SOURCE_SVG.exists():
        raise FileNotFoundError(f"missing source logo: {SOURCE_SVG}")
    return SOURCE_SVG.read_text(encoding="utf-8")


def _clean_svg(svg_text: str) -> str:
    # Remove explicit white full-canvas background path.
    cleaned = re.sub(
        r'<path[^>]*d="M0 0H1500V1500H0Z"[^>]*fill="#(?:fff|ffffff)"[^>]*/>\s*',
        "",
        svg_text,
        flags=re.IGNORECASE,
    )
    cleaned, width_count = re.subn(r'\swidth="[^"]+"', ' width="256"', cleaned, count=1, flags=re.IGNORECASE)
    if width_count == 0:
        cleaned = cleaned.replace("<svg ", '<svg width="256" ', 1)
    cleaned, height_count = re.subn(r'\sheight="[^"]+"', ' height="256"', cleaned, count=1, flags=re.IGNORECASE)
    if height_count == 0:
        cleaned = cleaned.replace("<svg ", '<svg height="256" ', 1)
    if "preserveAspectRatio=" not in cleaned:
        cleaned = cleaned.replace("<svg ", '<svg preserveAspectRatio="xMidYMid meet" ', 1)
    return cleaned


def _save_web_svgs(svg_text: str) -> None:
    FRONTEND_STATIC.mkdir(parents=True, exist_ok=True)
    (FRONTEND_STATIC / "kabootar.svg").write_text(svg_text, encoding="utf-8")
    # Keep compatibility with existing fallback path used in old builds.
    (FRONTEND_STATIC / "t_logo.svg").write_text(svg_text, encoding="utf-8")


def _render_svg(svg_text: str, size: int = 1024) -> Image.Image:
    if svg2png is None:
        raise RuntimeError("CairoSVG runtime is unavailable")
    png_bytes = svg2png(
        bytestring=svg_text.encode("utf-8"),
        output_width=size,
        output_height=size,
        background_color=None,
    )
    return Image.open(BytesIO(png_bytes)).convert("RGBA")


def _extract_embedded_png(svg_text: str) -> Image.Image:
    match = re.search(r'data:image/png;base64,\s*([^"]+)"', svg_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        raise RuntimeError("embedded PNG data not found in kabootar.svg")
    b64_raw = re.sub(r"\s+", "", match.group(1))
    png_bytes = base64.b64decode(b64_raw)
    return Image.open(BytesIO(png_bytes)).convert("RGBA")


def _near_white(px: tuple[int, int, int, int], threshold: int = 245) -> bool:
    r, g, b, a = px
    return a > 0 and r >= threshold and g >= threshold and b >= threshold


def _remove_edge_white_background(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    pix = rgba.load()
    w, h = rgba.size

    q: deque[tuple[int, int]] = deque()
    visited = set()
    for x in range(w):
        q.append((x, 0))
        q.append((x, h - 1))
    for y in range(h):
        q.append((0, y))
        q.append((w - 1, y))

    while q:
        x, y = q.popleft()
        if x < 0 or y < 0 or x >= w or y >= h:
            continue
        key = (x, y)
        if key in visited:
            continue
        visited.add(key)

        if not _near_white(pix[x, y]):
            continue

        r, g, b, _ = pix[x, y]
        pix[x, y] = (r, g, b, 0)
        q.append((x + 1, y))
        q.append((x - 1, y))
        q.append((x, y + 1))
        q.append((x, y - 1))

    bbox = rgba.getbbox()
    if bbox:
        rgba = rgba.crop(bbox)
    return rgba


def _fit_square(img: Image.Image, size: int = 1024, inner_ratio: float = 0.88) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inner = max(1, int(size * inner_ratio))
    ratio = min(inner / img.width, inner / img.height)
    target = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
    resized = img.resize(target, Image.Resampling.LANCZOS)
    ox = (size - target[0]) // 2
    oy = (size - target[1]) // 2
    canvas.paste(resized, (ox, oy), resized)
    return canvas


def _save_windows_icon(master: Image.Image) -> None:
    WINDOWS_ICON.parent.mkdir(parents=True, exist_ok=True)
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(WINDOWS_ICON, format="ICO", sizes=ico_sizes)


def _save_linux_icon(master: Image.Image) -> None:
    LINUX_ICON.parent.mkdir(parents=True, exist_ok=True)
    master.resize((512, 512), Image.Resampling.LANCZOS).save(LINUX_ICON, format="PNG")


def _save_macos_icon(master: Image.Image) -> None:
    MACOS_ICON.parent.mkdir(parents=True, exist_ok=True)
    icns_sizes = [(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)]
    master.save(MACOS_ICON, format="ICNS", sizes=icns_sizes)


def _save_android_icons(master: Image.Image) -> None:
    for folder, size in ANDROID_SIZES.items():
        out_dir = ANDROID_RES / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        icon = master.resize((size, size), Image.Resampling.LANCZOS)
        icon.save(out_dir / "ic_launcher.png", format="PNG")
        icon.save(out_dir / "ic_launcher_round.png", format="PNG")


def main() -> None:
    raw_svg = _read_svg()
    cleaned_svg = _clean_svg(raw_svg)
    _save_web_svgs(cleaned_svg)

    raster_source = "svg-render"
    try:
        raster = _render_svg(cleaned_svg, size=1024)
    except Exception as exc:
        raster = _extract_embedded_png(cleaned_svg)
        raster_source = f"embedded-png-fallback:{exc}"
    raster = _remove_edge_white_background(raster)
    master = _fit_square(raster, size=1024, inner_ratio=0.88)

    _save_windows_icon(master)
    _save_linux_icon(master)
    _save_macos_icon(master)
    _save_android_icons(master)

    print(f"[brand] source: {SOURCE_SVG}")
    print(f"[brand] web: {FRONTEND_STATIC / 'kabootar.svg'}")
    print(f"[brand] windows icon: {WINDOWS_ICON}")
    print(f"[brand] linux icon: {LINUX_ICON}")
    print(f"[brand] macos icon: {MACOS_ICON}")
    print(f"[brand] raster source: {raster_source}")
    print("[brand] android icons: mipmap-*/ic_launcher(.png|_round.png)")


if __name__ == "__main__":
    main()
