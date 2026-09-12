#!/usr/bin/env python3
"""Render the deterministic captioned Front Desk film to a local 1080p MP4.

Requires the running fake-provider app, Python Playwright, Chromium and ffmpeg.
The three real browser recordings must already exist under static/demo/media/.
No model, video-generation provider, hosted rendering or upload is used.
"""

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8010/static/demo/index.html")
    parser.add_argument("--output", default="/tmp/household-design-demo/household-1080p.mp4")
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--duration", type=float, default=72)
    args = parser.parse_args()
    if not 1 <= args.fps <= 60 or not 0 < args.duration <= 72:
        parser.error("fps must be 1–60 and duration greater than 0 and at most 72 seconds")
    if not shutil.which("ffmpeg"):
        parser.error("ffmpeg must be installed")
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    started = time.monotonic()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url + ("&" if "?" in args.url else "?") + "render=1", wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.wait_for_function(
            "Array.from(document.querySelectorAll('video')).every(v => v.readyState >= 2 && Number.isFinite(v.duration))",
            timeout=30000,
        )
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-framerate",
            str(args.fps),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ]
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        frames = round(args.duration * args.fps)
        try:
            for frame in range(frames):
                page.evaluate("(t) => window.frontDeskDemo.renderFrame(t)", frame / args.fps)
                process.stdin.write(page.screenshot(type="jpeg", quality=93))
                if frame % (args.fps * 6) == 0:
                    print(f"{frame/args.fps:05.1f}s / {args.duration:g}s rendered", flush=True)
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError("ffmpeg encoding failed")
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            browser.close()
    if errors:
        raise RuntimeError(f"Browser errors: {errors}")
    receipt = {
        "output": str(output),
        "width": 1920,
        "height": 1080,
        "fps": args.fps,
        "duration": frames / args.fps,
        "frames": frames,
        "audio": "none; English on-screen narration",
        "elapsedSeconds": round(time.monotonic() - started, 1),
        "browserErrors": errors,
    }
    output.with_suffix(".json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
