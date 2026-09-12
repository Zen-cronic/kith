"""Create a receipt-owned, scoped-IAM household Runtime preview in explicit steps.

Every resource-creating step (provision, publish, create) is a human gate: run `plan`, show the operator the
receipt (resource names + the exact IAM policy), and only run the later steps once the operator approves. Read-only
calls (sts, describe, IAM simulate) never mutate anything.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.validate import validate_parameters

REPOSITORY = 'bedrock-agentcore-household-preview'
ROLE = 'AmazonBedrockAgentCoreHouseholdPreview'
RUNTIME = 'household_preview'
POLICY = 'HouseholdRuntime'

# The live household models. Nova 2 Lite is the default node model, Nova Pro the intake reader, Nova 2 Sonic the
# voice model. Cross-region inference profiles route to the same foundation models in whichever region serves the
# request, so the underlying foundation-model ARN is named exactly but left region-agnostic (`:*:`); this is a named
# resource, not a `Resource: "*"` grant.
INFERENCE_PROFILES = ('us.amazon.nova-pro-v1:0', 'us.amazon.nova-2-lite-v1:0')
CROSS_REGION_MODELS = ('amazon.nova-pro-v1:0', 'amazon.nova-2-lite-v1:0')
SONIC_MODEL = 'amazon.nova-2-sonic-v1:0'
BEDROCK_ACTIONS = ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream', 'bedrock:InvokeModelWithBidirectionalStream']

ENVIRONMENT = {
    'MODEL_PROVIDER': 'bedrock',
    'BEDROCK_MODEL_ID': 'us.amazon.nova-2-lite-v1:0',
    'NODE_MODELS': 'intake=bedrock:us.amazon.nova-pro-v1:0',
    'SONIC_MODEL_ID': SONIC_MODEL,
    'EXECUTION_MODE': 'live',
    'MAX_MODEL_CALLS': '30',
    'OTEL_SDK_DISABLED': 'true',
}


def bedrock_resources(account: str, region: str) -> list[str]:
    root = f'arn:aws:bedrock:{region}:{account}:inference-profile'
    resources = [f'{root}/{profile}' for profile in INFERENCE_PROFILES]
    resources += [f'arn:aws:bedrock:*::foundation-model/{model}' for model in CROSS_REGION_MODELS]
    resources.append(f'arn:aws:bedrock:{region}::foundation-model/{SONIC_MODEL}')
    return resources


def iam_policy(account: str, region: str, ses_from: str | None) -> dict:
    logs = f'arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/{RUNTIME}-*'
    statements = [
        {'Sid': 'PullImage', 'Effect': 'Allow', 'Action': ['ecr:BatchGetImage', 'ecr:GetDownloadUrlForLayer'],
         'Resource': f'arn:aws:ecr:{region}:{account}:repository/{REPOSITORY}'},
        # ecr:GetAuthorizationToken has no resource-level scoping in AWS; `Resource: "*"` is mandatory for this action
        # alone and is the only unscoped grant in this policy.
        {'Sid': 'EcrAuth', 'Effect': 'Allow', 'Action': 'ecr:GetAuthorizationToken', 'Resource': '*'},
        {'Sid': 'LogGroup', 'Effect': 'Allow', 'Action': ['logs:CreateLogGroup', 'logs:DescribeLogStreams'], 'Resource': logs},
        {'Sid': 'LogStream', 'Effect': 'Allow', 'Action': ['logs:CreateLogStream', 'logs:PutLogEvents'], 'Resource': logs + ':log-stream:*'},
        {'Sid': 'InvokeModels', 'Effect': 'Allow', 'Action': BEDROCK_ACTIONS, 'Resource': bedrock_resources(account, region)},
    ]
    if ses_from:
        statements.append({
            'Sid': 'SendMail', 'Effect': 'Allow', 'Action': ['ses:SendEmail', 'ses:SendRawEmail'],
            'Resource': f'arn:aws:ses:{region}:{account}:identity/{ses_from}',
            'Condition': {'StringEquals': {'ses:FromAddress': ses_from}},
        })
    return {'Version': '2012-10-17', 'Statement': statements}


def plan(account: str, region: str, image: str, commit: str, owner: str, ses_from: str | None = None) -> dict:
    if not re.fullmatch(r'\d{12}', account) or region != 'us-east-1':
        raise ValueError('Expected a 12-digit account and the verified us-east-1 region')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', image) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Use the full verified image ID and source commit')
    root = f'arn:aws:bedrock-agentcore:{region}:{account}'
    tags = {'Project': 'household', 'Purpose': 'runtime-preview', 'DeploymentOwner': owner}
    environment = {**ENVIRONMENT, 'AWS_REGION': region}
    if ses_from:
        environment['SES_FROM'] = ses_from
    return {
        'account': account, 'region': region, 'imageId': image, 'sourceCommit': commit,
        'owner': owner, 'repository': REPOSITORY, 'sesFrom': ses_from, 'tags': tags,
        'iam': {
            'role': ROLE,
            'policyName': POLICY,
            'trust': {'Version': '2012-10-17', 'Statement': [{
                'Effect': 'Allow', 'Principal': {'Service': 'bedrock-agentcore.amazonaws.com'},
                'Action': 'sts:AssumeRole', 'Condition': {
                    'StringEquals': {'aws:SourceAccount': account},
                    'ArnLike': {'aws:SourceArn': f'{root}:runtime/{RUNTIME}-*'},
                },
            }]},
            'policy': iam_policy(account, region, ses_from),
        },
        'runtimeRequest': {
            'agentRuntimeName': RUNTIME, 'roleArn': f'arn:aws:iam::{account}:role/{ROLE}',
            'networkConfiguration': {'networkMode': 'PUBLIC'},
            'protocolConfiguration': {'serverProtocol': 'HTTP'},
            'lifecycleConfiguration': {'idleRuntimeSessionTimeout': 300, 'maxLifetime': 1800},
            'environmentVariables': environment,
            'description': 'Household runtime preview; scoped IAM for Bedrock (text + Nova Sonic voice); no Memory',
        },
    }


def require_owner(tags: dict, state: dict) -> None:
    if any(tags.get(key) != value for key, value in state['tags'].items()):
        raise ValueError('Existing resource is not owned by this receipt; refusing to adopt or change it')


def save(path: Path, state: dict) -> None:
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=2, default=str) + '\n')
    temp.replace(path)


def client(session, name):
    return session.client(name, config=Config(connect_timeout=10, read_timeout=120, retries={'total_max_attempts': 1}))


def docker_host() -> str:
    # A temporary auth config must not silently switch Docker Desktop to /var/run/docker.sock.
    endpoint = json.loads(subprocess.check_output(
        ['docker', 'context', 'inspect', '--format', '{{json .Endpoints.docker}}'], text=True))['Host']
    if not endpoint.startswith('unix://'):
        raise ValueError('This preview publisher requires the verified local Unix-socket Docker context')
    return endpoint


def verified_manifest(ecr, image: dict, expected: str) -> dict:
    manifest = json.loads(image['imageManifest'])
    digest = image['imageId']['imageDigest']
    if 'config' in manifest:
        if expected not in {digest, manifest['config']['digest']}:
            raise ValueError('ECR image does not match the verified local artifact')
        return image
    # Docker Desktop can identify an OCI index containing ARM64 plus provenance attestations.
    if digest != expected:
        raise ValueError('ECR index does not match the verified local artifact')
    candidates = [m for m in manifest.get('manifests', [])
                  if m.get('platform', {}).get('os') == 'linux' and m['platform'].get('architecture') == 'arm64']
    if len(candidates) != 1:
        raise ValueError('Expected exactly one verified ARM64 manifest')
    child = ecr.batch_get_image(repositoryName=REPOSITORY, imageIds=[{'imageDigest': candidates[0]['digest']}])['images'][0]
    if child['imageId']['imageDigest'] != candidates[0]['digest'] or 'config' not in json.loads(child['imageManifest']):
        raise ValueError('ECR ARM64 manifest is incomplete')
    return child


def provision(session, state, path):
    ecr, iam = client(session, 'ecr'), client(session, 'iam')
    tags = [{'Key': k, 'Value': v} for k, v in state['tags'].items()]
    try:
        repo = ecr.describe_repositories(repositoryNames=[REPOSITORY])['repositories'][0]
        require_owner({t['Key']: t['Value'] for t in ecr.list_tags_for_resource(resourceArn=repo['repositoryArn'])['tags']}, state)
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'RepositoryNotFoundException':
            raise
        repo = ecr.create_repository(repositoryName=REPOSITORY, imageTagMutability='IMMUTABLE',
                                     encryptionConfiguration={'encryptionType': 'AES256'}, tags=tags)['repository']
    state['repositoryUri'] = repo['repositoryUri']
    state['repositoryArn'] = repo['repositoryArn']
    save(path, state)
    trust = state['iam']['trust']
    try:
        role = iam.get_role(RoleName=ROLE)['Role']
        require_owner({t['Key']: t['Value'] for t in role.get('Tags', [])}, state)
        if role['AssumeRolePolicyDocument'] != trust:
            raise ValueError('Owned role trust drifted; refusing to replace it')
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'NoSuchEntity':
            raise
        role = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust), Tags=tags)['Role']
    state['roleArn'] = role['Arn']
    save(path, state)
    iam.put_role_policy(RoleName=ROLE, PolicyName=POLICY, PolicyDocument=json.dumps(state['iam']['policy']))
    state['provisioned'] = True
    save(path, state)


def publish(session, state, path):
    if not state.get('provisioned'):
        raise ValueError('Provision owned resources first')
    info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', state['imageId']], text=True))[0]
    if info['Architecture'] != 'arm64' or info['Os'] != 'linux' or info['Id'] != state['imageId']:
        raise ValueError('Expected the exact verified ARM64 image')
    ecr = client(session, 'ecr')
    require_owner({t['Key']: t['Value'] for t in ecr.list_tags_for_resource(resourceArn=state['repositoryArn'])['tags']}, state)
    target = state['repositoryUri'] + ':' + state['sourceCommit'][:7]
    try:
        ecr.describe_images(repositoryName=REPOSITORY, imageIds=[{'imageTag': state['sourceCommit'][:7]}])
    except ClientError as exc:
        if exc.response['Error']['Code'] != 'ImageNotFoundException':
            raise
        auth = ecr.get_authorization_token()['authorizationData'][0]
        username, password = base64.b64decode(auth['authorizationToken']).decode().split(':', 1)
        host = docker_host()
        # ECR credentials exist only in a disposable Docker config, never in receipts or shell arguments.
        with tempfile.TemporaryDirectory(prefix='household-ecr-') as config:
            subprocess.run(['docker', '--config', config, 'login', '--username', username, '--password-stdin', auth['proxyEndpoint']],
                           input=password, text=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            subprocess.run(['docker', 'tag', state['imageId'], target], check=True)
            subprocess.run(['docker', '--config', config, '--host', host, 'push', target], check=True)
    image = ecr.batch_get_image(repositoryName=REPOSITORY, imageIds=[{'imageTag': state['sourceCommit'][:7]}])['images'][0]
    state['publishedDigest'] = image['imageId']['imageDigest']
    image = verified_manifest(ecr, image, state['imageId'])
    state['imageDigest'] = image['imageId']['imageDigest']
    state['imageUri'] = state['repositoryUri'] + '@' + state['imageDigest']
    save(path, state)


def create(session, state, path):
    control = client(session, 'bedrock-agentcore-control')
    if state.get('runtimeId'):
        raise ValueError('Runtime already recorded; use status instead of creating another')
    if not state.get('imageUri'):
        raise ValueError('Publish and verify the image first')
    request = {**state['runtimeRequest'], 'agentRuntimeArtifact': {'containerConfiguration': {'containerUri': state['imageUri']}},
               'tags': state['tags'], 'clientToken': state['owner']}
    validate_parameters(request, control.meta.service_model.operation_model('CreateAgentRuntime').input_shape)
    state['submittedRequest'] = request
    save(path, state)
    result = control.create_agent_runtime(**request)
    state.update(runtimeId=result['agentRuntimeId'], runtimeArn=result['agentRuntimeArn'], status=result['status'])
    save(path, state)


def resolve_image(explicit: str | None) -> str:
    if explicit:
        return explicit
    # The verified local artifact built by check 2; its Id is the digest the plan pins.
    info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', 'household:runtime'], text=True))[0]
    return info['Id']


def resolve_commit(explicit: str | None) -> str:
    return explicit or subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'provision', 'publish', 'create', 'status'])
    parser.add_argument('--state', type=Path, default=Path('runs/aws-preview/deployment.json'))
    parser.add_argument('--profile', default='hackathon-1')
    parser.add_argument('--account')
    parser.add_argument('--image')
    parser.add_argument('--commit')
    parser.add_argument('--ses-from', default=None)
    args = parser.parse_args()
    if args.action == 'plan':
        if args.state.exists():
            raise ValueError('Receipt already exists; preserve it for ownership and recovery')
        session = boto3.Session(profile_name=args.profile, region_name='us-east-1')
        account = args.account or client(session, 'sts').get_caller_identity()['Account']
        args.state.parent.mkdir(parents=True, exist_ok=True)
        state = plan(account, 'us-east-1', resolve_image(args.image), resolve_commit(args.commit), str(uuid.uuid4()),
                     ses_from=args.ses_from)
        save(args.state, state)
    else:
        state = json.loads(args.state.read_text())
        session = boto3.Session(profile_name=args.profile, region_name=state['region'])
        account = args.account or state['account']
        if account != state['account'] or client(session, 'sts').get_caller_identity()['Account'] != account:
            raise ValueError('AWS account does not match the deployment receipt')
        if args.action == 'status':
            response = client(session, 'bedrock-agentcore-control').get_agent_runtime(agentRuntimeId=state['runtimeId'])
            response.pop('ResponseMetadata', None)
            state['observedRuntime'] = response
            state['status'] = response['status']
            save(args.state, state)
            print(json.dumps(response, indent=2, default=str))
        else:
            {'provision': provision, 'publish': publish, 'create': create}[args.action](session, state, args.state)
    print(json.dumps({'action': args.action, 'receipt': str(args.state), 'status': state.get('status', 'prepared')}))


if __name__ == '__main__':
    main()
