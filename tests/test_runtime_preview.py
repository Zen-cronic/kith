"""Deployment boundaries: exact artifacts, scoped role, fixture-only configuration."""
import importlib.util
from pathlib import Path

import botocore.session
import pytest
from botocore.validate import validate_parameters

spec = importlib.util.spec_from_file_location('deploy_preview', Path(__file__).resolve().parents[1] / 'scripts/deploy_runtime_preview.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def deployment():
    return preview.plan('123456789012', 'us-east-1', 'sha256:' + 'a' * 64, 'b' * 40, 'test-owner')


def test_fixture_deployment_has_no_model_permissions_or_persistent_memory():
    p = deployment()
    statements = p['policy']['Statement']
    actions = [a for s in statements for a in (s['Action'] if isinstance(s['Action'], list) else [s['Action']])]
    assert not any(a.startswith(('bedrock:', 'bedrock-agentcore:')) for a in actions)
    assert statements[0]['Resource'].endswith('/bedrock-agentcore-household-preview')
    assert p['trust']['Statement'][0]['Condition']['StringEquals']['aws:SourceAccount'] == p['account']
    req = p['runtimeRequest']
    assert req['environmentVariables']['MODEL_PROVIDER'] == 'fake'
    assert 'AGENTCORE_MEMORY_ID' not in req['environmentVariables']
    assert 'AWS_PROFILE' not in req['environmentVariables']
    assert req['lifecycleConfiguration'] == {'idleRuntimeSessionTimeout': 60, 'maxLifetime': 300}
    assert 'authorizerConfiguration' not in req  # IAM default, no public OAuth bypass.
    req['agentRuntimeArtifact'] = {'containerConfiguration': {'containerUri': '123456789012.dkr.ecr.us-east-1.amazonaws.com/household@sha256:' + 'a' * 64}}
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
    ('wrong', 'us-east-1', 'sha256:' + 'a' * 64, 'b' * 40),
    ('123456789012', 'us-west-2', 'sha256:' + 'a' * 64, 'b' * 40),
    ('123456789012', 'us-east-1', 'household:latest', 'b' * 40),
    ('123456789012', 'us-east-1', 'sha256:' + 'a' * 64, 'b6566a9'),
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
