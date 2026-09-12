"""Rendered redesign checks; no provider calls outside labelled local fixture mode."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("HOUSEHOLD_TEST_URL", "http://127.0.0.1:8010")
OUT = Path(os.environ.get("HOUSEHOLD_PROOF_DIR", "/tmp/household-design-proof"))
OUT.mkdir(parents=True, exist_ok=True)
checks = []
captures = []
errors = []
failed_local = []


def fit(page, state):
    dimensions = page.evaluate("({width:innerWidth, document:document.documentElement.scrollWidth})")
    assert dimensions["document"] <= dimensions["width"] + 1, (state, dimensions)
    checks.append(state + ": no horizontal overflow")


def shot(page, name):
    page.screenshot(path=str(OUT / (name + ".png")), full_page=False)
    captures.append({"id": name, "path": str(OUT / (name + ".png")), "viewport": page.viewport_size})


with sync_playwright() as pw:
    browser = pw.chromium.launch()
    for device, viewport in [
        ("desktop", {"width": 1440, "height": 1000}),
        ("mobile", {"width": 390, "height": 844}),
    ]:
        page = browser.new_page(viewport=viewport)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on(
            "response",
            lambda r: failed_local.append({"url": r.url, "status": r.status})
            if r.url.startswith(URL) and r.status >= 400
            else None,
        )
        page.goto(URL)
        meta = page.request.get(URL + "/api/meta").json()
        assert meta["provider"] == "fake", "This verification requires a fixture-only preview."
        page.locator('[data-code="es"]').wait_for()
        page.evaluate("document.fonts.ready")
        fit(page, device + " welcome")
        shot(page, device + "-welcome")
        page.keyboard.press("Tab")
        focus = page.evaluate(
            '({tag:document.activeElement.tagName, visible:document.activeElement.matches(":focus-visible"), outline:getComputedStyle(document.activeElement).outlineStyle})'
        )
        assert focus["visible"] and focus["outline"] != "none", focus
        checks.append(device + ": keyboard focus visibly styled")
        page.locator('[data-code="es"]').click()
        fit(page, device + " consent")
        shot(page, device + "-consent")
        page.locator("#consent-agree").click()
        fit(page, device + " document")
        shot(page, device + "-document")
        page.get_by_role(
            "button",
            name="Ontario LTB Form N4 - Notice to End your Tenancy Early for Non-payment of Rent (filled) high real public form · demo",
            exact=True,
        ).click()
        expect(page.locator("#visitor-card")).to_be_visible(timeout=30000)
        expect(page.locator("#source-facts")).to_contain_text("21/09/2026")
        expect(page.locator("#source-facts")).to_contain_text("$1,850.00")
        fit(page, device + " N4")
        shot(page, device + "-n4")
        page.locator("#visitor-card").scroll_into_view_if_needed()
        shot(page, device + "-n4-visitor")
        page.locator("#privacy-toggle").click()
        fit(page, device + " privacy")
        shot(page, device + "-privacy")
        page.locator("#new-session").click()
        expect(page.locator("#screen-language")).to_be_visible()
        assert page.locator("#source-facts").text_content() == ""
        page.emulate_media(reduced_motion="reduce")
        motion = page.evaluate(
            """() => [...document.querySelectorAll('*')].filter(e=>e.getClientRects().length).filter(e=>{const c=getComputedStyle(e);return c.animationName!=='none' && c.animationDuration.split(',').some(t=>parseFloat(t)>.01)}).map(e=>e.id||e.className)"""
        )
        assert not motion, ("reduced-motion animations", motion)
        checks.append(device + ": reduced motion removes decorative animation")
        page.close()
    browser.close()
assert not errors, errors
assert not failed_local, failed_local
print(
    json.dumps(
        {
            "passed": checks,
            "browserErrors": errors,
            "failedLocalRequests": failed_local,
            "captures": captures,
        },
        indent=2,
    )
)
