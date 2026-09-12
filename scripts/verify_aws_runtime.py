"""Bounded fixture proof of a deployed IAM Runtime through an extracted web wheel."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


def guard_invocations(path: Path):
    from household.web import runtime
    original = runtime.aws_client

    def client(target):
        wrapped = original(target)

        class Client:
            def invoke_agent_runtime(self, **kwargs):
                payload = json.loads(kwargs['payload'])
                with path.open('a+') as record:
                    fcntl.flock(record, fcntl.LOCK_EX)
                    record.seek(0)
                    if len(record.readlines()) >= 20:
                        raise RuntimeError('Cloud verification reached its 20-invocation ceiling')
                    record.write(json.dumps({'sessionId': kwargs['runtimeSessionId'], 'operation': payload.get('operation', 'session'),
                                             'fixture': payload.get('fixture_id', 'metadata' if payload.get('operation') else 'synthetic-raw')}) + '\n')
                    record.flush()
                return wrapped.invoke_agent_runtime(**kwargs)

            def close(self):
                wrapped.close()

        return Client()

    runtime.aws_client = client


def main():
    from playwright.sync_api import expect, sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, default=Path('runs/aws-preview/deployment.json'))
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('runs/aws-preview/proof'))
    args = parser.parse_args()
    deployment = json.loads(args.deployment.read_text())
    if deployment['status'] != 'READY' or deployment['runtimeRequest']['environmentVariables']['MODEL_PROVIDER'] != 'fake':
        raise ValueError('Require a READY fixture-only deployment receipt')
    observed = deployment['observedRuntime']
    if (observed['agentRuntimeArtifact']['containerConfiguration']['containerUri'] != deployment['imageUri']
            or observed['environmentVariables'] != deployment['runtimeRequest']['environmentVariables']):
        raise ValueError('Observed Runtime artifact or environment differs from the preview plan')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    attempts = out.parent / 'invocations.jsonl'  # Shared across retries, not reset by a new proof directory.
    installed = out / 'installed'
    installed.mkdir()
    with zipfile.ZipFile(args.wheel) as archive:
        archive.extractall(installed)
    sys.path.insert(0, str(installed))
    from household.web import runtime
    assert str(installed) in runtime.__file__
    guard_invocations(attempts)
    os.environ.update(AWS_PROFILE='hackathon-1', AWS_REGION=deployment['region'], MODEL_PROVIDER='fake', OTEL_SDK_DISABLED='true')
    target = runtime.RuntimeTarget('agentcore', deployment['runtimeArn'], deployment['region'])
    proof = {'status': 'failed', 'runtimeArn': target.endpoint, 'runtimeVersion': deployment['observedRuntime']['agentRuntimeVersion'],
             'imageDigest': deployment['imageDigest'], 'sourceCommit': deployment['sourceCommit'], 'checks': []}

    async def api_checks():
        meta = await runtime.remote_metadata(target)
        assert meta['provider'] == 'fake' and meta['memory_enabled'] is False and meta['max_model_calls'] == 20
        proof['checks'].append('AWS metadata matches fixture mode, Memory off and call allowance')
        for payload, expected in [({'fixture_id': 'ltb-n4', 'language': 'es'}, 'escalate'),
                                  ({'fixture_id': 'school-trip-letter', 'language': 'es'}, 'proceed'),
                                  ({'document_text': 'Library notice: your book is ready for pickup.', 'title': 'Synthetic library notice', 'language': 'es'}, 'proceed')]:
            events = [e async for e in runtime.remote_session(target, payload, meta)]
            assert events[-1]['event'] == 'result', events[-1]
            result = events[-1]['result']
            assert result['outcome'] == expected and result['provider'] == 'fake'
            if payload.get('fixture_id') == 'ltb-n4':
                assert result['guard']['rule_id'] == 'ON-LTB-N4' and 'drafter' not in result['execution_order']
            if payload.get('fixture_id') == 'school-trip-letter':
                assert [v['decision'] for v in result['verdicts']] == ['revise', 'approve']
                assert result['source_form'] and result['drafts'][-1]['preparation_steps']
            name = payload.get('fixture_id', 'synthetic-raw')
            (out / f'{name}.json').write_text(json.dumps(events, indent=2))
            proof['checks'].append('Actual AWS graph: ' + name + ' -> ' + expected)

    server = None
    try:
        asyncio.run(api_checks())
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        bootstrap = out / 'serve.py'
        bootstrap.write_text('import runpy\nfrom pathlib import Path\nimport uvicorn\n'
                             + f"runpy.run_path({str(Path(__file__).resolve())!r})['guard_invocations'](Path({str(attempts)!r}))\n"
                             + f"uvicorn.run('household.web.app:app', host='127.0.0.1', port={port})\n")
        with (out / 'web.log').open('w') as log:
            env = {**os.environ, 'PYTHONPATH': str(installed), 'SESSION_BACKEND': 'agentcore', 'HOUSEHOLD_RUNTIME_ARN': target.endpoint}
            server = subprocess.Popen([sys.executable, str(bootstrap)], cwd=installed, env=env, stdout=log, stderr=subprocess.STDOUT)
            url = f'http://127.0.0.1:{port}'
            deadline = time.monotonic() + 30
            while True:
                try:
                    urllib.request.urlopen(url, timeout=2).close()  # Static route; does not invoke Runtime.
                    break
                except urllib.error.URLError:
                    if time.monotonic() > deadline or server.poll() is not None:
                        raise
                    time.sleep(.25)
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(url)
                expect(page.locator('[data-code="es"]')).to_be_visible(timeout=150000)
                page.locator('[data-code="es"]').click()
                expect(page.locator('#consent-where')).to_contain_text('AWS AgentCore Runtime')
                page.screenshot(path=str(out / 'aws-consent.png'), full_page=True)
                page.locator('#consent-agree').click()
                page.get_by_role('button', name='School field-trip permission letter', exact=False).click()
                expect(page.locator('#approved-reply')).to_be_visible(timeout=150000)
                expect(page.locator('#print-summary')).to_be_enabled()
                page.screenshot(path=str(out / 'aws-routine.png'), full_page=True)
                page.locator('#choose-document').click()
                page.get_by_role('button', name='Ontario LTB Form N4 - Notice to End your Tenancy Early for Non-payment of Rent (filled) high real public form · demo', exact=True).click()
                expect(page.locator('#visitor-card')).to_be_visible(timeout=150000)
                expect(page.locator('#card-rule')).to_contain_text('Form N4')
                expect(page.locator('#approved-reply')).to_be_hidden()
                page.locator('#visitor-card').scroll_into_view_if_needed()
                page.screenshot(path=str(out / 'aws-refusal.png'), full_page=True)
                assert not errors, errors
                proof['pageErrors'] = errors
                proof['checks'].append('Extracted-wheel browser: AWS consent, approved routine takeaway and N4 refusal')
                browser.close()
        proof['status'] = 'passed'
    finally:
        if server:
            server.terminate()
            server.wait(timeout=15)
        calls = [json.loads(line) for line in attempts.read_text().splitlines()] if attempts.exists() else []
        proof['invocationAttempts'] = len(calls)
        proof['distinctSessions'] = len({c['sessionId'] for c in calls})
        proof['ownedWebServerStopped'] = server is None or server.poll() is not None
        (out / 'receipt.json').write_text(json.dumps(proof, indent=2))
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
