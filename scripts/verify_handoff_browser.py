"""Real-browser staff handoff lifecycle, using synthetic names and fixture letters."""
import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader

url = os.environ.get("HOUSEHOLD_TEST_URL", "http://127.0.0.1:8011")
out = Path(os.environ.get("HOUSEHOLD_PROOF_DIR", "/tmp/household-handoff17"))
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1100}, timezone_id="America/Toronto")
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    page.goto(url)
    page.locator('[data-code="es"]').click()
    page.locator("#consent-agree").click()
    page.get_by_role("button", name="Ontario LTB Form N4 - Notice to End your Tenancy Early for Non-payment of Rent (filled) high real public form · demo", exact=True).click()
    expect(page.locator("#visitor-card")).to_be_visible(timeout=30000)
    expect(page.locator("#visitor-card")).to_have_class("card visitor-card escalate")
    original_statement = page.locator("#card-statement").inner_text()
    original_when = page.locator("#card-when").inner_text()
    page.locator("#handoff-editor summary").click()
    expect(page.locator("#visitor-handoff")).to_be_hidden()
    page.locator("#handoff-save").click()
    expect(page.locator("#visitor-handoff")).to_be_hidden()
    page.locator("#handoff-owner").fill("Demo Sam — intake worker")
    page.locator("#handoff-place").fill("Demo reception desk")
    page.locator("#handoff-recorded-by").fill("Demo Jo")
    page.locator("#handoff-action").select_option("review")
    page.locator("#handoff-mode").select_option("now")
    page.locator("#handoff-save").click()
    expect(page.locator("#visitor-handoff")).to_be_hidden()
    network_before = len(requests)
    page.locator("#handoff-attest").check()
    page.locator("#handoff-save").click()
    expect(page.locator("#visitor-handoff")).to_be_visible()
    expect(page.locator("#visitor-handoff")).to_contain_text("Confirmación del personal")
    expect(page.locator("#visitor-handoff")).to_contain_text("Demo Sam — intake worker")
    expect(page.locator("#visitor-handoff")).to_contain_text("America/Toronto")
    assert len(requests) == network_before, "Staff plan must remain local, without provider or server calls"
    assert page.locator("#card-statement").inner_text() == original_statement
    assert page.locator("#card-when").inner_text() == original_when
    expect(page.locator("#approved-reply")).to_be_hidden()
    expect(page.locator("#visitor-card")).to_have_class("card visitor-card escalate")
    assert "Staff confirmation" in page.locator("#print-area").inner_text()
    assert "Confirmación del personal" in page.locator("#print-area").inner_text()
    page.evaluate("""() => { window.spoken = []; window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } }; speechSynthesis.getVoices = () => [{lang:'es-ES'}]; speechSynthesis.speak = u => window.spoken.push(u.text); speechSynthesis.cancel = () => {}; }""")
    page.locator("#read-aloud").click()
    spoken = page.evaluate("window.spoken.at(-1)")
    assert "Demo Sam — intake worker" in spoken and "Revisar juntos esta carta" in spoken
    page.locator("#handoff-owner").fill("Demo Alex <img src=x onerror=alert(1)>")
    expect(page.locator("#handoff-attest")).not_to_be_checked()
    expect(page.locator("#visitor-handoff")).to_contain_text("Demo Sam — intake worker")
    page.locator("#handoff-mode").select_option("scheduled")
    page.locator("#handoff-time").fill("2001-01-01T10:30")
    page.locator("#handoff-attest").check()
    page.locator("#handoff-save").click()
    expect(page.locator("#handoff-error")).to_contain_text("future")
    expect(page.locator("#visitor-handoff")).to_contain_text("Demo Sam — intake worker")
    page.locator("#handoff-time").fill("2027-02-20T15:00")
    expect(page.locator("#handoff-attest")).not_to_be_checked()
    page.locator("#handoff-attest").check()
    page.locator("#handoff-save").click()
    expect(page.locator("#visitor-handoff")).to_contain_text("Demo Alex <img src=x onerror=alert(1)>")
    assert page.locator("#visitor-handoff img").count() == 0
    assert page.locator("#print-area img").count() == 0
    expect(page.locator("#visitor-handoff")).to_contain_text("GMT-05:00")
    expect(page.locator("#visitor-handoff")).to_contain_text("20 de febrero de 2027")
    # Use a clean synthetic label for the visual proof.
    page.locator("#handoff-owner").fill("Demo Alex — intake worker")
    page.locator("#handoff-attest").check()
    page.locator("#handoff-save").click()
    page.locator("#handoff-editor").scroll_into_view_if_needed()
    page.locator("#visitor-handoff").scroll_into_view_if_needed()
    page.screenshot(path=str(out / "staff-confirmation.png"), full_page=True)
    page.emulate_media(media="print")
    page.pdf(path=str(out / "staff-confirmation.pdf"), format="A4")
    assert len(PdfReader(out / "staff-confirmation.pdf").pages) == 2
    page.emulate_media(media="screen")
    page.locator("#handoff-withdraw").click()
    expect(page.locator("#visitor-handoff")).to_be_hidden()
    assert "Demo Alex" not in page.locator("#print-area").inner_text()
    assert page.locator("#handoff-owner").input_value() == ""
    page.locator("#handoff-owner").fill("Demo Pat")
    page.locator("#handoff-place").fill("Demo desk 2")
    page.locator("#handoff-recorded-by").fill("Demo Jo")
    page.locator("#handoff-action").select_option("contact")
    page.locator("#handoff-mode").select_option("now")
    page.locator("#handoff-attest").check()
    page.locator("#handoff-save").click()
    expect(page.locator("#visitor-handoff")).to_contain_text("Demo Pat")
    assert len(requests) == network_before
    page.locator("#new-session").click()
    expect(page.locator("#screen-language")).to_be_visible()
    assert page.locator("#handoff-owner").input_value() == ""
    assert page.locator("#handoff-recorded-by").input_value() == ""
    assert page.locator("#visitor-handoff").inner_text() == ""
    assert page.locator("#print-area").inner_text() == ""
    assert "Demo Pat" not in page.locator("body").text_content()
    assert not page.evaluate("Object.keys(localStorage).length || Object.keys(sessionStorage).length")
    assert not errors, errors
    receipt = {"passed": ["required fields and staff attestation", "local confirmation without network/storage", "policy refusal and draft withholding preserved", "English/Spanish print and Spanish audio", "edits require reconfirmation; prior plan remains until saved", "past-time rejection and explicit timezone/offset", "staff text escaped in screen and print", "withdrawal and new visitor clear all confirmation data"], "browserErrors": errors, "artifacts": str(out)}
    (out / "checks.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    browser.close()
