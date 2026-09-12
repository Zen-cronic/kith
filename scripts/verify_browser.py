"""Prove the household web UI in a real browser against the built wheel, not the checkout.

Extracts the newest `dist/household-*.whl`, starts its own uvicorn from the extracted tree (PYTHONPATH=installed,
EXECUTION_MODE=live with AWS credentials, instance metadata and outbound HTTP all blocked so nothing can leave the
machine), drives the screens with Playwright, and writes screenshots plus `receipt.json` under `runs/browser/`.

Checks: identify with a PIN (wrong then right) -> ledger with grant states -> upload endpoint -> in-scope run with
receipt chips that match the executor's labels -> a minor's request queues an approval that only an adult PIN
releases -> a decline -> an expired grant surfaces its reason -> the stale-response guard -> zero page errors.

    poetry build -f wheel && MODEL_PROVIDER=fake HOUSEHOLD_TEST_URL=http://127.0.0.1:8022 python scripts/verify_browser.py
    HOUSEHOLD_EXTERNAL_SERVER=1 HOUSEHOLD_DATA_DIR=... python scripts/verify_browser.py   # against a server you run
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("HOUSEHOLD_TEST_URL", "http://127.0.0.1:8022").rstrip("/")
OUT = Path(os.environ.get("HOUSEHOLD_PROOF_DIR", ROOT / "runs" / "browser")).resolve()
EXTERNAL = os.environ.get("HOUSEHOLD_EXTERNAL_SERVER", "") == "1"
DATA_DIR = Path(os.environ.get("HOUSEHOLD_DATA_DIR", OUT / "data")).resolve()
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
STALE_MARKER = "STALE-VISITOR-LEAK"
# Everything a server process would need to reach AWS or the internet is removed or pointed at a dead port.
BLOCKED_ENV = ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "SES_FROM",
               "STRIPE_SECRET_KEY", "AEROAPI_KEY", "HOUSEHOLD_ALLOW_LIVE_SES", "SESSION_BACKEND", "RUNTIME_HTTP_URL",
               "HOUSEHOLD_RUNTIME_ARN", "NODE_MODELS")
SERVER_ENV = {
    "MODEL_PROVIDER": "fake", "EXECUTION_MODE": "live", "HOUSEHOLD_ALLOW_RESET": "1", "OTEL_SDK_DISABLED": "true",
    "AWS_EC2_METADATA_DISABLED": "true", "AWS_SHARED_CREDENTIALS_FILE": "/nonexistent", "AWS_CONFIG_FILE": "/nonexistent",
    "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost",
}


def api(method: str, path: str, data: dict | None = None) -> dict | list:
    request = urllib.request.Request(URL + path, method=method, headers={"content-type": "application/json"},
                                     data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def ready(deadline: float = 90) -> dict:
    end = time.monotonic() + deadline
    while True:
        try:
            return api("GET", "/api/meta")
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if time.monotonic() > end:
                raise
            time.sleep(0.25)


def newest_wheel() -> Path:
    wheels = sorted((ROOT / "dist").glob("household-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        sys.exit("no wheel under dist/; run `poetry build -f wheel` first")
    return wheels[-1]


def server_env(installed: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in BLOCKED_ENV}
    env.update(SERVER_ENV)
    env.update({"PYTHONPATH": str(installed), "HOUSEHOLD_DATA_DIR": str(DATA_DIR)})
    return env


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    receipt: dict = {"status": "failed", "url": URL, "externalServer": EXTERNAL, "cloudDeployment": False,
                     "serverEnv": {k: v for k, v in SERVER_ENV.items()}, "checks": [], "screenshots": [], "pageErrors": []}
    server: subprocess.Popen | None = None
    log = None
    try:
        if not EXTERNAL:
            wheel = newest_wheel()
            receipt["wheel"] = wheel.name
            receipt["wheelSha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
            installed = OUT / "installed"
            shutil.rmtree(installed, ignore_errors=True)
            installed.mkdir(parents=True)
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(installed)
            env = server_env(installed)
            resolved = subprocess.check_output([sys.executable, "-c", "import household; print(household.__file__)"], cwd=installed, env=env, text=True).strip()
            assert Path(resolved).is_relative_to(installed), f"the server would import {resolved}, not the extracted wheel"
            receipt["importedFrom"] = resolved
            shutil.rmtree(DATA_DIR, ignore_errors=True)
            port = urlsplit(URL).port or 80
            log = (OUT / "server.log").open("w")
            server = subprocess.Popen([sys.executable, "-m", "uvicorn", "household.web.app:app", "--host", urlsplit(URL).hostname or "127.0.0.1", "--port", str(port)],
                                      cwd=installed, env=env, stdout=log, stderr=subprocess.STDOUT)
        meta = ready()
        assert meta["provider"] == "fake" and meta["backend"] == "local" and meta["reset_enabled"], meta
        rails = {r["id"]: r for r in meta["rails"]["items"]}
        assert set(rails) == {"ses-email", "internal-ledger", "stripe-test", "official-form", "external-api-readonly"}
        if not EXTERNAL:
            assert meta["execution_mode"] == "live" and rails["ses-email"]["now"]["mode"] == "SIMULATED", rails["ses-email"]
            assert rails["internal-ledger"]["now"]["mode"] == "COMPLETE", rails["internal-ledger"]
        receipt["executionMode"] = meta["execution_mode"]
        receipt["railsNow"] = {rail: r["now"] for rail, r in rails.items()}
        receipt["checks"].append("meta: fake provider, local backend, five rails with environment-derived labels, no email can leave (SES_FROM unset)")
        api("POST", "/api/reset")
        household = api("GET", "/api/household")
        pins = household["demo_pins"]
        assert set(pins) == {"ama", "daniel", "kofi", "mei"}, pins
        names = {m["id"]: m["name"] for m in household["members"]}

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def shot(name: str, full: bool = True) -> None:
                path = OUT / f"{name}.png"
                page.screenshot(path=str(path), full_page=full)
                receipt["screenshots"].append(str(path))

            def no_raw_json() -> None:
                text = page.locator("body").inner_text()
                assert '{"' not in text and '"action_id"' not in text and "model_dump" not in text, "raw JSON leaked onto the screen"

            def identify(member_id: str) -> None:
                page.locator(f'.member-card[data-member-id="{member_id}"]').click()
                page.locator("#pin").fill(pins[member_id])
                page.locator("#pin-submit").click()
                expect(page.locator("#screen-home")).to_be_visible()
                expect(page.locator("#who-line")).to_contain_text(names[member_id])

            def run_sample(fixture_id: str) -> None:
                page.locator(f'.sample[data-fixture-id="{fixture_id}"]').click()
                page.locator("#run-request").click()
                expect(page.locator("#screen-session")).to_be_visible()
                expect(page.locator("#outcome-chip")).to_be_visible(timeout=60000)

            # Members screen and the "What is real" panel driven by /api/meta.rails
            page.goto(URL)
            expect(page.locator(".member-card")).to_have_count(4)
            assert page.locator('.member-card[data-role="minor"]').count() == 2
            for rail_id, rail in rails.items():
                item = page.locator(f'#what-is-real li[data-rail="{rail_id}"]')
                expect(item.locator(".chip")).to_have_text(rail["now"]["mode"])
                expect(item).to_contain_text(rail["name"])
                expect(item).to_contain_text(rail["now"]["reason"])
            expect(page.locator("#wir-env")).to_contain_text(f"Execution mode: {meta['execution_mode']}")
            shot("members")
            receipt["checks"].append("members screen: four cards with adult/minor badges; What is real panel shows every rail's current label from /api/meta")

            # Identify: wrong PIN then right PIN
            page.locator('.member-card[data-member-id="ama"]').click()
            expect(page.locator("#pin-hint")).to_contain_text(pins["ama"])
            page.locator("#pin").fill("0000")
            page.locator("#pin-submit").click()
            expect(page.locator("#pin-error")).to_contain_text("does not match")
            expect(page.locator("#screen-members")).to_be_visible()
            page.locator("#pin").fill(pins["ama"])
            page.locator("#pin-submit").click()
            expect(page.locator("#screen-home")).to_be_visible()
            expect(page.locator("#who-line")).to_contain_text("Ama")
            expect(page.locator("#who-line .badge")).to_have_text("adult")
            receipt["checks"].append("identify: a wrong PIN is refused on the members screen; the right PIN opens home as that member")

            # Ledger: members, grant states, consents, accounts
            expect(page.locator("#ledger-members .ledger-row")).to_have_count(4)
            expect(page.locator('#ledger-grants .ledger-row[data-grant-id="g-expired"] .chip.state-expired')).to_have_text("expired")
            expect(page.locator('#ledger-grants .ledger-row[data-grant-id="g-revoked"] .chip.state-revoked')).to_have_text("revoked")
            expect(page.locator('#ledger-grants .ledger-row[data-grant-id="g-daniel-ama-payments"] .chip.state-active')).to_have_text("active")
            expect(page.locator("#ledger-consents .ledger-row")).to_have_count(len(household["consents"]))
            expect(page.locator('#ledger-accounts .ledger-row[data-account-id="allow-kofi"]')).to_contain_text("42.00 CAD")
            expect(page.locator("#queue")).to_contain_text("Nothing queued")
            shot("home-ama")
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            shot("home-ama-mobile")
            page.set_viewport_size({"width": 1440, "height": 1000})
            receipt["checks"].append("home ledger: members with roles, grants with scope/limit/expiry and active/expired/revoked states, consents, account balances; stacks on mobile")

            # Upload endpoint through the file control (a photo run itself belongs to the vision packet)
            page.locator("#upload-file").set_input_files({"name": "statement.png", "mimeType": "image/png", "buffer": PNG})
            expect(page.locator("#upload-status")).to_contain_text("Uploaded statement.png")
            page.locator("#upload-clear").click()
            expect(page.locator("#upload-status")).to_have_text("")
            receipt["checks"].append(f"upload: a PNG is stored for the session and acknowledged on screen (photo runs available: {meta['upload']['available']})")

            # In-scope run as Ama: six nodes, receipts whose chips match the executor's labels
            run_sample("ama-dental-cob")
            expect(page.locator(".agent.done")).to_have_count(6)
            expect(page.locator("#request-text-view")).to_contain_text("Sun Life")
            expect(page.locator("#briefing-headline")).not_to_be_empty()
            listed = api("GET", "/api/receipts")
            assert len(listed) == 2, listed
            for r in listed:
                expect(page.locator(f'#session-receipts .receipt[data-receipt-id="{r["id"]}"] .chip.mode')).to_have_text(r["mode"])
                expect(page.locator(f'#session-receipts .receipt[data-receipt-id="{r["id"]}"]')).to_contain_text(r["label_reason"])
            expect(page.locator("#mount-session #what-is-real")).to_be_visible()
            no_raw_json()
            shot("session-ama-dental")
            receipt["inScopeReceipts"] = [{"rail": r["rail"], "mode": r["mode"], "label_reason": r["label_reason"]} for r in listed]
            receipt["checks"].append("in-scope run: all six agents complete; two receipts on screen whose chips and reasons equal the executor's labels (" + ", ".join(f"{r['rail']}={r['mode']}" for r in listed) + ")")

            # A minor's request queues an approval; only an adult PIN releases it
            page.locator("#switch-member").click()
            expect(page.locator("#screen-members")).to_be_visible()
            identify("kofi")
            expect(page.locator("#who-line .badge")).to_have_text("minor")
            run_sample("kofi-allowance-40")
            expect(page.locator("#outcome-chip")).to_have_text("waiting for approval")
            expect(page.locator("#agent-executor")).to_have_class("agent skipped")
            form = page.locator("#approvals .approval-form").first
            expect(form).to_be_visible()
            options = form.locator("select option").all_text_contents()
            assert all("(adult)" in o for o in options) and not any(names["kofi"] in o or names["mei"] in o for o in options), options
            assert page.locator('#approvals option[value="kofi"], #approvals option[value="mei"]').count() == 0
            shot("approval-kofi-40")
            form.locator("select").select_option("ama")
            form.locator("input").fill("9999")
            form.locator("button.approve").click()
            expect(form.locator(".error-line")).to_contain_text("does not match")
            assert api("GET", "/api/receipts")[:1] == listed[:1] and len(api("GET", "/api/receipts")) == 2
            form.locator("input").fill(pins["ama"])
            form.locator("button.approve").click()
            decided = page.locator("#approvals .decided")
            expect(decided).to_be_visible(timeout=20000)
            expect(decided.locator(".chip.outcome-allow")).to_have_text("allow")
            expect(decided).to_contain_text("approved by Ama")
            latest = api("GET", "/api/receipts")[0]
            assert latest["action_type"] == "allowance:transfer" and latest["subject_member_id"] == "kofi", latest
            expect(decided.locator(".receipt .chip.mode")).to_have_text(latest["mode"])
            expect(decided.locator(".receipt")).to_contain_text(latest["label_reason"])
            expect(page.locator("#outcome-chip")).to_have_text("done")
            no_raw_json()
            shot("approval-done")
            receipt["approvalReceipt"] = {"rail": latest["rail"], "mode": latest["mode"], "label_reason": latest["label_reason"]}
            receipt["checks"].append(f"approval: a minor's over-limit request waits; only adults are offered as approvers; a wrong PIN is refused; Ama's PIN executes it and the receipt chip equals the executor's label ({latest['rail']}={latest['mode']})")
            page.locator("#back-home").click()
            expect(page.locator("#screen-home")).to_be_visible()
            expect(page.locator(f'#queue .action[data-action-id="{latest["action_id"]}"]')).to_have_attribute("data-status", "executed")
            expect(page.locator('#ledger-consents .ledger-row[data-kind="action-approve"]')).to_have_count(1)
            expect(page.locator("#receipts .receipt")).to_have_count(3)
            if latest["mode"] == "COMPLETE":
                expect(page.locator('#ledger-accounts .ledger-row[data-account-id="allow-kofi"]')).to_contain_text("82.00 CAD")
            shot("home-kofi-after")
            receipt["checks"].append("home after approval: the queue shows the action as done, the consent list gains an action-approve entry, receipts list three")

            # A decline by the other guardian
            page.locator("#switch-member").click()
            identify("mei")
            run_sample("mei-email-teacher")
            form = page.locator("#approvals .approval-form").first
            form.locator("select").select_option("daniel")
            form.locator("input").fill(pins["daniel"])
            form.locator("button.decline").click()
            decided = page.locator("#approvals .decided")
            expect(decided).to_be_visible(timeout=20000)
            expect(decided.locator(".chip.outcome-block")).to_have_text("block")
            expect(decided).to_contain_text("declined by Daniel")
            expect(page.locator("#outcome-chip")).to_have_text("declined")
            assert len(api("GET", "/api/receipts")) == 3
            page.locator("#back-home").click()
            expect(page.locator('#queue .action[data-status="declined"]')).to_have_count(1)
            expect(page.locator('#ledger-consents .ledger-row[data-kind="action-decline"]')).to_have_count(1)
            shot("home-mei-declined")
            receipt["checks"].append("decline: Daniel's PIN declines Mei's email; nothing is sent, the decline is a consent record and the queue shows it as declined")

            # An expired grant: the working ledger is staged (this rig owns it), then the request must wait with the reason on screen
            ledger_path = DATA_DIR / "demo.json"
            if ledger_path.exists():
                data = json.loads(ledger_path.read_text(encoding="utf-8"))
                grant = next(g for g in data["grants"] if g["id"] == "g-daniel-ama-payments")
                grant["expires_at"] = "2026-08-01T00:00:00-04:00"
                ledger_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                page.locator("#switch-member").click()
                identify("ama")
                expect(page.locator('#ledger-grants .ledger-row[data-grant-id="g-daniel-ama-payments"] .chip.state-expired')).to_have_text("expired")
                run_sample("daniel-payment-450")
                expect(page.locator("#outcome-chip")).to_have_text("waiting for approval")
                expect(page.locator("#approvals")).to_contain_text("expired at 2026-08-01")
                expect(page.locator("#approvals .approval-form")).to_have_count(2)
                expect(page.locator('#plan-body .plan-revision[data-revision="2"]')).to_be_visible()
                assert len(api("GET", "/api/receipts")) == 3
                no_raw_json()
                shot("session-expired-grant")
                receipt["checks"].append("expired grant: both parts of the split payment wait for Daniel and the screen names the expired grant as the reason; nothing executed")
            else:
                receipt["checks"].append("expired grant: skipped (no access to the server's ledger file; set HOUSEHOLD_DATA_DIR)")

            # Stale-response guard: an old /api/run response that ignores the abort must not reach a later screen
            page.locator("#back-home").click()
            expect(page.locator("#screen-home")).to_be_visible()
            page.evaluate("""() => {
              window.normalFetch = window.fetch;
              window.fetch = (url, options) => url === '/api/run' ? new Promise(resolve => {
                window.releaseOld = () => resolve(new Response('data: ' + JSON.stringify({event:'node_done', node_id:'intake', run:1, status:'completed', execution_ms:1, output:{document_class:'text-request', issuer:'old', subject_hint:'', amounts:[], dates:[], transcribed_lines:[], summary_en:'__MARKER__', evidence:[], confidence:'high'}}) + '\\n\\n', {status:200, headers:{'content-type':'text/event-stream'}}));
              }) : window.normalFetch(url, options);
            }""".replace("__MARKER__", STALE_MARKER))
            page.locator("#request-text").fill("Please check the mail.")
            page.locator("#run-request").click()
            expect(page.locator("#screen-session")).to_be_visible()
            page.locator("#switch-member").click()
            expect(page.locator("#screen-members")).to_be_visible()
            page.evaluate("window.releaseOld()")
            page.wait_for_timeout(200)
            expect(page.locator("#screen-members")).to_be_visible()
            assert STALE_MARKER not in page.locator("body").text_content()
            page.evaluate("() => { window.fetch = window.normalFetch; }")
            receipt["checks"].append("stale response rejected: a late session response is dropped after switching member")

            assert not errors, errors
            receipt["pageErrors"] = errors
            receipt["checks"].append("zero page errors")
            receipt["finalReceipts"] = api("GET", "/api/receipts")
            receipt["finalActions"] = [{"id": a["id"], "status": a["status"], "action_type": a["action_type"]} for a in api("GET", "/api/household")["actions"]]
            browser.close()
        receipt["status"] = "passed"
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            receipt["ownedServerStopped"] = True
        if log is not None:
            log.close()
        (OUT / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in receipt.items() if k not in {"finalReceipts", "finalActions"}}, indent=2))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
