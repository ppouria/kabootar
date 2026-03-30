#!/usr/bin/env python3
from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
from collections import deque
from io import BytesIO
from pathlib import Path

from PIL import Image

try:
    from cairosvg import svg2png
except Exception:
    svg2png = None

ROOT = Path(__file__).resolve().parents[3]
FRONTEND_STATIC = ROOT / "client" / "frontend" / "static"
SOURCE_SVG = FRONTEND_STATIC / "kabootar.svg"
LEGACY_BUILD_SVG = Path(__file__).resolve().with_name("kabootar.svg")
WINDOWS_ICON = ROOT / "client" / "build" / "windows" / "kabootar.ico"
MACOS_ICON = ROOT / "client" / "build" / "macos" / "kabootar.icns"
ANDROID_RES = ROOT / "client" / "android" / "app" / "src" / "main" / "res"
ANDROID_SIZES = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}
COMMON_BROWSER_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]


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


def _save_compatibility_svgs(svg_text: str) -> None:
    FRONTEND_STATIC.mkdir(parents=True, exist_ok=True)
    # Keep compatibility with existing fallback path used in old builds.
    (FRONTEND_STATIC / "t_logo.svg").write_text(svg_text, encoding="utf-8")
    LEGACY_BUILD_SVG.write_text(svg_text, encoding="utf-8")


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


def _find_headless_browser() -> Path | None:
    override = (os.getenv("KABOOTAR_SVG_BROWSER") or "").strip()
    if override:
        candidate = Path(override)
        if candidate.exists():
            return candidate
    for candidate in COMMON_BROWSER_PATHS:
        if candidate.exists():
            return candidate
    return None


def _render_svg_via_browser(svg_text: str, size: int = 1024) -> Image.Image:
    browser = _find_headless_browser()
    if browser is None:
        raise RuntimeError("headless browser renderer is unavailable")
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    html, body {{
      margin: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: transparent;
    }}
    body {{
      display: grid;
      place-items: center;
    }}
    .stage {{
      width: {size}px;
      height: {size}px;
      display: grid;
      place-items: center;
      background: transparent;
    }}
    .stage > svg {{
      display: block;
      width: 100%;
      height: 100%;
    }}
  </style>
</head>
<body>
  <div class="stage">{svg_text}</div>
</body>
</html>
"""
    with tempfile.TemporaryDirectory(prefix="kabootar-svg-render-") as temp_dir:
        temp_path = Path(temp_dir)
        html_path = temp_path / "render.html"
        png_path = temp_path / "render.png"
        html_path.write_text(html, encoding="utf-8")
        cmd = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--default-background-color=00000000",
            f"--window-size={size},{size}",
            f"--screenshot={png_path}",
            html_path.resolve().as_uri(),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if completed.returncode != 0 or not png_path.exists():
            stderr = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(f"headless browser render failed: {stderr or completed.returncode}")
        return Image.open(png_path).convert("RGBA")


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
    _save_compatibility_svgs(raw_svg)

    raster_source = "svg-render"
    try:
        raster = _render_svg(cleaned_svg, size=1024)
    except Exception as exc:
        try:
            raster = _render_svg_via_browser(cleaned_svg, size=1024)
            raster_source = f"browser-screenshot-fallback:{type(exc).__name__}"
        except Exception as browser_exc:
            raster = _extract_embedded_png(cleaned_svg)
            raster_source = f"embedded-png-fallback:{exc}; {browser_exc}"
    raster = _remove_edge_white_background(raster)
    master = _fit_square(raster, size=1024, inner_ratio=0.88)

    _save_windows_icon(master)
    _save_macos_icon(master)
    _save_android_icons(master)

    print(f"[brand] source: {SOURCE_SVG}")
    print(f"[brand] web: {SOURCE_SVG}")
    print(f"[brand] compatibility mirror: {LEGACY_BUILD_SVG}")
    print(f"[brand] windows icon: {WINDOWS_ICON}")
    print(f"[brand] linux icon: {SOURCE_SVG}")
    print(f"[brand] macos icon: {MACOS_ICON}")
    print(f"[brand] raster source: {raster_source}")
    print("[brand] android icons: mipmap-*/ic_launcher(.png|_round.png)")


if __name__ == "__main__":
    main()
