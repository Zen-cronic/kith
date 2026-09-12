import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from pypdf import PdfReader

url = os.environ.get("HOUSEHOLD_TEST_URL", "http://127.0.0.1:8010")
out = Path(os.environ.get("HOUSEHOLD_PROOF_DIR", "/tmp/household-browser10"))
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)

    def consent():
        page.locator('[data-code="es"]').click()
        expect(page.locator("#screen-consent")).to_be_visible()
        expect(page.locator("#consent-target")).to_contain_text("Un programa")
        page.locator("#consent-agree").click()
        expect(page.locator("#screen-document")).to_be_visible()

    consent()
    page.locator("#paste").fill("PRIVATE-LETTER-ALPHA")
    page.locator("#paste-title").fill("PRIVATE-TITLE-ALPHA")
    page.get_by_role("button", name="School field-trip permission letter", exact=False).click()
    expect(page.locator("#approved-reply")).to_be_visible(timeout=30000)
    approved = page.locator("#approved-reply-text").inner_text()
    original_form = page.locator("#original-form-text").inner_text()
    assert "I give permission" in original_form and "Phone: ____________________" in original_form
    assert "Complete el documento original" in page.locator("#original-form-note").inner_text()
    assert original_form in page.locator("#print-area").inner_text()
    assert "No se ha firmado" in page.locator("#card-statement").inner_text()
    assert "No se ha firmado" in page.locator("#print-area").inner_text()
    assert approved and approved in page.locator("#print-area").inner_text()
    assert "PRIVATE-LETTER-ALPHA" not in page.locator("#print-area").inner_text()
    page.locator("#approved-reply").scroll_into_view_if_needed()
    page.screenshot(path=str(out / "approved-reply.png"), full_page=True)
    page.locator("#verdict-card").scroll_into_view_if_needed()
    page.screenshot(path=str(out / "revise-loop.png"), full_page=True)
    page.evaluate(
        """() => { window.spoken = []; window.cancelled = 0; window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } }; speechSynthesis.getVoices = () => [{lang:'es-ES'}]; speechSynthesis.speak = u => window.spoken.push(u.text); speechSynthesis.cancel = () => window.cancelled++; }"""
    )
    page.locator("#read-aloud").click()
    spoken = page.evaluate("window.spoken.at(-1)")
    assert approved in spoken
    assert page.locator("#card-next").inner_text() in spoken
    assert page.locator("#card-who").inner_text() in spoken
    assert page.locator("#card-when").inner_text() in spoken
    assert page.locator("#card-who").inner_text().startswith("usted")
    assert "Fechas de la carta" in page.locator("#card-when").inner_text()
    assert page.locator("#card-who").inner_text() in page.locator("#print-area").inner_text()
    assert page.locator("#card-when").inner_text() in page.locator("#print-area").inner_text()
    page.emulate_media(media="print")
    page.screenshot(path=str(out / "print-reply.png"), full_page=True)
    page.pdf(path=str(out / "print-reply.pdf"), format="A4")
    page.emulate_media(media="screen")
    page.locator("#privacy-toggle").click()
    page.locator("#new-session").click()
    expect(page.locator("#screen-language")).to_be_visible()
    assert page.locator("#paste").input_value() == ""
    assert page.locator("#paste-title").input_value() == ""
    assert page.locator("#print-area").inner_text() == ""
    assert page.locator("#approved-reply-text").inner_text() == ""
    assert approved not in page.locator("body").text_content()
    assert original_form not in page.locator("body").text_content()
    assert "privacy" not in page.locator("body").get_attribute("class")
    assert page.evaluate("window.cancelled") >= 2
    page.screenshot(path=str(out / "new-visitor.png"), full_page=True)
    page.locator('[data-code="es"]').click()
    page.screenshot(path=str(out / "consent-spanish.png"), full_page=True)
    page.locator("#consent-decline").click()
    expect(page.locator("#consent-agree")).to_be_disabled()
    expect(page.locator("#consent-status")).to_contain_text("No se enviará")
    page.locator("#consent-back").click()
    page.locator('[data-code="fa"]').click()
    expect(page.locator("#consent-agree")).to_be_disabled()
    page.locator("#consent-confirm").check()
    expect(page.locator("#consent-agree")).to_be_enabled()
    page.locator("#consent-back").click()
    consent()
    # Exercise fetch failure, then a successful actual Strands N4 run.
    page.route("**/api/run", lambda route: route.abort("failed"))
    page.locator(".doc").first.click()
    expect(page.locator("#session-error")).to_be_visible()
    page.unroute("**/api/run")
    page.locator("#choose-document").click()
    page.get_by_role(
        "button",
        name="Ontario LTB Form N4 - Notice to End your Tenancy Early for Non-payment of Rent (filled) high real public form · demo",
        exact=True,
    ).click()
    expect(page.locator("#visitor-card")).to_be_visible(timeout=30000)
    expect(page.locator("#visitor-card")).to_have_class("card visitor-card escalate")
    expect(page.locator("#approved-reply")).to_be_hidden()
    assert "Draft to review with staff" not in page.locator("#print-area").inner_text()
    expect(page.locator("#card-who")).to_contain_text("Personal de esta organización")
    expect(page.locator("#card-when")).to_contain_text("No se ha confirmado")
    expect(page.locator("#backtrans-text")).to_contain_text("not an eviction order")
    expect(page.locator("#reading-card")).to_contain_text("Source-checked policy explanation")
    expect(page.locator("#print-area")).to_contain_text("no tiene que mudarse")
    expect(page.locator("#card-rule")).to_contain_text("Form N4")
    assert "15 minutes" not in page.locator("#print-area").inner_text()
    assert "pague $1,850.00 o múdese" not in page.locator("#print-area").inner_text()
    page.locator("#visitor-card").scroll_into_view_if_needed()
    page.screenshot(path=str(out / "n4-handoff.png"), full_page=True)
    page.locator("#privacy-toggle").click()
    page.screenshot(path=str(out / "n4-privacy.png"), full_page=True)
    page.locator("#privacy-toggle").click()
    page.emulate_media(media="print")
    page.pdf(path=str(out / "n4-summary.pdf"), format="A4")
    assert len(PdfReader(out / "n4-summary.pdf").pages) == 1
    page.emulate_media(media="screen")
    page.locator("#choose-document").click()
    page.get_by_role("button", name="Social-assistance case appointment letter", exact=False).click()
    expect(page.locator("#visitor-card")).to_be_visible(timeout=30000)
    expect(page.locator("#card-when")).to_contain_text("No se ha confirmado")
    expect(page.locator("#card-headline")).to_contain_text("La traducción necesita revisión")
    page.locator("#visitor-card").scroll_into_view_if_needed()
    page.screenshot(path=str(out / "fidelity-handoff.png"), full_page=True)
    # Adversarial final payload: even a proceed outcome cannot release an unapproved draft.
    response = page.request.post(
        url + "/api/run", data={"fixture_id": "school-trip-letter", "language": "es",
                               "consent_token": page.request.get(url + "/api/meta").json().get("consent_token")}
    )
    events = [json.loads(line[6:]) for line in response.text().splitlines() if line.startswith("data: ")]
    result = next(e["result"] for e in events if e["event"] == "result")
    result["verdicts"][-1]["decision"] = "revise"
    result["reading"]["source_issues"] = ["Unmatched source quote."]
    result["fidelity"]["meaning_warnings"] = ["Negation changed; a person must check the meaning."]
    result["fidelity"]["band"] = "unreliable"
    result["fidelity"]["score"] = 0.49
    page.route(
        "**/api/run",
        lambda route: route.fulfill(
            status=200,
            content_type="text/event-stream",
            body="data: " + json.dumps({"event": "result", "result": result}) + "\n\n",
        ),
    )
    page.locator("#choose-document").click()
    page.locator(".doc").first.click()
    expect(page.locator("#visitor-card")).to_be_visible()
    expect(page.locator("#approved-reply")).to_be_hidden()
    assert approved not in page.locator("#print-area").inner_text()
    expect(page.locator("#source-facts")).to_be_hidden()
    # Warning must be visible even if the final result is the only received event.
    expect(page.locator("#meaning-warnings")).to_be_visible()
    page.unroute("**/api/run")
    # A delayed old response deliberately ignores AbortSignal. The generation guard must reject it.
    page.locator("#new-session").click()
    consent()
    page.evaluate("""() => {
      window.normalFetch = window.fetch;
      window.fetch = (url, options) => url === '/api/run' ? new Promise(resolve => {
        window.releaseOld = () => resolve(new Response('data: ' + JSON.stringify({event:'node_done', node_id:'reader', run:1, status:'ok', execution_ms:1, output:{title:'STALE-VISITOR-LEAK', document_class:'old', stakes:'low', confidence:1, what_it_is:'old private letter', what_it_asks:'old private task'}}) + '\\n\\n', {status:200}));
      }) : window.normalFetch(url, options);
    }""")
    page.locator(".doc").first.click()
    expect(page.locator("#screen-session")).to_be_visible()
    page.locator("#new-session").click()
    page.evaluate("window.releaseOld()")
    page.wait_for_timeout(150)
    expect(page.locator("#screen-language")).to_be_visible()
    assert "STALE-VISITOR-LEAK" not in page.locator("body").text_content()
    assert not errors, errors
    print(
        json.dumps(
            {
                "passed": [
                    "approved reply on screen, audio and print",
                    "unchanged source form reference in screen/print and cleared on reset",
                    "new visitor clears pasted, rendered and print content",
                    "privacy and speech reset",
                    "language before consent",
                    "declined consent remains declined",
                    "staff-assisted consent for unlocalized languages",
                    "failed request recovery and N4 escalation",
                    "stale response rejected after reset",
                    "unapproved draft withheld from visitor and print",
                    "final-result meaning warning visible",
                    "unverified source values withheld from highlighted facts",
                    "Spanish who and when in screen, audio and print",
                    "N4 notice distinction and unconfirmed availability",
                    "source-checked N4 policy provenance, qualification and citation",
                ],
                "browserErrors": errors,
                "artifacts": str(out),
            },
            indent=2,
        )
    )
    browser.close()
