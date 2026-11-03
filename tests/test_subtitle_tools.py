#!/usr/bin/env python3
"""Offline unit tests for scripts/subtitle_tools.py (no network, no ffmpeg)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import subtitle_tools as st  # noqa: E402

SRT = "1\n00:00:01,000 --> 00:00:02,500\nHello world\n\n2\n00:01:02,050 --> 00:01:04,000\nSecond line\nwraps\n"


class SubtitleToolsTest(unittest.TestCase):
    def test_srt_roundtrip(self):
        cues = st.parse_srt(SRT)
        self.assertEqual(cues[0], (1000, 2500, "Hello world"))
        self.assertEqual(cues[1][0], 62050)
        self.assertEqual(st.parse_srt(st.format_srt(cues)), cues)

    def test_batches_and_numbered(self):
        cues = [(i * 1000, i * 1000 + 900, f"line {i}") for i in range(130)]
        batches = st.make_batches(cues, max_chars=100000, max_cues=60)
        self.assertEqual([len(b[1]) for b in batches], [60, 60, 10])
        self.assertEqual(batches[1][0], 60)
        reply = st.numbered(["a", "b"], 5)
        self.assertEqual(st.parse_numbered(reply, 5, 3), ["a", "b", None])

    def test_numbered_batch_respects_character_limit_including_ids(self):
        cues = [(i * 1000, i * 1000 + 800, "x" * 48) for i in range(60)]
        batches = st.make_batches(cues, max_chars=3000, max_cues=60)
        self.assertGreaterEqual(len(batches), 1)
        for start, texts in batches:
            self.assertLessEqual(len(st.numbered(texts, start)), 3000)
            self.assertLessEqual(len(texts), 60)

    def test_single_oversized_cue_is_rejected_before_translation(self):
        with self.assertRaisesRegex(ValueError, "subtitle_cue_too_long"):
            st.make_batches([(0, 1000, "x" * 3000)], max_chars=3000)

    def test_apply_translation_fallback(self):
        cues = st.parse_srt(SRT)
        out, missing = st.apply_translation(cues, ["سلام", None])
        self.assertEqual(missing, 1)
        self.assertEqual(out[0][2], "سلام")
        self.assertEqual(out[1][2], cues[1][2])
        with self.assertRaises(ValueError):
            st.apply_translation(cues, ["x"])

    def test_ass_output(self):
        ass = st.to_ass([(1000, 2500, "سلام {دنیا}\nخط دوم")])
        self.assertIn("Estedad", ass)
        self.assertIn("Dialogue: 1,0:00:01.00,0:00:02.50", ass)
        self.assertIn("\\N", ass)
        style_line = next(line for line in ass.splitlines() if line.startswith("Style: Default,"))
        self.assertEqual(
            style_line,
            "Style: Default,Estedad,60,&H00FFFFFF,&H000000FF,&H66000000,&H80000000,"
            "-1,0,0,0,100,100,0,0,1,0,0,2,40,40,40,1",
        )
        self.assertNotIn(r"\ybord", ass)
        self.assertNotIn(r"\p1", ass)
        event_text = "\\n".join(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        self.assertNotIn("{", event_text)

    def test_opaque_box_vertical_padding_changes_symmetrically_only(self):
        ass = st.to_ass([(0, 1800, "نمونه زیرنویس")])
        style_line = next(line for line in ass.splitlines() if line.startswith("Style: Default,"))
        fields = style_line.split(",")
        # BorderStyle 1 removes the oversized ASS opaque box; drawing is added separately.
        self.assertEqual(fields[1], "Estedad")
        self.assertEqual(fields[2], "60")
        self.assertEqual(fields[15], "1")
        self.assertEqual(fields[16], "0")
        self.assertEqual(fields[17], "0")
        self.assertEqual(fields[21], "40")
        dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        self.assertNotIn(r"\ybord", dialogue)
        self.assertNotIn(r"\p1", dialogue)

    def test_ass_frame_renders_with_libass_when_available(self):
        ffmpeg = shutil.which("ffmpeg")
        self.assertIsNotNone(ffmpeg, "ffmpeg must be installed for real libass tests")
        filters = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout
        self.assertTrue(
            any("ass " in line and "libass" in line.lower() for line in filters.splitlines()),
            "ffmpeg must include the libass ASS filter",
        )

        repo_root = Path(__file__).resolve().parents[1]
        fontsdir = repo_root / "assets" / "fonts"
        with tempfile.TemporaryDirectory() as tmp:
            ass_path = Path(tmp) / "sample.ass"
            png_path = Path(tmp) / "sample.png"
            ass_path.write_text(
                st.to_ass([(0, 1800, "نمونه زیرنویس")]),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=white:s=1280x720:r=1:d=2",
                    "-vf", f"ass={ass_path}:fontsdir={fontsdir}",
                    "-frames:v", "1", str(png_path),
                ],
                capture_output=True, text=True, check=True, timeout=30,
            )
            self.assertTrue(png_path.is_file())
            self.assertGreater(png_path.stat().st_size, 100)
            self.assertEqual(png_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_ass_font_size_is_medium_large_with_floor(self):
        cue = [(1000, 2500, "سلام")]
        self.assertIn("Style: Default,Estedad,60,", st.to_ass(cue))
        self.assertIn("Style: Default,Estedad,44,", st.to_ass(cue, size=44))
        self.assertIn("Style: Default,Estedad,72,", st.to_ass(cue, size=72))

    def test_burn_cmd_defaults_to_plain_ass_filter(self):
        cmd = st.burn_cmd("in.mp4", "subs.ass", "assets/fonts", "out.mp4")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertNotIn("-filter_complex", cmd)
        self.assertEqual(cmd[cmd.index("-vf") + 1], "ass=subs.ass:fontsdir=assets/fonts")
        self.assertEqual(cmd[-3:-1], ["-c:a", "copy"])
        with self.assertRaises(ValueError):
            st.burn_cmd("in.mp4", "a'b.ass", "assets/fonts", "out.mp4")

    def test_burn_cmd_blur_true_builds_graph(self):
        cmd = st.burn_cmd("in.mp4", "subs.ass", "assets/fonts", "out.mp4", blur=True)
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("ass=subs.ass:fontsdir=assets/fonts", graph)
        # Opt-in legacy graph: only the bottom strip is blurred.
        self.assertIn("gblur=", graph)
        self.assertIn("drawbox=", graph)
        self.assertIn("overlay=x=0:y=H-h", graph)
        self.assertLess(graph.index("overlay="), graph.index("ass="))
        self.assertIn("[out]", cmd)
        self.assertIn("0:a?", cmd)
        self.assertEqual(cmd[-3:-1], ["-c:a", "copy"])

    def test_burn_cmd_without_blur_keeps_plain_filter(self):
        cmd = st.burn_cmd("in.mp4", "subs.ass", "assets/fonts", "out.mp4", blur=False)
        self.assertIn("ass=subs.ass:fontsdir=assets/fonts", cmd)
        self.assertNotIn("-filter_complex", cmd)


class SubtitleBidiTest(unittest.TestCase):
    def test_each_arabic_display_line_is_wrapped_independently_without_changing_srt(self):
        cues = [(1234, 5678, "سلام!..." + chr(10) + "متن، English 42؟" + chr(10) + "عدد ۱۲۳")]
        original_srt = st.format_srt(cues)

        ass = st.to_ass(cues)
        dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        body = dialogue.rsplit(",,", 1)[1].removeprefix("{" + chr(92) + "ybord4}")

        display_lines = body.split(chr(92) + "N")
        self.assertEqual(
            display_lines[:3],
            [
                chr(0x202B) + "سلام!..." + chr(0x202C),
                chr(0x202B) + "متن، English 42؟" + chr(0x202C),
                chr(0x202B) + "عدد ۱۲۳" + chr(0x202C),
            ],
        )
        self.assertEqual(len(display_lines), 3)
        self.assertIn("Default,,0,0,40,,", dialogue)
        self.assertEqual(st.format_srt(cues), original_srt)
        self.assertEqual(st.parse_srt(original_srt), cues)

    def test_latin_only_display_lines_are_not_wrapped(self):
        ass = st.to_ass([(0, 1000, "English 123!")])
        dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        body = dialogue.rsplit(",,", 1)[1].removeprefix("{" + chr(92) + "ybord4}")
        self.assertEqual(body, st.RLM + "English 123!")


    def test_bidi_punctuation_moves_to_left_edge_in_estedad_libass_png(self):
        ffmpeg = shutil.which("ffmpeg")
        self.assertIsNotNone(ffmpeg, "ffmpeg must be installed for real libass tests")
        filters = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout
        self.assertTrue(
            any("ass " in line and "libass" in line.lower() for line in filters.splitlines()),
            "ffmpeg must include the libass ASS filter",
        )
        from PIL import Image
        root = Path(__file__).resolve().parents[1]
        fontsdir = root / "assets" / "fonts"
        self.assertTrue((fontsdir / "Estedad-Regular.ttf").is_file())
        self.assertTrue((fontsdir / "Estedad-Bold.ttf").is_file())

        artifact_dir = os.environ.get("SUBTITLE_BIDI_ARTIFACT_DIR")
        if artifact_dir:
            output_dir = Path(artifact_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            self._render_and_check_bidi_images(ffmpeg, fontsdir, output_dir, Image)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                self._render_and_check_bidi_images(ffmpeg, fontsdir, Path(tmp), Image)

    def _render_and_check_bidi_images(self, ffmpeg, fontsdir, output_dir, Image):
        def render(name, ass_text, width, height):
            ass_path = output_dir / (name + ".ass")
            png_path = output_dir / (name + ".png")
            ass_path.write_text(ass_text, encoding="utf-8")
            subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=1:d=2",
                    "-vf", f"ass={ass_path}:fontsdir={fontsdir}",
                    "-frames:v", "1", str(png_path),
                ],
                capture_output=True, text=True, check=True, timeout=30,
            )
            self.assertTrue(png_path.is_file())
            self.assertEqual(png_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            return Image.open(png_path).convert("RGB")

        def components(image, scale):
            pixels = image.load()
            active = set()
            for y in range(int(image.height * 0.60), image.height):
                for x in range(image.width):
                    red, green, blue = pixels[x, y]
                    if red > 210 and green > 210 and blue > 210:
                        active.add((x, y))
            found = []
            while active:
                seed = active.pop()
                stack = [seed]
                xs, ys = [seed[0]], [seed[1]]
                while stack:
                    x, y = stack.pop()
                    for ny in range(y - 1, y + 2):
                        for nx in range(x - 1, x + 2):
                            point = (nx, ny)
                            if point in active:
                                active.remove(point)
                                stack.append(point)
                                xs.append(nx)
                                ys.append(ny)
                found.append((min(xs), min(ys), max(xs), max(ys), len(xs)))
            return found

        def exclamation_and_body(image, scale):
            parts = components(image, scale)
            stems = [
                part for part in parts
                if part[2] - part[0] <= max(18, round(18 * scale))
                and part[3] - part[1] >= max(14, round(14 * scale))
            ]
            dots = [
                part for part in parts
                if part[2] - part[0] <= max(13, round(13 * scale))
                and part[3] - part[1] <= max(13, round(13 * scale))
                and part[4] <= round(180 * scale * scale)
            ]
            marker = None
            for stem in stems:
                sx = (stem[0] + stem[2]) / 2
                for dot in dots:
                    dx = (dot[0] + dot[2]) / 2
                    if abs(sx - dx) <= max(5, round(5 * scale)) and -1 <= dot[1] - stem[3] <= round(18 * scale):
                        marker = (
                            min(stem[0], dot[0]), min(stem[1], dot[1]),
                            max(stem[2], dot[2]), max(stem[3], dot[3]),
                        )
                        break
                if marker:
                    break
            self.assertIsNotNone(marker, "could not isolate the Estedad exclamation glyph")
            non_marker = [
                part for part in parts
                if not (
                    part[0] >= marker[0] - 2 and part[2] <= marker[2] + 2
                    and part[1] >= marker[1] - 2 and part[3] <= marker[3] + 2
                )
            ]
            self.assertTrue(non_marker, "Persian word glyphs were not rendered")
            return (
                marker[0], marker[2],
                min(part[0] for part in non_marker),
                max(part[2] for part in non_marker),
            )

        for width, height in ((1920, 1080), (1280, 720), (1080, 1920), (720, 1280), (1080, 1080)):
            scale = min(width, height) / 720
            cue = [(0, 1800, "سلام!")]
            after_ass = st.to_ass(cue, width=width, height=height)
            after_dialogue = next(line for line in after_ass.splitlines() if line.startswith("Dialogue:"))
            prefix = after_dialogue.rsplit(",,", 1)[0]
            before_dialogue = prefix + ",," + st.RLM + "سلام!"
            before_ass = after_ass.replace(after_dialogue, before_dialogue)
            name = f"bidi-{width}x{height}"
            before = render(name + "-before", before_ass, width, height)
            after = render(name + "-after", after_ass, width, height)

            before_left, before_right, before_body_left, before_body_right = exclamation_and_body(before, scale)
            after_left, after_right, after_body_left, after_body_right = exclamation_and_body(after, scale)
            self.assertGreater(
                before_left, before_body_right,
                f"{width}x{height}: legacy leading RLM should leave ! on the right edge",
            )
            self.assertLess(
                after_right, after_body_left,
                f"{width}x{height}: RLE/PDF should move ! to the left edge of Persian text",
            )

class SubtitleLayoutMatrixTest(unittest.TestCase):
    RESOLUTIONS = [
        (1920, 1080),
        (1280, 720),
        (1080, 1920),
        (720, 1280),
        (1080, 1080),
    ]

    SHORT_LINES = [
        "آره، آره. EN 42!",
        "فارسی؟ ۱۲۳!",
        "English 42!",
    ]
    LONG_LINE = (
        "این جملهٔ فارسی نسبتاً بلند است و باید با اندازه‌گیری واقعی libass "
        "در محدودهٔ امن عرض بماند، کادر کامل داشته باشد و English 123 و نشانه‌ها؟! "
        "را بدون بیرون‌زدگی نمایش بدهد"
    )

    def test_real_libass_estedad_layout_matrix_has_complete_balanced_boxes(self):
        ffmpeg = shutil.which("ffmpeg")
        self.assertIsNotNone(ffmpeg, "ffmpeg must be installed for real libass matrix tests")
        filters = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout
        self.assertTrue(
            any("ass " in line and "libass" in line.lower() for line in filters.splitlines()),
            "ffmpeg must include the libass ASS filter",
        )

        from PIL import Image

        root = Path(__file__).resolve().parents[1]
        fontsdir = root / "assets" / "fonts"
        self.assertTrue((fontsdir / "Estedad-Regular.ttf").is_file())
        self.assertTrue((fontsdir / "Estedad-Bold.ttf").is_file())

        artifact_dir = os.environ.get("SUBTITLE_LAYOUT_ARTIFACT_DIR")
        if artifact_dir:
            output_dir = Path(artifact_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            self._run_matrix(ffmpeg, fontsdir, output_dir, Image)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                self._run_matrix(ffmpeg, fontsdir, Path(tmp), Image)

    def _run_matrix(self, ffmpeg, fontsdir, output_dir, Image):
        def mask_bbox(image, low, high):
            gray = image.convert("L")
            lookup = [255 if low < value < high else 0 for value in range(256)]
            return gray.point(lookup).getbbox()

        for width, height in self.RESOLUTIONS:
            portrait = height > width
            max_lines = 2 if portrait else 3
            short_edge = min(width, height)
            expected_padding = round(12 * short_edge / 720)
            ratio_limit = round(width * 0.80)

            for requested_lines in (1, 2, 3):
                for variant in ("short", "long"):
                    with self.subTest(width=width, height=height, lines=requested_lines, variant=variant):
                        source_lines = (
                            self.SHORT_LINES[:requested_lines]
                            if variant == "short"
                            else [self.LONG_LINE for _ in range(requested_lines)]
                        )
                        input_cues = [(0, 18000, "\n".join(source_lines))]
                        layouted, font_sizes = st.layout_cues(
                            input_cues, width=width, height=height,
                            fontsdir=str(fontsdir), font="Estedad",
                        )
                        self.assertTrue(layouted, "layout must not silently remove subtitle text")
                        self.assertEqual(len(layouted), len(font_sizes))
                        if variant == "short":
                            self.assertEqual(
                                sum(len(text.split("\n")) for _start, _end, text in layouted),
                                requested_lines,
                                "short matrix cases must exercise exactly the requested number of rendered lines",
                            )
                        self.assertTrue(all(16 <= size <= round(60 * short_edge / 720) for size in font_sizes))
                        for _start, _end, text in layouted:
                            self.assertLessEqual(
                                len(text.split("\n")), max_lines,
                                "one portrait event must never contain more than two lines",
                            )

                        ass_text, measured = st.to_ass_with_glyph_boxes(
                            layouted, fontsdir=str(fontsdir), font="Estedad",
                            width=width, height=height, font_sizes=font_sizes,
                            ffmpeg=ffmpeg,
                        )
                        self.assertEqual(len(measured), len(layouted))
                        self.assertIn(f"PlayResX: {width}", ass_text)
                        self.assertIn(f"PlayResY: {height}", ass_text)
                        self.assertIn("WrapStyle: 2", ass_text)
                        self.assertEqual(
                            len([line for line in ass_text.splitlines()
                                 if line.startswith("Dialogue: 0,") and r"\p1" in line]),
                            len(layouted),
                            "each complete cue block must have exactly one independent background drawing",
                        )
                        for left, _top, right, _bottom in measured:
                            self.assertLessEqual(
                                right - left, ratio_limit + 1,
                                "measured libass glyph width must stay inside 80% of actual frame width",
                            )

                        # Re-time the already measured drawing/text pairs to one second each,
                        # so every final libass render is independently inspectable as a PNG.
                        frame_ass = []
                        frame_index = -1
                        for line in ass_text.splitlines():
                            if line.startswith("Dialogue: 0,"):
                                frame_index += 1
                                fields = line.split(",", 9)
                                fields[1] = st._ass_ts(frame_index * 1000)
                                fields[2] = st._ass_ts((frame_index + 1) * 1000)
                                line = ",".join(fields)
                            elif line.startswith("Dialogue: 1,"):
                                fields = line.split(",", 9)
                                fields[1] = st._ass_ts(frame_index * 1000)
                                fields[2] = st._ass_ts((frame_index + 1) * 1000)
                                line = ",".join(fields)
                            frame_ass.append(line)
                        self.assertEqual(frame_index + 1, len(layouted))

                        stem = f"{width}x{height}-{requested_lines}line-{variant}"
                        ass_path = output_dir / f"{stem}.ass"
                        frame_pattern = output_dir / f"{stem}-%02d.png"
                        ass_path.write_text("\n".join(frame_ass) + "\n", encoding="utf-8")
                        subprocess.run(
                            [
                                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                                "-f", "lavfi", "-i",
                                f"color=c=0x808080:s={width}x{height}:r=1:d={len(layouted)}",
                                "-vf", f"ass={ass_path}:fontsdir={fontsdir}",
                                "-frames:v", str(len(layouted)), "-vsync", "0", str(frame_pattern),
                            ],
                            capture_output=True, text=True, check=True, timeout=60,
                        )

                        for index, (glyph_bounds, _cue, _font_size) in enumerate(zip(measured, layouted, font_sizes), 1):
                            png_path = output_dir / f"{stem}-{index:02d}.png"
                            self.assertTrue(png_path.is_file(), f"missing actual libass PNG: {png_path.name}")
                            image = Image.open(png_path).convert("RGB")
                            self.assertEqual(image.size, (width, height))
                            self.assertEqual(png_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                            glyph = mask_bbox(image, 220, 256)
                            box = mask_bbox(image, 5, 105)
                            self.assertIsNotNone(glyph, "Estedad text pixels must be rendered")
                            self.assertIsNotNone(box, "translucent black box pixels must be rendered")

                            left_pad = glyph[0] - box[0]
                            top_pad = glyph[1] - box[1]
                            right_pad = box[2] - glyph[2]
                            bottom_pad = box[3] - glyph[3]
                            pads = {
                                "left": left_pad,
                                "top": top_pad,
                                "right": right_pad,
                                "bottom": bottom_pad,
                            }
                            for side, value in pads.items():
                                self.assertLessEqual(
                                    abs(value - expected_padding), 2,
                                    f"{stem} {index} {side} padding={value}px; expected {expected_padding}px ±2",
                                )
                            self.assertLessEqual(
                                abs(top_pad - bottom_pad), 2,
                                f"{stem} {index} top/bottom padding must be symmetric",
                            )
                            self.assertLessEqual(
                                abs(left_pad - right_pad), 2,
                                f"{stem} {index} left/right padding must be symmetric",
                            )
                            self.assertGreaterEqual(box[0], 0)
                            self.assertGreaterEqual(box[1], 0)
                            self.assertLessEqual(box[2], width)
                            self.assertLessEqual(box[3], height)
                            self.assertLessEqual(box[0], glyph[0])
                            self.assertLessEqual(box[1], glyph[1])
                            self.assertGreaterEqual(box[2], glyph[2])
                            self.assertGreaterEqual(box[3], glyph[3])
                            for actual, measured_edge in zip(glyph, glyph_bounds):
                                self.assertLessEqual(
                                    abs(actual - measured_edge), 2,
                                    "final antialiased glyph bounds must match the libass measurement within 2 px",
                                )

    def test_font_size_uses_actual_short_edge_and_portrait_is_not_larger(self):
        short_cue = [(0, 5000, "سلام! English 42")]
        landscape, landscape_sizes = st.layout_cues(
            short_cue, width=1280, height=720, fontsdir="assets/fonts",
        )
        portrait, portrait_sizes = st.layout_cues(
            short_cue, width=720, height=1280, fontsdir="assets/fonts",
        )
        high_resolution, high_sizes = st.layout_cues(
            short_cue, width=1920, height=1080, fontsdir="assets/fonts",
        )
        self.assertEqual(landscape_sizes, [60])
        self.assertEqual(portrait_sizes, [60])
        self.assertEqual(high_sizes, [90])
        self.assertLessEqual(
            portrait_sizes[0] / 720, landscape_sizes[0] / 720,
            "portrait subtitles must not be relatively larger than landscape subtitles",
        )
        self.assertLessEqual(len(portrait[0][2].split("\n")), 2)
        self.assertLessEqual(len(landscape[0][2].split("\n")), 3)






if __name__ == "__main__":
    unittest.main()
