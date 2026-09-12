"""Prove the web bridge against an extracted wheel and actual local ARM64 Runtime images.

Creates only owned temporary containers/servers. No cloud resources or model calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.STDOUT).strip()


def ready(url):
    deadline = time.monotonic() + 120
    while True:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if time.monotonic() > deadline:
                raise
            time.sleep(.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--wheel", required=True)
    parser.add_argument("--output", default="/tmp/household-runtime-bridge-proof")
    args = parser.parse_args()
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    installed = out / "installed"
    installed.mkdir(exist_ok=True)
    wheel = Path(args.wheel).resolve()
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(installed)
    image_id = json.loads(docker("image", "inspect", args.image))[0]["Id"]
    containers, servers, logs = [], [], []
    receipt = {"status": "failed", "imageId": image_id, "wheelSha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "cloudDeployment": False, "checks": []}
    env = {**os.environ, "PYTHONPATH": str(installed), "MODEL_PROVIDER": "fake", "OTEL_SDK_DISABLED": "true", "AGENTCORE_MEMORY_ID": ""}
    try:
        runtime_urls = {}
        for limit in [20, 3]:
            name = "household-bridge-proof-" + uuid.uuid4().hex[:12]
            docker("run", "--detach", "--name", name, "--platform", "linux/arm64", "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=64m", "--memory", "2g", "--cpus", "2", "--publish", "127.0.0.1::8080", "--env", "MODEL_PROVIDER=fake", "--env", f"MAX_MODEL_CALLS={limit}", "--env", "OTEL_SDK_DISABLED=true", "--env", "AGENTCORE_MEMORY_ID=", image_id)
            containers.append(name)
            url = "http://" + docker("port", name, "8080/tcp").splitlines()[0]
            ready(url + "/ping")
            runtime_urls[limit] = url

        def serve(backend, limit):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            log = (out / f"web-{backend}-{limit}.log").open("w")
            logs.append(log)
            server_env = {**env, "SESSION_BACKEND": backend, "MAX_MODEL_CALLS": str(limit if backend == "local" else 20), "RUNTIME_HTTP_URL": runtime_urls[limit]}
            proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "household.web.app:app", "--host", "127.0.0.1", "--port", str(port)], cwd=installed, env=server_env, stdout=log, stderr=subprocess.STDOUT)
            servers.append(proc)
            url = f"http://127.0.0.1:{port}"
            meta = ready(url + "/api/meta")
            assert meta["backend"] == backend and meta["max_model_calls"] == limit and meta["provider"] == "fake", meta
            return url

        remote, limited = serve("runtime-http", 20), serve("runtime-http", 3)
        local, local_limited = serve("local", 20), serve("local", 3)
        # Existing regression scripts execute against the built wheel, including normal local consent.
        for script in ["verify_browser.py", "verify_handoff_browser.py", "verify_design_browser.py", "verify_budget_browser.py"]:
            proof = out / script.removesuffix(".py")
            with (out / (script + ".log")).open("w") as log:
                subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=installed, env={**env, "HOUSEHOLD_TEST_URL": local, "HOUSEHOLD_LIMITED_URL": local_limited, "HOUSEHOLD_PROOF_DIR": str(proof)}, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=240)
            receipt["checks"].append(f"built local wheel: {script}")
        # Reuse core source/consent/reset/print checks through actual ARM64 Runtime HTTP.
        with (out / "remote-browser.log").open("w") as log:
            subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_browser.py")], cwd=installed, env={**env, "HOUSEHOLD_TEST_URL": remote, "HOUSEHOLD_PROOF_DIR": str(out / "remote-browser")}, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
        receipt["checks"].append("built wheel to actual ARM64 Runtime: source, consent, refusal, revision, reset and print regression")

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("""window.spoken=[]; Object.defineProperty(window,'speechSynthesis',{value:{cancel(){},getVoices(){return [{lang:'es-ES'}]},speak(u){window.spoken.push(u.text)}}}); window.SpeechSynthesisUtterance=function(text){this.text=text};""")

            def consent(url):
                page.goto(url)
                page.locator('[data-code="es"]').click()
                expect(page.locator("#consent-where")).to_contain_text("letter is sent to the configured Runtime service")
                expect(page.locator("#consent-target")).to_contain_text("El texto se envía al servicio Runtime")
                assert "No network call" not in page.locator("#consent-where").text_content()
                size = "mobile" if page.viewport_size["width"] < 600 else "desktop"
                page.screenshot(path=str(out / f"remote-consent-{size}.png"), full_page=True)
                page.locator("#consent-agree").click()

            consent(limited)
            page.get_by_role("button", name="School field-trip permission letter").click()
            expect(page.locator("#session-error")).to_contain_text("3-call model limit", timeout=45000)
            expect(page.locator("#visitor-text")).to_have_text("La lectura no terminó. Pida ayuda al personal.")
            expect(page.locator("#print-summary")).to_be_disabled()
            assert page.locator("#print-area").text_content() == ""
            expect(page.locator("#draft-card")).to_be_hidden()
            page.locator("#read-aloud").click()
            assert page.evaluate("window.spoken") == ["La lectura no terminó. Pida ayuda al personal."]
            page.screenshot(path=str(out / "remote-budget-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(out / "remote-budget-mobile.png"), full_page=True)
            receipt["checks"].append("actual limited Runtime: remote consent, partial-output clearing, print disabled and help-only speech")
            consent(remote)
            page.get_by_role("button", name="School field-trip permission letter").click()
            expect(page.locator("#approved-reply")).to_be_visible(timeout=45000)
            expect(page.locator("#print-summary")).to_be_enabled()
            page.screenshot(path=str(out / "remote-routine-mobile.png"), full_page=True)
            receipt["checks"].append("fresh consent recovers on normal Runtime and yields approved routine draft")
            page.locator("#choose-document").click()
            # Tamper only the submitted stale snapshot; actual backend rejects it without document invocation.
            def stale(route):
                data = route.request.post_data_json
                data["consent_token"] = "stale"
                route.continue_(post_data=json.dumps(data))
            page.route("**/api/run", stale, times=1)
            page.get_by_role("button", name="School field-trip permission letter").click()
            expect(page.locator("#session-error")).to_contain_text("Reload Front Desk and review consent")
            expect(page.locator("#visitor-card")).to_be_hidden()
            expect(page.locator("#print-summary")).to_be_disabled()
            page.screenshot(path=str(out / "remote-config-changed-mobile.png"), full_page=True)
            receipt["checks"].append("stale consent rejected and explicit reload/review recovery shown")
            # End the owned normal Runtime to prove startup failure does not become local processing.
            docker("stop", containers[0])
            page.goto(remote)
            expect(page.locator("#provider-line")).to_contain_text("reading service is unavailable", timeout=45000)
            expect(page.locator("#startup-error")).to_be_visible()
            expect(page.locator("#startup-error")).to_contain_text("reading service is unavailable")
            expect(page.locator("#reload-service")).to_be_visible()
            assert page.locator("#language-grid button").count() == 0
            page.screenshot(path=str(out / "remote-unavailable-mobile.png"), full_page=True)
            page.locator("#reload-service").click()
            expect(page.locator("#startup-error")).to_be_visible()
            receipt["checks"].append("offline Runtime blocks onboarding with no local fallback")
            assert not errors, errors
            receipt["pageErrors"] = errors
            browser.close()
        receipt["status"] = "passed"
    finally:
        for proc in servers:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for log in logs:
            log.close()
        for name in containers:
            (out / (name + ".log")).write_text(docker("logs", name))
            docker("rm", "--force", name)
        receipt["ownedServicesStopped"] = True
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
