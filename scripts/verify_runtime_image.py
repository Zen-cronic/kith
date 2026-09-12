"""Verify an actual local ARM64 Runtime image; creates/removes only its own containers.

Uses fake models in simulated execution mode, Memory off and no mounted credentials. Docker Desktop/QEMU execution
is image-contract evidence, not AWS deployment or ARM hardware timing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def docker(*args: str, input_text: str | None = None) -> str:
    return subprocess.check_output(["docker", *args], input=input_text, text=True).strip()


def invoke(url: str, payload: dict) -> list[dict]:
    req = urllib.request.Request(url + "/invocations", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as response:
        assert response.status == 200
        assert response.headers["Content-Type"].startswith("text/event-stream")
        return [json.loads(line[6:]) for line in response.read().decode().splitlines() if line.startswith("data: ")]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="An already-built local ARM64 image tag or ID")
    parser.add_argument("--output", default="/tmp/household-runtime-proof")
    args = parser.parse_args()
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    info = json.loads(docker("image", "inspect", args.image))[0]
    assert info["Architecture"] == "arm64" and info["Os"] == "linux"
    assert info["Config"]["User"] == "household"
    assert info["Config"]["Cmd"] == ["python", "agentcore/app.py"]
    assert "8080/tcp" in info["Config"]["ExposedPorts"]
    # Immutable image ID avoids a tag changing during verification.
    image_id = info["Id"]
    containers: list[str] = []
    checks = []
    results = {}
    started = time.monotonic()
    receipt = {"imageId": image_id, "requestedTag": args.image, "platform": "linux/arm64", "imageBytes": info["Size"], "sourceCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "provider": "fake", "executionMode": "simulated", "cloudDeployment": False}
    try:
        for limit in [30, 3]:
            container = "household-runtime-proof-" + uuid.uuid4().hex[:12]
            docker("run", "--detach", "--name", container, "--platform", "linux/arm64", "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=64m", "--memory", "2g", "--cpus", "2", "--publish", "127.0.0.1::8080", "--env", "MODEL_PROVIDER=fake", "--env", "EXECUTION_MODE=simulated", "--env", f"MAX_MODEL_CALLS={limit}", "--env", "OTEL_SDK_DISABLED=true", "--env", "AGENTCORE_MEMORY_ID=", image_id)
            containers.append(container)
            url = "http://" + docker("port", container, "8080/tcp").splitlines()[0]
            deadline = time.monotonic() + 120
            while True:
                state = json.loads(docker("inspect", "--format", "{{json .State}}", container))
                assert state["Running"], state
                try:
                    with urllib.request.urlopen(url + "/ping", timeout=2) as response:
                        ping = json.load(response)
                        assert response.status == 200 and ping["status"] == "Healthy", ping
                        break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    if time.monotonic() > deadline:
                        raise RuntimeError("Runtime image startup timed out") from None
                    time.sleep(.5)
            checks.append(f"limit{limit}: non-root ARM64 image starts with read-only root and healthy ping")
            if limit == 3:
                events = invoke(url, {"fixture_id": "kofi-allowance-8", "actor_member_id": "kofi", "max_model_calls": 1000})
                assert events[-1]["code"] == "model_call_limit", events[-1]
                assert events[-1]["model_calls"]["limit"] == 3 and events[-1]["model_calls"]["exhausted"] is True, events[-1]
                assert not any(e["event"] == "result" for e in events)
                (out / "limited-events.json").write_text(json.dumps(events, indent=2))
                checks.append("typed call-limit exhaustion with no result; payload cannot raise server limit")
                continue
            meta = invoke(url, {"operation": "metadata"})
            assert len(meta) == 1 and meta[0]["event"] == "runtime_meta", meta
            m = meta[0]["metadata"]
            assert m["provider"] == "fake" and m["memory_enabled"] is False and m["execution_mode"] == "simulated"
            assert m["rails"] and isinstance(m["skills_digest"], str) and len(m["skills_digest"]) == 64
            checks.append("metadata reports fake provider, Memory off, simulated mode, rails and skills digest")
            for fixture, actor, outcome in [("kofi-allowance-8", "kofi", "executed"), ("kofi-allowance-40", "kofi", "needs-approval"), ("daniel-payment-450", "daniel", "partial")]:
                events = invoke(url, {"fixture_id": fixture, "actor_member_id": actor})
                assert events[0]["event"] == "session_start" and events[-1]["event"] == "result", events[-1]
                result = events[-1]["result"]
                assert result["provider"] == "fake" and result["outcome"] == outcome, (fixture, result["outcome"])
                assert result["model_calls"]["attempted"] <= 30 and not result["model_calls"]["exhausted"]
                assert [e["node_id"] for e in events if e["event"] == "node_start"][:4] == ["intake", "matcher", "planner", "authority"]
                receipt_events = [e for e in events if e["event"] == "receipt"]
                if outcome == "needs-approval":
                    assert "executor" not in result["execution_order"] and not receipt_events
                    assert any(e["event"] == "approval_needed" for e in events)
                else:
                    assert "executor" in result["execution_order"]
                    assert receipt_events and receipt_events[0]["receipt"]["mode"] == "SIMULATED"
                (out / (fixture + "-events.json")).write_text(json.dumps(events, indent=2))
                results[fixture] = {"outcome": outcome, "execution_order": result["execution_order"], "model_calls": result["model_calls"]}
                checks.append(f"actual image SSE: {fixture} => {outcome}")
            events = invoke(url, {"request_text": "Can I get help paying the twelve dollar library fine?", "actor_member_id": "kofi"})
            assert events[0]["event"] == "session_start" and events[-1]["event"] == "result"
            assert events[-1]["result"]["provider"] == "fake" and events[-1]["result"]["actor_member_id"] == "kofi"
            (out / "adhoc-request-events.json").write_text(json.dumps(events, indent=2))
            checks.append("ad-hoc typed request accepted by installed image")
            assert invoke(url, {})[-1]["event"] == "error"
            checks.append("missing request returns an explicit error")
            probe = '''import hashlib,json,os,platform,sys
from pathlib import Path
from importlib.metadata import distributions
from household.fixtures import FixtureStore
sys.path.insert(0, '/app/agentcore')
import app as agentcore_app
routes=sorted({getattr(r,'path',None) for r in agentcore_app.app.routes} - {None})
files={str(p.relative_to('/app')):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app').rglob('*') if p.is_file()}
print(json.dumps({'uid':os.getuid(),'architecture':platform.machine(),'python':platform.python_version(),'routes':routes,'files':files,'fixtures':len(FixtureStore().list()),'packages':{d.metadata['Name'].lower().replace('_','-'):d.version for d in distributions()}}))
'''
            installed = json.loads(docker("exec", "-i", container, "python", "-", input_text=probe))
            assert installed["uid"] == 10001 and installed["architecture"] == "aarch64", installed
            assert "/invocations" in installed["routes"] and "/ws" in installed["routes"], installed["routes"]
            expected = set()
            for name in ["pyproject.toml", "poetry.lock", "README.md", "LICENSE", "agentcore/app.py"]:
                expected.add(name)
            for folder in ["household", "fixtures"]:
                for file in (ROOT / folder).rglob('*'):
                    if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc':
                        expected.add(str(file.relative_to(ROOT)))
            assert set(installed["files"]) == expected, set(installed["files"]) ^ expected
            for name, digest in installed["files"].items():
                assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
            lock = tomllib.loads((ROOT / "poetry.lock").read_text())
            for dependency in lock["package"]:
                if "main" in dependency.get("groups", []):
                    package = dependency["name"].lower().replace('_', '-')
                    # Platform-specific packages can be absent; every installed main version must be locked.
                    if package in installed["packages"]:
                        allowed = {p['version'] for p in lock['package'] if p['name'].lower().replace('_','-') == package}
                        assert installed["packages"][package] in allowed, package
            for package in ['household', 'strands-agents', 'strands-agents-tools', 'bedrock-agentcore', 'fastapi', 'pydantic', 'uvicorn', 'websockets', 'awscrt', 'aws-sdk-bedrock-runtime']:
                assert package in installed["packages"], package
            assert installed["fixtures"] == 46
            assert "No broken requirements found" in docker("exec", container, "python", "-m", "pip", "check")
            (out / "installed-image.json").write_text(json.dumps(installed, indent=2))
            checks.append("exact source/static/fixture allowlist present; no history, credentials or harness files")
            checks.append("installed main dependency versions match lock; voice/Bedrock ARM64 wheels and 46 fixtures present")
            print("Normal image flows and package audit passed", flush=True)
        receipt.update(passed=checks, fixtureResults=results, elapsedSeconds=round(time.monotonic()-started, 1))
    finally:
        for name in containers:
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
            (out / (name + ".log")).write_text(logs.stdout + logs.stderr)
            subprocess.run(["docker", "rm", "--force", name], check=True, capture_output=True)
        receipt["temporaryContainersRemoved"] = containers
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
