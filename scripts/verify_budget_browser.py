"""Inspect a real limited-server failure and recovery against a normal built server."""

import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("HOUSEHOLD_TEST_URL", "http://127.0.0.1:8011")
LIMITED_URL = os.environ.get("HOUSEHOLD_LIMITED_URL", "http://127.0.0.1:8013")
OUT = Path(os.environ.get("HOUSEHOLD_PROOF_DIR", "/tmp/household-budget-browser"))
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("""window.spoken=[];
      Object.defineProperty(window,'speechSynthesis',{value:{cancel(){},getVoices(){return [{lang:'es-ES'}]},speak(u){window.spoken.push(u.text)}}});
      window.SpeechSynthesisUtterance=function(text){this.text=text};""")
    page.goto(URL)
    assert page.request.get(URL + "/api/meta").json()["provider"] == "fake"
    limited_meta = page.request.get(LIMITED_URL + "/api/meta").json()
    assert limited_meta["provider"] == "fake" and limited_meta["max_model_calls"] == 3

    def limited_run(route):
        response = page.request.post(LIMITED_URL + "/api/run", data=route.request.post_data, headers={"Content-Type": "application/json"})
        events = [json.loads(line[6:]) for line in response.text().splitlines() if line.startswith("data: ")]
        assert events[-1]["code"] == "model_call_limit"
        assert events[-1]["model_calls"]["attempted"] == 3
        assert not any(e["event"] == "result" for e in events)
        assert any(e["event"] == "node_done" and e["node_id"] == "interpreter" for e in events)
        (OUT / "limited-response.json").write_text(json.dumps(events, indent=2))
        # Actual built-server SSE, including completed partial nodes, not fabricated events.
        route.fulfill(response=response)

    page.route("**/api/run", limited_run, times=1)
    page.locator('[data-code="es"]').click()
    page.locator("#consent-agree").click()
    page.get_by_role("button", name="School field-trip permission letter").click()
    expect(page.locator("#session-error")).to_contain_text("3-call model limit")
    expect(page.locator("#visitor-text")).to_have_text("La lectura no terminó. Pida ayuda al personal.")
    for target in ["visitor-card", "draft-card", "approved-reply", "visitor-handoff", "source-facts"]:
        expect(page.locator("#" + target)).to_be_hidden()
    for target in ["print-area", "draft-log", "card-statement", "source-facts"]:
        assert page.locator("#" + target).text_content() == ""
    expect(page.locator("#print-summary")).to_be_disabled()
    page.locator("#read-aloud").click()
    assert page.evaluate("window.spoken") == ["La lectura no terminó. Pida ayuda al personal."]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(OUT / "limit-error-desktop.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(OUT / "limit-error-mobile.png"), full_page=True)
    page.locator("#choose-document").click()
    page.get_by_role("button", name="School field-trip permission letter").click()
    expect(page.locator("#visitor-card")).to_be_visible(timeout=30000)
    expect(page.locator("#approved-reply")).to_be_visible()
    expect(page.locator("#session-error")).to_be_hidden()
    expect(page.locator("#print-summary")).to_be_enabled()
    page.screenshot(path=str(OUT / "recovered-mobile.png"), full_page=True)
    assert not errors, errors
    browser.close()

receipt = {"passed": ["real built-server typed exhaustion after partial interpretation", "no final result", "partial visitor/draft/source/print content cleared", "only unfinished-reading help spoken", "printing disabled after exhaustion", "desktop/mobile no horizontal overflow", "new document recovers through normal built server"], "pageErrors": errors, "artifacts": str(OUT)}
(OUT / "checks.json").write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt, indent=2))
