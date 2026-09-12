"""Bounded live proof of the deployed household Runtime through an extracted web wheel.

Runs the credential-free relay path (`household.web.runtime`) against the real Runtime: it reads runtime metadata,
then streams a few live household sessions over the SigV4 AgentCore transport. Every dispatch is counted and the run
refuses the 21st invocation, so a live Bedrock check can never spend more than 20 model sessions. This is an API-level
proof; the browser end-to-end is covered separately by `scripts/verify_browser.py` against a local server.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import sys
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
                                             'fixture': payload.get('fixture_id', 'metadata' if payload.get('operation') else 'request')}) + '\n')
                    record.flush()
                return wrapped.invoke_agent_runtime(**kwargs)

            def close(self):
                wrapped.close()

        return Client()

    runtime.aws_client = client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, default=Path('runs/aws-preview/deployment.json'))
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('runs/aws-preview/proof'))
    args = parser.parse_args()
    deployment = json.loads(args.deployment.read_text())
    env = deployment['runtimeRequest']['environmentVariables']
    if deployment['status'] != 'READY' or env['MODEL_PROVIDER'] != 'bedrock':
        raise ValueError('Require a READY live household deployment receipt')
    observed = deployment['observedRuntime']
    if (observed['agentRuntimeArtifact']['containerConfiguration']['containerUri'] != deployment['imageUri']
            or observed['environmentVariables'] != env):
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
    os.environ.update(AWS_PROFILE='hackathon-1', AWS_REGION=deployment['region'], OTEL_SDK_DISABLED='true')
    target = runtime.RuntimeTarget('agentcore', deployment['runtimeArn'], deployment['region'])
    proof = {'status': 'failed', 'runtimeArn': target.endpoint, 'runtimeVersion': observed['agentRuntimeVersion'],
             'imageDigest': deployment['imageDigest'], 'sourceCommit': deployment['sourceCommit'], 'checks': []}

    async def api_checks():
        meta = await runtime.remote_metadata(target)
        assert meta['provider'] == 'bedrock' and meta['memory_enabled'] is False and meta['max_model_calls'] == 30
        proof['checks'].append('AWS metadata matches live Bedrock mode, Memory off and call allowance')
        for payload in [{'fixture_id': 'kofi-allowance-8', 'request_text': 'allowance', 'actor_member_id': 'kofi', 'language': 'en'},
                        {'fixture_id': 'kofi-allowance-40', 'request_text': 'allowance', 'actor_member_id': 'kofi', 'language': 'en'}]:
            events = [e async for e in runtime.remote_session(target, payload, meta)]
            terminal = events[-1]
            assert terminal['event'] in {'result', 'error'}, terminal
            if terminal['event'] == 'result':
                assert terminal['result']['provider'] == 'bedrock'
                assert terminal['result']['model_calls']['attempted'] <= meta['max_model_calls']
            (out / (payload['fixture_id'] + '.json')).write_text(json.dumps(events, indent=2))
            proof['checks'].append('Live AWS household session: ' + payload['fixture_id'] + ' -> ' + terminal['event'])

    try:
        asyncio.run(api_checks())
        proof['status'] = 'passed'
    finally:
        calls = [json.loads(line) for line in attempts.read_text().splitlines()] if attempts.exists() else []
        proof['invocationAttempts'] = len(calls)
        proof['distinctSessions'] = len({c['sessionId'] for c in calls})
        (out / 'receipt.json').write_text(json.dumps(proof, indent=2))
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
