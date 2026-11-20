"""Pillow schematic preview generator for Stage F (layout schematic, not a render)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pptv01.contracts import ElementKind, PageDesignEntry

from .capacity import wrap_mixed_text

PX_PER_IN = 100
WATERMARK_TEXT = "layout schematic, not a render"

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _load_font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, max(8, size_px))
            except OSError:
                continue
    return ImageFont.load_default()


def _hex_rgb(val: str | None, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if not val:
        return default
    h = val.lstrip("#")
    if len(h) == 6:
        try:
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            return default
    return default


def _draw_dashed_rect(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], color, width: int = 2) -> None:
    x1, y1, x2, y2 = xy
    dash = 10
    gap = 6
    for x in range(x1, x2, dash + gap):
        draw.line([(x, y1), (min(x + dash, x2), y1)], fill=color, width=width)
        draw.line([(x, y2), (min(x + dash, x2), y2)], fill=color, width=width)
    for y in range(y1, y2, dash + gap):
        draw.line([(x1, y), (x1, min(y + dash, y2))], fill=color, width=width)
        draw.line([(x2, y), (x2, min(y + dash, y2))], fill=color, width=width)


def render_schematic_preview(page: PageDesignEntry, run_dir: Path, out_rel: str) -> Path:
    """Draw a labelled schematic diagram of ``page`` (never mistaken for a real render)."""
    w = int(round(page.canvas_width_in * PX_PER_IN))
    h = int(round(page.canvas_height_in * PX_PER_IN))
    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    for el in sorted(page.elements, key=lambda e: e.z_index):
        x1 = int(round(el.x_in * PX_PER_IN))
        y1 = int(round(el.y_in * PX_PER_IN))
        x2 = int(round((el.x_in + el.w_in) * PX_PER_IN))
        y2 = int(round((el.y_in + el.h_in) * PX_PER_IN))

        if el.kind is ElementKind.shape:
            fill = _hex_rgb(el.fill_hex, (241, 245, 249))
            outline = _hex_rgb(el.line_hex, (203, 213, 225))
            draw.rectangle([x1, y1, x2, y2], fill=fill, outline=outline, width=1)
            continue

        if el.kind is ElementKind.placeholder:
            draw.rectangle([x1, y1, x2, y2], fill=(254, 243, 199))
            _draw_dashed_rect(draw, (x1, y1, x2, y2), (217, 119, 6), width=3)
            font = _load_font(int((el.font_size_pt or 14) * 1.25))
            lines = wrap_mixed_text(el.text or "", el.w_in, el.font_size_pt or 14.0)
            ty = y1 + 12
            for ln in lines[:6]:
                draw.text((x1 + 10, ty), ln, fill=(146, 64, 14), font=font)
                ty += int((el.font_size_pt or 14) * 1.5)
            continue

        if el.kind is ElementKind.image and el.asset_path:
            src = Path(run_dir) / el.asset_path
            draw.rectangle([x1, y1, x2, y2], fill=(226, 232, 240), outline=(59, 130, 246), width=2)
            if src.is_file() and src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                try:
                    with Image.open(src) as pic:
                        pic_rgb = pic.convert("RGB")
                        pic_rgb.thumbnail((max(1, x2 - x1 - 4), max(1, y2 - y1 - 4)))
                        px = x1 + (x2 - x1 - pic_rgb.width) // 2
                        py = y1 + (y2 - y1 - pic_rgb.height) // 2
                        img.paste(pic_rgb, (px, py))
                except OSError:
                    pass
            badge_font = _load_font(12)
            tag = el.asset_id or el.request_id or el.element_id
            draw.rectangle([x1 + 4, y1 + 4, x1 + 92, y1 + 24], fill=(30, 64, 175))
            draw.text((x1 + 8, y1 + 6), f"IMG {tag}", fill=(255, 255, 255), font=badge_font)
            continue

        if el.kind in (ElementKind.chart, ElementKind.table):
            draw.rectangle([x1, y1, x2, y2], fill=(239, 246, 255), outline=(37, 99, 235), width=2)
            font = _load_font(14)
            tag = el.request_id or el.asset_id or el.element_id
            draw.text((x1 + 12, y1 + 12), f"[{el.kind.value.upper()}] {tag} ({el.asset_path})", fill=(30, 64, 175), font=font)
            continue

        # Text boxes
        draw.rectangle([x1, y1, x2, y2], outline=(203, 213, 225), width=1)
        pt = el.font_size_pt or 16.0
        font = _load_font(int(pt * 1.2))
        color = _hex_rgb(el.color_hex, (30, 41, 59))
        lines = wrap_mixed_text(el.text or "", el.w_in, pt)
        ty = y1 + 6
        step = max(12, int(pt * 1.45))
        for ln in lines:
            if ty + step > y2:
                break
            draw.text((x1 + 8, ty), ln, fill=color, font=font)
            ty += step

    # Clear watermark banner so schematics are never mistaken for LibreOffice renders
    wm_font = _load_font(14)
    label = f"{page.page_id} · {page.layout_name or 'layout'} · {WATERMARK_TEXT}"
    tw = len(label) * 8 + 20
    draw.rectangle([w - tw - 12, 8, w - 8, 32], fill=(15, 23, 42))
    draw.text((w - tw - 4, 12), label, fill=(248, 250, 252), font=wm_font)

    out_path = Path(run_dir) / out_rel
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
