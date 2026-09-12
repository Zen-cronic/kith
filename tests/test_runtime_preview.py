"""Deployment boundaries: exact artifacts, a role scoped to the household models, no IAM wildcards, live household
configuration, and the ownership guard that refuses to adopt anyone else's resources."""
import importlib.util
from pathlib import Path

import botocore.session
import pytest
from botocore.validate import validate_parameters

spec = importlib.util.spec_from_file_location('deploy_preview', Path(__file__).resolve().parents[1] / 'scripts/deploy_runtime_preview.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)

ACCOUNT = '123456789012'
IMAGE = 'sha256:' + 'a' * 64
COMMIT = 'b' * 40


def deployment(ses_from: str | None = None):
    return preview.plan(ACCOUNT, 'us-east-1', IMAGE, COMMIT, 'test-owner', ses_from=ses_from)


def actions(statement):
    return statement['Action'] if isinstance(statement['Action'], list) else [statement['Action']]


def resources(statement):
    return statement['Resource'] if isinstance(statement['Resource'], list) else [statement['Resource']]


def test_policy_has_no_iam_wildcards():
    """No action carries a `*`, and the only unscoped `Resource: "*"` is ecr:GetAuthorizationToken (AWS mandates it)."""
    for ses_from in (None, 'household@example.org'):
        for statement in deployment(ses_from)['iam']['policy']['Statement']:
            assert not any('*' in a for a in actions(statement)), statement
            for resource in resources(statement):
                if resource == '*':
                    assert actions(statement) == ['ecr:GetAuthorizationToken'], statement


def test_policy_grants_exactly_the_household_bedrock_and_log_actions():
    policy = deployment()['iam']['policy']
    by_sid = {s['Sid']: s for s in policy['Statement']}
    assert set(by_sid) == {'PullImage', 'EcrAuth', 'LogGroup', 'LogStream', 'InvokeModels'}
    invoke = by_sid['InvokeModels']
    assert actions(invoke) == ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream', 'bedrock:InvokeModelWithBidirectionalStream']
    assert resources(invoke) == [
        'arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.amazon.nova-pro-v1:0',
        'arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.amazon.nova-2-lite-v1:0',
        'arn:aws:bedrock:*::foundation-model/amazon.nova-pro-v1:0',
        'arn:aws:bedrock:*::foundation-model/amazon.nova-2-lite-v1:0',
        'arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-sonic-v1:0',
    ]
    # No SES grant unless a verified sender is configured.
    assert 'SendMail' not in by_sid


def test_ses_grant_is_added_only_with_a_from_address_and_is_scoped_to_it():
    policy = deployment('parent@household.example')['iam']['policy']
    send = next(s for s in policy['Statement'] if s['Sid'] == 'SendMail')
    assert actions(send) == ['ses:SendEmail', 'ses:SendRawEmail']
    assert resources(send) == ['arn:aws:ses:us-east-1:123456789012:identity/parent@household.example']
    assert send['Condition'] == {'StringEquals': {'ses:FromAddress': 'parent@household.example'}}


def test_runtime_request_is_live_household_no_memory_and_validates():
    p = deployment('parent@household.example')
    req = p['runtimeRequest']
    env = req['environmentVariables']
    assert env['MODEL_PROVIDER'] == 'bedrock' and env['EXECUTION_MODE'] == 'live' and env['MAX_MODEL_CALLS'] == '30'
    assert env['SONIC_MODEL_ID'] == 'amazon.nova-2-sonic-v1:0' and env['NODE_MODELS'] == 'intake=bedrock:us.amazon.nova-pro-v1:0'
    assert env['SES_FROM'] == 'parent@household.example'
    assert 'AGENTCORE_MEMORY_ID' not in env and 'AWS_PROFILE' not in env
    assert req['lifecycleConfiguration'] == {'idleRuntimeSessionTimeout': 300, 'maxLifetime': 1800}
    assert req['protocolConfiguration'] == {'serverProtocol': 'HTTP'}
    assert 'authorizerConfiguration' not in req  # IAM default, no public OAuth bypass.
    assert p['iam']['policy']['Statement'][0]['Resource'].endswith('/bedrock-agentcore-household-preview')
    assert p['iam']['trust']['Statement'][0]['Condition']['StringEquals']['aws:SourceAccount'] == ACCOUNT
    req['agentRuntimeArtifact'] = {'containerConfiguration': {'containerUri': '123456789012.dkr.ecr.us-east-1.amazonaws.com/household@' + IMAGE}}
    model = botocore.session.get_session().get_service_model('bedrock-agentcore-control')
    validate_parameters(req, model.operation_model('CreateAgentRuntime').input_shape)


def test_receipt_never_adopts_unowned_or_partially_matching_resources():
    p = deployment()
    for tags in [{}, {'Project': 'household'}, {**p['tags'], 'DeploymentOwner': 'someone-else'}]:
        with pytest.raises(ValueError, match='not owned'):
            preview.require_owner(tags, p)
    preview.require_owner(p['tags'], p)


def test_disposable_login_keeps_the_local_docker_daemon(monkeypatch):
    monkeypatch.setattr(preview.subprocess, 'check_output', lambda *a, **k: '{"Host":"unix:///home/user/.docker/desktop/docker.sock"}')
    assert preview.docker_host() == 'unix:///home/user/.docker/desktop/docker.sock'
    monkeypatch.setattr(preview.subprocess, 'check_output', lambda *a, **k: '{"Host":"tcp://remote:2375"}')
    with pytest.raises(ValueError, match='local Unix-socket'):
        preview.docker_host()


@pytest.mark.parametrize('account,region,image,commit', [
    ('wrong', 'us-east-1', IMAGE, COMMIT),
    (ACCOUNT, 'us-west-2', IMAGE, COMMIT),
    (ACCOUNT, 'us-east-1', 'household:latest', COMMIT),
    (ACCOUNT, 'us-east-1', IMAGE, 'b6566a9'),
])
def test_plan_rejects_ambiguous_account_region_or_artifact(account, region, image, commit):
    with pytest.raises(ValueError):
        preview.plan(account, region, image, commit, 'test-owner')


def test_published_index_selects_only_its_verified_arm64_child():
    import json
    child = {'imageId': {'imageDigest': 'sha256:child'}, 'imageManifest': json.dumps({'config': {'digest': 'sha256:config'}})}

    class ECR:
        def batch_get_image(self, **kwargs):
            assert kwargs['imageIds'] == [{'imageDigest': 'sha256:child'}]
            return {'images': [child]}
    index = {'imageId': {'imageDigest': 'sha256:index'}, 'imageManifest': json.dumps({'manifests': [
        {'digest': 'sha256:child', 'platform': {'os': 'linux', 'architecture': 'arm64'}},
        {'digest': 'sha256:attestation', 'platform': {'os': 'unknown', 'architecture': 'unknown'}},
    ]})}
    assert preview.verified_manifest(ECR(), index, 'sha256:index') == child
    with pytest.raises(ValueError, match='does not match'):
        preview.verified_manifest(ECR(), index, 'sha256:wrong')
    assert preview.verified_manifest(ECR(), child, 'sha256:config') == child
    with pytest.raises(ValueError, match='does not match'):
        preview.verified_manifest(ECR(), child, 'sha256:wrong')


def test_cloud_verifier_refuses_twenty_first_dispatch_across_clients(tmp_path, monkeypatch):
    import json

    from household.web import runtime
    spec = importlib.util.spec_from_file_location('verify_aws', Path(__file__).resolve().parents[1] / 'scripts/verify_aws_runtime.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    requests = []

    class Client:
        def invoke_agent_runtime(self, **kwargs):
            requests.append(kwargs)
            return {'sent': True}

        def close(self):
            pass
    monkeypatch.setattr(runtime, 'aws_client', lambda target: Client())
    log = tmp_path / 'invocations.jsonl'
    log.write_text('\n'.join(json.dumps({'sessionId': str(n)}) for n in range(19)) + '\n')
    verifier.guard_invocations(log)
    first = runtime.aws_client(None)
    payload = {'payload': b'{"operation":"metadata"}', 'runtimeSessionId': 'twentieth'}
    assert first.invoke_agent_runtime(**payload) == {'sent': True}
    second = runtime.aws_client(None)
    with pytest.raises(RuntimeError, match='20-invocation ceiling'):
        second.invoke_agent_runtime(**{**payload, 'runtimeSessionId': 'twenty-first'})
    assert len(requests) == 1
    assert len(log.read_text().splitlines()) == 20
