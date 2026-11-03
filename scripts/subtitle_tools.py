#!/usr/bin/env python3
"""Pure-stdlib subtitle helpers for the YouTube phase (no third-party code).

Pipeline: faster-whisper -> SRT -> batches -> Groq translation -> SRT/ASS -> ffmpeg burn-in.
This module only does text/command work; it never touches the network.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path

Cue = tuple  # (start_ms, end_ms, text)
_TS = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./\-]+$")
RLM = "\u200f"
RLE = "\u202b"
PDF = "\u202c"
# 60 px at a 720 px short edge. Every output scales from the actual video dimensions.
DEFAULT_ASS_SIZE = 60
MIN_ASS_SIZE = 16
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
BASE_LAYOUT_EDGE = 720
MAX_TEXT_WIDTH_RATIO = 0.80
LANDSCAPE_MAX_LINES = 3
PORTRAIT_MAX_LINES = 2
GLYPH_BOX_PADDING = 12
GLYPH_BOX_RADIUS = 8
GLYPH_BOX_ALPHA = "80"  # 50% transparent black
DEFAULT_ASS_MARGIN_V = 40
LAYOUT_SPLIT_MIN_MS = 250


def _ms(m: re.Match) -> int:
    h, mi, s, f = m.groups()
    return ((int(h) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(f.ljust(3, "0"))


def parse_srt(text: str) -> list:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = block.split("\n")
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        a, b = lines[idx].split("-->", 1)
        ma, mb = _TS.search(a), _TS.search(b)
        body = "\n".join(lines[idx + 1:]).strip()
        if ma and mb and body:
            cues.append((_ms(ma), _ms(mb), body))
    return cues


def fmt_ts(ms: int, sep: str = ",") -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, f = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{f:03d}"


def format_srt(cues: list) -> str:
    out = [f"{i}\n{fmt_ts(s)} --> {fmt_ts(e)}\n{t}" for i, (s, e, t) in enumerate(cues, 1)]
    return "\n\n".join(out) + "\n" if out else ""


def make_batches(cues: list, max_chars: int = 3000, max_cues: int = 60) -> list:
    """Split cues so numbered requests, including cue IDs and newlines, stay bounded."""
    if max_chars < 1 or max_cues < 1:
        raise ValueError("invalid_subtitle_batch_limits")
    batches, cur, size, start = [], [], 0, 0
    for i, (_, _, t) in enumerate(cues):
        flat = " ".join(str(t).split())
        # A serialized line is "N|text" plus a newline separator. Count one extra
        # character per cue so the final numbered request cannot exceed max_chars.
        cost = len(flat) + len(str(i)) + 2
        if cost > max_chars:
            raise ValueError("subtitle_cue_too_long")
        if cur and (size + cost > max_chars or len(cur) >= max_cues):
            batches.append((start, cur))
            cur, size, start = [], 0, i
        cur.append(flat)
        size += cost
    if cur:
        batches.append((start, cur))
    return batches


def numbered(texts: list, start: int = 0) -> str:
    return "\n".join(f"{start + i}|{t}" for i, t in enumerate(texts))


def parse_numbered(reply: str, start: int, count: int) -> list:
    """Parse 'N|text' lines; missing/invalid entries become None."""
    found = {}
    for ln in reply.splitlines():
        m = re.match(r"\s*(\d+)\s*\|\s*(.+?)\s*$", ln)
        if m:
            found[int(m.group(1))] = m.group(2)
    return [found.get(start + i) for i in range(count)]


def apply_translation(cues: list, translations: list) -> tuple:
    """Return (new_cues, missing_count). Missing translations keep the original text."""
    if len(cues) != len(translations):
        raise ValueError("cue/translation count mismatch")
    out, missing = [], 0
    for (s, e, t), tr in zip(cues, translations):
        if tr:
            out.append((s, e, tr))
        else:
            out.append((s, e, t))
            missing += 1
    return out, missing


def _ass_ts(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, f = divmod(rem, 1000)
    return f"{h}:{m:02d}:{s:02d}.{f // 10:02d}"


def _has_arabic_letter(text: str) -> bool:
    """Return whether a display line contains an Arabic-script letter."""
    for char in text:
        if unicodedata.category(char).startswith("L"):
            if unicodedata.name(char, "").startswith("ARABIC LETTER"):
                return True
    return False


def _ass_display_text(text: str, rtl: bool = True) -> str:
    clean = str(text).replace("{", "(").replace("}", ")").replace("\n", r"\N")
    if not rtl:
        return clean
    display_lines = []
    for display_line in clean.split(r"\N"):
        if _has_arabic_letter(display_line):
            display_lines.append(RLE + display_line + PDF)
        else:
            display_lines.append(RLM + display_line)
    return r"\N".join(display_lines)



def _frame_metrics(width: int, height: int) -> dict:
    """Return dimension-relative subtitle geometry for the actual video canvas."""
    if (not isinstance(width, int) or not isinstance(height, int)
            or width < 1 or height < 1):
        raise ValueError("invalid_subtitle_frame_size")
    short_edge = min(width, height)
    portrait = height > width
    padding = max(1, int(round(GLYPH_BOX_PADDING * short_edge / BASE_LAYOUT_EDGE)))
    radius = max(1, int(round(GLYPH_BOX_RADIUS * short_edge / BASE_LAYOUT_EDGE)))
    # Fixed lower safe band per orientation; no automatic burned-in subtitle detection.
    bottom_gap = max(1, int(round(short_edge * (24 if portrait else 33) / BASE_LAYOUT_EDGE)))
    margin_v = max(0, int(round(short_edge * (30 if portrait else DEFAULT_ASS_MARGIN_V) / BASE_LAYOUT_EDGE)))
    box_bottom = height - bottom_gap
    return {
        "short_edge": short_edge,
        "portrait": portrait,
        "max_lines": PORTRAIT_MAX_LINES if portrait else LANDSCAPE_MAX_LINES,
        "padding": padding,
        "radius": radius,
        "bottom_gap": bottom_gap,
        "box_bottom": box_bottom,
        "glyph_bottom": box_bottom - padding,
        "margin_v": margin_v,
        "margin_l": max(1, int(round(width * (1 - MAX_TEXT_WIDTH_RATIO) / 2))),
        "margin_r": max(1, int(round(width * (1 - MAX_TEXT_WIDTH_RATIO) / 2))),
        "max_text_width": max(1, int(round(width * MAX_TEXT_WIDTH_RATIO))),
    }


def _scaled_font_size(width: int, height: int, base_size: int = DEFAULT_ASS_SIZE) -> int:
    metrics = _frame_metrics(width, height)
    return max(MIN_ASS_SIZE, int(round(int(base_size) * metrics["short_edge"] / BASE_LAYOUT_EDGE)))


def _layout_font(fontsdir: str, font: str, size: int):
    from PIL import ImageFont

    directory = Path(fontsdir)
    candidates = [directory / f"{font}-Bold.ttf", directory / f"{font}-Regular.ttf"]
    font_path = next((path for path in candidates if path.is_file()), None)
    if font_path is None:
        raise RuntimeError("subtitle_layout_font_missing")
    try:
        return ImageFont.truetype(str(font_path), int(size), layout_engine=ImageFont.Layout.RAQM)
    except (AttributeError, TypeError, ValueError):
        # Some Pillow builds lack libraqm; libass still does the authoritative render.
        return ImageFont.truetype(str(font_path), int(size))


def _measure_line(font_obj, text: str) -> float:
    direction = "rtl" if _has_arabic_letter(text) else "ltr"
    try:
        return float(font_obj.getlength(text, direction=direction, language="fa" if direction == "rtl" else "en"))
    except (TypeError, KeyError, ValueError):
        return float(font_obj.getlength(text))


def _wrap_words(text: str, font_obj, max_width: int) -> list:
    """Greedily wrap each source line on spaces without dropping any word."""
    words = " ".join(str(text).split()).split(" ")
    lines = []
    current = ""
    for word in words:
        if not word:
            continue
        candidate = word if not current else current + " " + word
        if current and _measure_line(font_obj, candidate) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def layout_cues(cues: list, *, width: int, height: int, fontsdir: str = "assets/fonts",
                font: str = "Estedad", size: int = DEFAULT_ASS_SIZE) -> tuple:
    """Wrap cues against 80% of the real frame width; portrait blocks are limited to two lines.

    Existing explicit line breaks are honored. Long blocks shrink first, then are split at
    word boundaries with proportional timing; no words are removed.
    """
    metrics = _frame_metrics(width, height)
    base_size = _scaled_font_size(width, height, size)
    minimum_size = max(MIN_ASS_SIZE, int(round(base_size * 0.40)))
    max_width = metrics["max_text_width"]
    max_lines = metrics["max_lines"]
    output, font_sizes = [], []

    for start_ms, end_ms, raw_text in cues:
        paragraphs = [
            " ".join(part.split())
            for part in str(raw_text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ]
        paragraphs = [part for part in paragraphs if part]
        if not paragraphs:
            continue

        selected = None
        candidate_size = base_size
        decrement = max(1, int(round(base_size * 0.025)))
        while candidate_size >= minimum_size:
            font_obj = _layout_font(fontsdir, font, candidate_size)
            lines = [line for paragraph in paragraphs for line in _wrap_words(paragraph, font_obj, max_width)]
            fits = len(lines) <= max_lines and all(_measure_line(font_obj, line) <= max_width for line in lines)
            if fits:
                selected = (lines, candidate_size)
                break
            candidate_size -= decrement
        if selected:
            lines, chosen_size = selected
        else:
            chosen_size = minimum_size
            font_obj = _layout_font(fontsdir, font, chosen_size)
            lines = [line for paragraph in paragraphs for line in _wrap_words(paragraph, font_obj, max_width)]
            if any(_measure_line(font_obj, line) > max_width for line in lines):
                raise ValueError("subtitle_word_exceeds_safe_width")

        chunks = [lines[i:i + max_lines] for i in range(0, len(lines), max_lines)]
        duration = max(0, int(end_ms) - int(start_ms))
        if len(chunks) > 1 and duration < len(chunks) * LAYOUT_SPLIT_MIN_MS:
            raise ValueError("subtitle_cue_too_short_for_layout_split")
        weights = [max(1, len(" ".join(chunk))) for chunk in chunks]
        total_weight = sum(weights)
        cursor = int(start_ms)
        remaining = duration
        remaining_weight = total_weight
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            if index == len(chunks) - 1:
                next_cursor = int(end_ms)
            else:
                ideal = int(round(remaining * weight / max(1, remaining_weight)))
                min_after = (len(chunks) - index - 1) * LAYOUT_SPLIT_MIN_MS
                span = max(LAYOUT_SPLIT_MIN_MS, min(ideal, remaining - min_after))
                next_cursor = cursor + span
            output.append((cursor, next_cursor, "\n".join(chunk)))
            font_sizes.append(chosen_size)
            used = next_cursor - cursor
            cursor = next_cursor
            remaining -= used
            remaining_weight -= weight
    return output, font_sizes

def _ass_header(font: str, size: int, width: int = FRAME_WIDTH, height: int = FRAME_HEIGHT,
                margin_l: int | None = None, margin_r: int | None = None) -> str:
    metrics = _frame_metrics(width, height)
    left = metrics["margin_l"] if margin_l is None else int(margin_l)
    right = metrics["margin_r"] if margin_r is None else int(margin_r)
    default_margin_v = metrics["margin_v"]
    return (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H66000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,0,0,2,{left},{right},{default_margin_v},1\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _rounded_rectangle_path(box: tuple, radius: int = GLYPH_BOX_RADIUS) -> str:
    left, top, right, bottom = (int(value) for value in box)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise ValueError("invalid_subtitle_glyph_box")
    r = max(1, min(int(radius), width // 2, height // 2))
    k = int(round(r * 0.55228475))
    return (
        f"m {left + r} {top} "
        f"l {right - r} {top} "
        f"b {right - r + k} {top} {right} {top + r - k} {right} {top + r} "
        f"l {right} {bottom - r} "
        f"b {right} {bottom - r + k} {right - r + k} {bottom} {right - r} {bottom} "
        f"l {left + r} {bottom} "
        f"b {left + r - k} {bottom} {left} {bottom - r + k} {left} {bottom - r} "
        f"l {left} {top + r} "
        f"b {left} {top + r - k} {left + r - k} {top} {left + r} {top} c"
    )


def to_ass(cues: list, font: str = "Estedad", size: int = DEFAULT_ASS_SIZE, rtl: bool = True,
           *, margin_vs: list | None = None, glyph_boxes: list | None = None,
           width: int = FRAME_WIDTH, height: int = FRAME_HEIGHT,
           margin_ls: list | None = None, margin_rs: list | None = None,
           font_sizes: list | None = None) -> str:
    """Build dimension-aware ASS, optionally with per-cue glyph-size and drawing layers."""
    size = max(int(size), MIN_ASS_SIZE)
    metrics = _frame_metrics(width, height)
    for values, label in ((margin_vs, "subtitle_margin_count_mismatch"),
                          (glyph_boxes, "subtitle_glyph_box_count_mismatch"),
                          (margin_ls, "subtitle_margin_l_count_mismatch"),
                          (margin_rs, "subtitle_margin_r_count_mismatch"),
                          (font_sizes, "subtitle_font_size_count_mismatch")):
        if values is not None and len(values) != len(cues):
            raise ValueError(label)

    lines = []
    for index, (start_ms, end_ms, text) in enumerate(cues):
        start, end = _ass_ts(start_ms), _ass_ts(end_ms)
        if glyph_boxes is not None:
            path = _rounded_rectangle_path(glyph_boxes[index], metrics["radius"])
            drawing = (
                r"{\an7\pos(0,0)\p1\bord0\shad0\1c&H000000&\1a&H"
                + GLYPH_BOX_ALPHA + "&}"
                + path + r"{\p0}"
            )
            lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{drawing}")

        margin_v = metrics["margin_v"] if margin_vs is None else int(margin_vs[index])
        margin_l = 0 if margin_ls is None else int(margin_ls[index])
        margin_r = 0 if margin_rs is None else int(margin_rs[index])
        clean = _ass_display_text(text, rtl=rtl)
        font_size = size if font_sizes is None else max(MIN_ASS_SIZE, int(font_sizes[index]))
        if font_size != size:
            clean = "{\\fs" + str(font_size) + "}" + clean
        lines.append(f"Dialogue: 1,{start},{end},Default,,{margin_l},{margin_r},{margin_v},,{clean}")
    return _ass_header(font, size, width, height) + "\n".join(lines) + "\n"


def _render_glyph_bounds(cues: list, fontsdir: str, margin_vs: list,
                         font: str = "Estedad", size: int = DEFAULT_ASS_SIZE,
                         ffmpeg: str = "ffmpeg", *, width: int = FRAME_WIDTH,
                         height: int = FRAME_HEIGHT, margin_ls: list | None = None,
                         margin_rs: list | None = None, font_sizes: list | None = None) -> list:
    """Render exact Estedad glyph pixels through libass on the real video canvas."""
    from PIL import Image

    if not _SAFE_PATH.fullmatch(str(fontsdir)) or not _SAFE_PATH.fullmatch(str(ffmpeg)):
        raise ValueError("unsafe_subtitle_render_path")
    if not cues:
        return []
    probe_cues = [
        (index * 1000, (index + 1) * 1000, cue[2])
        for index, cue in enumerate(cues)
    ]
    with tempfile.TemporaryDirectory(prefix="subtitle-glyph-bbox-") as temp:
        workdir = Path(temp)
        ass_path = workdir / "glyph-mask.ass"
        frame_pattern = workdir / "glyph-%04d.png"
        ass_path.write_text(
            to_ass(
                probe_cues, font=font, size=size, margin_vs=margin_vs,
                width=width, height=height, margin_ls=margin_ls,
                margin_rs=margin_rs, font_sizes=font_sizes,
            ),
            encoding="utf-8",
        )
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            f"color=c=black:s={width}x{height}:r=1:d={len(cues)}",
            "-vf", f"ass={ass_path}:fontsdir={fontsdir}",
            "-frames:v", str(len(cues)), "-vsync", "0", str(frame_pattern),
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError("subtitle_glyph_render_failed")
        bounds = []
        for index in range(1, len(cues) + 1):
            frame = workdir / f"glyph-{index:04d}.png"
            if not frame.is_file():
                raise RuntimeError("subtitle_glyph_render_missing_frame")
            image = Image.open(frame).convert("RGB")
            bbox = image.getbbox()
            if bbox is None:
                raise RuntimeError("subtitle_glyph_bbox_empty")
            bounds.append(tuple(int(value) for value in bbox))
        return bounds


def to_ass_with_glyph_boxes(cues: list, fontsdir: str = "assets/fonts",
                            font: str = "Estedad", size: int = DEFAULT_ASS_SIZE,
                            rtl: bool = True, ffmpeg: str = "ffmpeg", *,
                            width: int = FRAME_WIDTH, height: int = FRAME_HEIGHT,
                            font_sizes: list | None = None) -> tuple:
    """Draw one dimension-relative rounded rectangle around each complete cue block."""
    metrics = _frame_metrics(width, height)
    scaled_size = _scaled_font_size(width, height, size)
    if not cues:
        return to_ass(cues, font=font, size=scaled_size, rtl=rtl, width=width, height=height), []

    if font_sizes is None:
        font_sizes = [scaled_size] * len(cues)
    if len(font_sizes) != len(cues):
        raise ValueError("subtitle_font_size_count_mismatch")
    font_sizes = [max(MIN_ASS_SIZE, int(value)) for value in font_sizes]
    margin_vs = [metrics["margin_v"]] * len(cues)
    margin_ls = [metrics["margin_l"]] * len(cues)
    margin_rs = [metrics["margin_r"]] * len(cues)
    bounds = []

    # Verify measured libass width, shrinking only the offending cue if Pillow's
    # advance-width estimate differs from the final shaped glyph pixels.
    for _ in range(5):
        bounds = _render_glyph_bounds(
            cues, fontsdir, margin_vs, font=font, size=scaled_size, ffmpeg=ffmpeg,
            width=width, height=height, margin_ls=margin_ls, margin_rs=margin_rs,
            font_sizes=font_sizes,
        )
        too_wide = [
            index for index, bound in enumerate(bounds)
            if bound[2] - bound[0] > metrics["max_text_width"]
        ]
        if not too_wide:
            break
        changed = False
        for index in too_wide:
            actual = bounds[index][2] - bounds[index][0]
            next_size = max(MIN_ASS_SIZE, int(font_sizes[index] * metrics["max_text_width"] / actual * 0.96))
            if next_size < font_sizes[index]:
                font_sizes[index] = next_size
                changed = True
        if not changed:
            raise RuntimeError("subtitle_glyph_text_exceeds_safe_width")
    else:
        raise RuntimeError("subtitle_glyph_text_exceeds_safe_width")

    for _ in range(5):
        corrections = [bound[3] - metrics["glyph_bottom"] for bound in bounds]
        if all(abs(value) <= 1 for value in corrections):
            break
        updated = [
            max(0, min(height, margin_vs[index] + correction))
            for index, correction in enumerate(corrections)
        ]
        if updated == margin_vs:
            raise RuntimeError("subtitle_glyph_baseline_could_not_be_aligned")
        margin_vs = updated
        bounds = _render_glyph_bounds(
            cues, fontsdir, margin_vs, font=font, size=scaled_size, ffmpeg=ffmpeg,
            width=width, height=height, margin_ls=margin_ls, margin_rs=margin_rs,
            font_sizes=font_sizes,
        )

    if any(abs(bound[3] - metrics["glyph_bottom"]) > 1 for bound in bounds):
        raise RuntimeError("subtitle_glyph_baseline_out_of_tolerance")

    rectangles = []
    for left, top, right, _bottom in bounds:
        box = (
            left - metrics["padding"],
            top - metrics["padding"],
            right + metrics["padding"],
            metrics["box_bottom"],
        )
        if (box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height
                or box[3] - metrics["glyph_bottom"] < metrics["padding"] - 1):
            raise RuntimeError("subtitle_glyph_box_out_of_frame")
        rectangles.append(box)

    return (
        to_ass(
            cues, font=font, size=scaled_size, rtl=rtl, margin_vs=margin_vs,
            glyph_boxes=rectangles, width=width, height=height,
            margin_ls=margin_ls, margin_rs=margin_rs, font_sizes=font_sizes,
        ),
        bounds,
    )


# Legacy blur-band settings, off by default; use burn_cmd(..., blur=True) to opt in.
BLUR_BAND_FRACTION = 0.28   # share of frame height, anchored to the bottom edge
BLUR_SIGMA = 18
BLUR_TINT = "black@0.45"


def burn_cmd(video: str, ass: str, fontsdir: str, out: str, blur: bool = False) -> list:
    """ffmpeg argv that burns an ASS file using the in-repo font directory.

    With blur=True a blurred, tinted band covers the bottom of the frame and the
    subtitles are drawn on top of it (only that band is blurred, the rest is untouched).
    """
    for p in (video, ass, fontsdir, out):
        if not _SAFE_PATH.match(p):
            raise ValueError(f"unsafe path for ffmpeg filter: {p!r}")
    ass_filter = f"ass={ass}:fontsdir={fontsdir}"
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
           "-c:a", "copy", out]
    if not blur:
        return ["ffmpeg", "-y", "-i", video, "-vf", ass_filter] + enc
    graph = (
        f"[0:v]split[base][strip];"
        f"[strip]crop=iw:trunc(ih*{BLUR_BAND_FRACTION}/2)*2:0:ih-trunc(ih*{BLUR_BAND_FRACTION}/2)*2,"
        f"gblur=sigma={BLUR_SIGMA},drawbox=x=0:y=0:w=iw:h=ih:color={BLUR_TINT}:t=fill[band];"
        f"[base][band]overlay=x=0:y=H-h,{ass_filter}[out]"
    )
    return ["ffmpeg", "-y", "-i", video, "-filter_complex", graph,
            "-map", "[out]", "-map", "0:a?"] + enc
