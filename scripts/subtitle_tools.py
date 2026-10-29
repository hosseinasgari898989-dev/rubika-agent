#!/usr/bin/env python3
"""Pure-stdlib subtitle helpers for the YouTube phase (no third-party code).

Pipeline: faster-whisper -> SRT -> batches -> Groq translation -> SRT/ASS -> ffmpeg burn-in.
This module only does text/command work; it never touches the network.
"""
from __future__ import annotations

import re

Cue = tuple  # (start_ms, end_ms, text)
_TS = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./\-]+$")
RLM = "\u200f"
# Burned-in subtitle size (PlayResY 720). Medium-large; callers asking for less get this floor.
DEFAULT_ASS_SIZE = 60
MIN_ASS_SIZE = 60


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


def to_ass(cues: list, font: str = "Estedad", size: int = DEFAULT_ASS_SIZE, rtl: bool = True) -> str:
    size = max(int(size), MIN_ASS_SIZE)
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,3,0,2,40,40,40,1\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for s, e, t in cues:
        clean = t.replace("{", "(").replace("}", ")").replace("\n", "\\N")
        if rtl:
            clean = RLM + clean
        lines.append(f"Dialogue: 0,{_ass_ts(s)},{_ass_ts(e)},Default,,0,0,0,,{clean}")
    return head + "\n".join(lines) + "\n"


# Blurred, darkened strip behind the subtitles so the text stays readable on any footage.
BLUR_BAND_FRACTION = 0.28   # share of frame height, anchored to the bottom edge
BLUR_SIGMA = 18
BLUR_TINT = "black@0.45"


def burn_cmd(video: str, ass: str, fontsdir: str, out: str, blur: bool = True) -> list:
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
