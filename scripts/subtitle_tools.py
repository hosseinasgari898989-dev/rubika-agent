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
# Burned-in subtitle size (PlayResY 720). Medium-large; callers asking for less get this floor.
DEFAULT_ASS_SIZE = 60
MIN_ASS_SIZE = 60
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
GLYPH_BOX_PADDING = 12
GLYPH_BOX_RADIUS = 8
GLYPH_BOX_ALPHA = "80"  # 50% transparent black
DEFAULT_ASS_MARGIN_V = 40
# Existing BorderStyle=3 rendering placed the opaque box's last pixel at row 686.
# Keep that exact lower edge while allocating 12 pixels below the rendered glyphs.
GLYPH_BOX_BOTTOM_EXCLUSIVE = 687
GLYPH_BOTTOM_TARGET_EXCLUSIVE = GLYPH_BOX_BOTTOM_EXCLUSIVE - GLYPH_BOX_PADDING


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


def _ass_header(font: str, size: int) -> str:
    return (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H66000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,0,0,2,40,40,40,1\n\n[Events]\n"
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
           *, margin_vs: list | None = None, glyph_boxes: list | None = None) -> str:
    """Build ASS with text-only glyphs, optionally adding separate layer-0 drawing boxes."""
    size = max(int(size), MIN_ASS_SIZE)
    if margin_vs is not None and len(margin_vs) != len(cues):
        raise ValueError("subtitle_margin_count_mismatch")
    if glyph_boxes is not None and len(glyph_boxes) != len(cues):
        raise ValueError("subtitle_glyph_box_count_mismatch")

    lines = []
    for index, (start_ms, end_ms, text) in enumerate(cues):
        start, end = _ass_ts(start_ms), _ass_ts(end_ms)
        if glyph_boxes is not None:
            path = _rounded_rectangle_path(glyph_boxes[index])
            drawing = (
                r"{\an7\pos(0,0)\p1\bord0\shad0\1c&H000000&\1a&H"
                + GLYPH_BOX_ALPHA + "&}"
                + path + r"{\p0}"
            )
            lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{drawing}")

        margin_v = DEFAULT_ASS_MARGIN_V if margin_vs is None else int(margin_vs[index])
        clean = _ass_display_text(text, rtl=rtl)
        lines.append(f"Dialogue: 1,{start},{end},Default,,0,0,{margin_v},,{clean}")
    return _ass_header(font, size) + "\n".join(lines) + "\n"


def _render_glyph_bounds(cues: list, fontsdir: str, margin_vs: list,
                         font: str = "Estedad", size: int = DEFAULT_ASS_SIZE,
                         ffmpeg: str = "ffmpeg") -> list:
    """Render each cue with real libass/Estedad pixels and return its visible glyph bbox."""
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
            to_ass(probe_cues, font=font, size=size, margin_vs=margin_vs),
            encoding="utf-8",
        )
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            f"color=c=black:s={FRAME_WIDTH}x{FRAME_HEIGHT}:r=1:d={len(cues)}",
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
                            rtl: bool = True, ffmpeg: str = "ffmpeg") -> tuple:
    """Draw one rounded translucent rectangle per cue, from its rendered glyph bbox."""
    if not cues:
        return to_ass(cues, font=font, size=size, rtl=rtl), []

    margins = [DEFAULT_ASS_MARGIN_V] * len(cues)
    bounds = _render_glyph_bounds(
        cues, fontsdir, margins, font=font, size=size, ffmpeg=ffmpeg,
    )
    for _ in range(4):
        corrections = [
            bound[3] - GLYPH_BOTTOM_TARGET_EXCLUSIVE for bound in bounds
        ]
        if all(abs(value) <= 1 for value in corrections):
            break
        updated = [
            max(0, min(200, margins[index] + correction))
            for index, correction in enumerate(corrections)
        ]
        if updated == margins:
            raise RuntimeError("subtitle_glyph_baseline_could_not_be_aligned")
        margins = updated
        bounds = _render_glyph_bounds(
        cues, fontsdir, margins, font=font, size=size, ffmpeg=ffmpeg,
    )

    if any(abs(bound[3] - GLYPH_BOTTOM_TARGET_EXCLUSIVE) > 1 for bound in bounds):
        raise RuntimeError("subtitle_glyph_baseline_out_of_tolerance")

    rectangles = []
    for left, top, right, _bottom in bounds:
        box = (
            left - GLYPH_BOX_PADDING,
            top - GLYPH_BOX_PADDING,
            right + GLYPH_BOX_PADDING,
            GLYPH_BOX_BOTTOM_EXCLUSIVE,
        )
        if box[0] < 0 or box[1] < 0 or box[2] > FRAME_WIDTH or box[3] > FRAME_HEIGHT:
            raise RuntimeError("subtitle_glyph_box_out_of_frame")
        rectangles.append(box)

    return (
        to_ass(cues, font=font, size=size, rtl=rtl, margin_vs=margins, glyph_boxes=rectangles),
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
