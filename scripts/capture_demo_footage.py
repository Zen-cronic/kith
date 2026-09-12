"""Record real local fixture interactions for the product film, never a model benchmark."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("HOUSEHOLD_TEST_URL", "http://127.0.0.1:8010")
OUT = Path(os.environ.get("HOUSEHOLD_DEMO_MEDIA", "/tmp/household-demo-footage"))
OUT.mkdir(parents=True, exist_ok=True)
receipts = []
N4 = "Ontario LTB Form N4 - Notice to End your Tenancy Early for Non-payment of Rent (filled) high real public form · demo"
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    for case in ["routine", "n4", "handoff"]:
        raw = OUT / ("raw-" + case)
        raw.mkdir(exist_ok=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            record_video_dir=str(raw),
            record_video_size={"width": 1280, "height": 900},
            timezone_id="America/Toronto",
        )
        page = ctx.new_page()
        errs = []
        page.on("pageerror", lambda e, errors=errs: errors.append(str(e)))
        page.goto(URL)
        assert page.request.get(URL + "/api/meta").json()["provider"] == "fake"
        page.locator('[data-code="es"]').click()
        page.locator("#consent-agree").click()
        page.wait_for_timeout(700)
        start = time.monotonic()
        page.get_by_role(
            "button",
            name="School field-trip permission letter" if case == "routine" else N4,
            exact=case != "routine",
        ).click()
        expect(page.locator("#visitor-card")).to_be_visible(timeout=30000)
        page.wait_for_timeout(1700)
        if case == "routine":
            page.locator("#verdict-card").scroll_into_view_if_needed()
            page.wait_for_timeout(4000)
            page.locator("#draft-card").scroll_into_view_if_needed()
            page.wait_for_timeout(3000)
            page.locator("#approved-reply").scroll_into_view_if_needed()
            page.wait_for_timeout(5000)
        elif case == "n4":
            page.locator("#verdict-card").scroll_into_view_if_needed()
            page.wait_for_timeout(4000)
            page.locator("#visitor-card").scroll_into_view_if_needed()
            page.wait_for_timeout(3000)
            page.locator("#privacy-toggle").click()
            page.wait_for_timeout(5000)
        else:
            editor = page.locator("#handoff-editor")
            editor.scroll_into_view_if_needed()
            editor.locator("summary").click()
            page.locator("#handoff-owner").fill("Demo Alex · intake worker")
            page.locator("#handoff-place").fill("Demo reception desk")
            page.locator("#handoff-action").select_option("review")
            page.locator("#handoff-mode").select_option("now")
            page.locator("#handoff-recorded-by").fill("Demo Jo")
            page.wait_for_timeout(1200)
            page.locator("#handoff-attest").check()
            page.locator("#handoff-save").click()
            page.locator("#visitor-handoff").scroll_into_view_if_needed()
            page.wait_for_timeout(5500)
            page.locator("#privacy-toggle").click()
            page.locator("#visitor-handoff").scroll_into_view_if_needed()
            page.wait_for_timeout(4500)
        page.screenshot(path=str(OUT / (case + "-last.png")))
        elapsed = time.monotonic() - start
        video = page.video
        ctx.close()
        source = Path(video.path())
        target = OUT / (case + ".webm")
        shutil.copy2(source, target)
        probe = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=width,height,r_frame_rate",
                    "-of",
                    "json",
                    str(target),
                ],
                text=True,
            )
        )
        assert not errs, errs
        receipts.append(
            {
                "case": case,
                "file": str(target),
                "interactionSeconds": elapsed,
                "probe": probe,
                "provider": "fake",
                "realUIInteractions": True,
                "viewport": {"width": 1280, "height": 900},
                "audio": "none",
            }
        )
        print(json.dumps(receipts[-1]), flush=True)
    browser.close()
(OUT / "capture-receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
