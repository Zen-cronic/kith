# Household on Amazon Bedrock AgentCore Runtime

Updated September 12, 2026. **The household container is built and verified locally as an ARM64 image; the AWS
Runtime is planned but not yet created — it is paused at the human approval gate (H6).** The image serves the
streaming `/invocations` text path and the `/ws` voice path. The web tier relays the browser voice socket to the
Runtime over a SigV4 `wss://` URL, so no AWS credential ever reaches the browser. No AWS resource has been created,
modified or deleted by this packet; `scripts/deploy_runtime_preview.py plan` only reads `sts get-caller-identity`
and writes the local receipt.

## Models

`AWS_PROFILE=hackathon-1`, `AWS_REGION=us-east-1`. The preview runs live Bedrock with:

- `us.amazon.nova-2-lite-v1:0` — default node model (`BEDROCK_MODEL_ID`).
- `us.amazon.nova-pro-v1:0` — the intake reader (`NODE_MODELS=intake=bedrock:us.amazon.nova-pro-v1:0`).
- `amazon.nova-2-sonic-v1:0` — the Nova 2 Sonic voice model (`SONIC_MODEL_ID`).

`bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream` is confirmed allowed for the deploy user
(`hackathon-deploy`) by IAM simulate, so the web relay's signed `wss://` call is permitted. Live-model output quality
is a separate review; this packet proves the container and deployment shape, not visitor-ready model usefulness.

## 1. Reproduce locally (no AWS)

```bash
cd ~/code/hackathons/agentsforhumans-2026/placeholder-2-s1
export VIRTUAL_ENV="$HOME/.pyenv/versions/.agentsforhumans-home"
export PATH="$VIRTUAL_ENV/bin:$PATH"
MODEL_PROVIDER=fake OTEL_SDK_DISABLED=true python -m pytest tests/test_agentcore_app.py
MODEL_PROVIDER=fake OTEL_SDK_DISABLED=true python agentcore/app.py    # POST http://localhost:8080/invocations
```

```bash
curl --fail http://localhost:8080/ping
curl --fail -N http://localhost:8080/invocations -H 'content-type: application/json' \
  -d '{"fixture_id":"kofi-allowance-8","actor_member_id":"kofi"}'
```

Expect SSE `data:` records ending in a result with `outcome=executed` and the roster
`intake → matcher → planner → authority → executor → briefer`. `kofi-allowance-40` ends `needs-approval` with no
executor; `daniel-payment-450` runs the revise loop and ends `partial`. Fake mode is deterministic and makes no model
calls.

## 2. Build and verify the complete ARM64 image

AWS requires an ARM64 image listening on `0.0.0.0:8080` with `/ping`, `/invocations` and `/ws`. The
[Dockerfile](../agentcore/Dockerfile) installs the root `poetry.lock` with Poetry 2.2.1 on the digest-pinned
`python:3.12-slim` base, runs as UID 10001 (`household`), and carries no AWS credentials or profile mount. The live
defaults are `MODEL_PROVIDER=bedrock`, `EXECUTION_MODE=live`, `MAX_MODEL_CALLS=30`.

```bash
docker buildx build --platform linux/arm64 -f agentcore/Dockerfile -t household:runtime --load .
python scripts/verify_runtime_image.py --image household:runtime
```

The verifier runs the image with `MODEL_PROVIDER=fake EXECUTION_MODE=simulated` on random loopback ports with a
read-only root, checks the healthy ping, the three household fixtures, the metadata handshake (rails + skills digest),
call-limit exhaustion, the `/invocations` and `/ws` routes, the exact source/fixture allowlist, and that the
voice/Bedrock ARM64 wheels (`awscrt`, `aws-sdk-bedrock-runtime`) and 46 fixtures are installed. It removes only its own
containers. Receipts land in `/tmp/household-runtime-proof` (`--output` to change).

## 3. Plan the deployment (human gate H6)

```bash
AWS_PROFILE=hackathon-1 python scripts/deploy_runtime_preview.py plan
cat runs/aws-preview/deployment.json | python -c "import json,sys;print(json.load(sys.stdin)['iam']['policy'])"
```

`plan` derives the account from `sts get-caller-identity`, pins the locally built image id and the source commit, and
writes `runs/aws-preview/deployment.json`. **Stop here and get operator approval** before any resource-creating step.
The plan creates, on approval:

- ECR repository `bedrock-agentcore-household-preview` (IMMUTABLE tags, AES256).
- IAM role `AmazonBedrockAgentCoreHouseholdPreview` trusting `bedrock-agentcore.amazonaws.com` (scoped by
  `aws:SourceAccount` and the runtime ARN), with inline policy `HouseholdRuntime`: ECR pull, scoped CloudWatch Logs,
  and `bedrock:InvokeModel*` on exactly the two inference profiles and their foundation models plus Nova 2 Sonic. SES
  send is added only when `--ses-from` is set, scoped to that identity with a `ses:FromAddress` condition. The only
  `Resource:"*"` is `ecr:GetAuthorizationToken`, which AWS does not scope; there are no action wildcards.
- AgentCore Runtime `household_preview` (PUBLIC network, HTTP protocol, IAM auth — no OAuth bypass), lifecycle
  `idleRuntimeSessionTimeout=300`, `maxLifetime=1800`, no Memory.

## 4. After approval

```bash
AWS_PROFILE=hackathon-1 python scripts/deploy_runtime_preview.py provision
AWS_PROFILE=hackathon-1 python scripts/deploy_runtime_preview.py publish   # pushes the verified ARM64 digest
AWS_PROFILE=hackathon-1 python scripts/deploy_runtime_preview.py create
AWS_PROFILE=hackathon-1 python scripts/deploy_runtime_preview.py status    # until READY
poetry build -f wheel && AWS_PROFILE=hackathon-1 python scripts/verify_aws_runtime.py --wheel dist/*.whl   # <= 20 live invocations
SESSION_BACKEND=agentcore python scripts/verify_voice.py --backend agentcore --url ws://127.0.0.1:8020/api/voice
```

The web tier reaches the Runtime by setting `SESSION_BACKEND=agentcore` and `HOUSEHOLD_RUNTIME_ARN`; the browser never
holds a credential. Teardown is in [runs/aws-preview/CLEANUP.md](../runs/aws-preview/CLEANUP.md). The paused Front Desk
runtime `front_desk_preview-tSouzTHLE8` is unrelated and must not be touched.
