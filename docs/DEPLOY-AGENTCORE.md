# Front Desk on Amazon Bedrock AgentCore Runtime

Updated September 11, 2026. **A fixture-only AgentCore Runtime is deployed and verified in AWS; public website hosting remains unfinished.** The verified `b6566a9` ARM64 application image serves real IAM invocations in `us-east-1`. An extracted-wheel website completed routine assistance and N4 refusal through that Runtime. This proves cloud execution and integration with the fixture provider; live-model usefulness remains under review. Use the [AWS setup checklist](../../submission/household/aws-setup-checklist.md) for account/model setup and event deadlines.

## Verified model and remaining quality failures

`AWS_PROFILE=hackathon-1`, `AWS_REGION=us-east-1`, `BEDROCK_MODEL_ID=us.amazon.nova-pro-v1:0` is accessible. Two N4 runs with the source-checked policy retained the notice/order and need-not-leave qualifications; date-format warnings remain. Earlier routine Pro output repeated the letter or summary.

A September 10 Nova 2 Lite probe completed the graph in eight requests after correcting a Strands structured-output schema mismatch. Defaulted lists must be requested explicitly so the model is not offered `null` that Pydantic rejects. The form draft left personal fields blank, but quality review still failed: it translated the cheque payee name, reformatted source dates, and the final card promised a completed form from staff. Explicit model approval and a 0.917 lexical fidelity score did not catch those defects. The probe is execution evidence, not a visitor-ready model recommendation. No default model or local preview provider was changed.

Use `us.amazon.nova-2-lite-v1:0` only for further bounded evaluation. AWS lists [Nova 2 Lite](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-2-lite.html) as Active. The account rejected `us.amazon.nova-premier-v1:0` as Legacy; [Premier's model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-premier.html) lists September 14, 2026 end of life. Do not choose Premier for this deployment. Claude Sonnet 4.5 separately requires the account's Anthropic first-time-use details. No full live evaluation has been completed.

## 1. Reproduce locally

Run from the product root, not `agentcore/`:

```bash
cd ~/code/hackathons/agentsforhumans-2026/household
export VIRTUAL_ENV="$HOME/.pyenv/versions/.agentsforhumans"
export PATH="$VIRTUAL_ENV/bin:$PATH"
poetry install
MODEL_PROVIDER=fake OTEL_SDK_DISABLED=true poetry run pytest tests/test_agentcore_app.py
MODEL_PROVIDER=fake OTEL_SDK_DISABLED=true poetry run python agentcore/app.py
```

In another terminal:

```bash
curl --fail http://localhost:8080/ping
curl --fail -N http://localhost:8080/invocations \
  -H 'content-type: application/json' \
  -d '{"fixture_id":"ltb-n4","language":"es"}'
```

Expect SSE data records ending with a result: `outcome=escalate`, `guard.rule_id=ON-LTB-N4`, and reader → interpreter → critic → router. The school-trip-letter fixture should include a drafter and final critic approval. This fake mode is deterministic fixture replay and makes no model calls.

## 2. Build and verify the complete ARM64 image

AWS requires an ARM64 image listening on `0.0.0.0:8080`, with `/ping` and `/invocations`. The maintained [Dockerfile](../agentcore/Dockerfile) installs the root `poetry.lock` with Poetry2.2.1 using the official Python3.12 base pinned by digest. The pinned image currently supplies Python3.12.14; the local development environment remains Python3.12.12. Both satisfy the project’s Python requirement.

The [Dockerfile context allowlist](../agentcore/Dockerfile.dockerignore) includes product source, static assets, fixtures, lockfiles, license and the Runtime entrypoint. It excludes `.env`, Git history, private harnesses and Python caches. `agentcore/requirements.txt` is a legacy toolkit input; this image uses the root lockfile. The process runs as UID10001 and defaults to the fixture provider with `MAX_MODEL_CALLS=20`. It contains no AWS credentials or local profile mount.

From the product root:

```bash
docker buildx build --platform linux/arm64 -f agentcore/Dockerfile \
  -t household:runtime --load .
poetry run python scripts/verify_runtime_image.py --image household:runtime
```

The verifier creates temporary containers on random loopback ports, using an immutable image ID, read-only root filesystems and a `/tmp` scratch volume. It inspects real SSE outputs for N4 refusal, school revision/approval, degraded translation, raw synthetic text and typed model-call exhaustion. It checks installed package versions against the lock, exact source/asset/fixture contents and the non-root ARM64 process, then removes only its own containers. Receipts are written to `/tmp/household-runtime-proof` by default; use `--output` for another location.

Manual local preview of the Runtime service (separate from the web UI):

```bash
docker run --rm --name household-runtime-check --platform linux/arm64 \
  -p 127.0.0.1:8080:8080 -e MODEL_PROVIDER=fake \
  -e MAX_MODEL_CALLS=20 -e OTEL_SDK_DISABLED=true household:runtime
```

Repeat the `/ping` and `/invocations` requests in step1. Local execution under Docker emulation verifies the image contract; it does not establish AWS deployment or native-ARM performance. The subsequent `b6566a9` image passed all 10 local image checks, then the separate AWS proof in section 7. Its local OCI index ID is `sha256:a0ecc1921e0f9196f849d9e9a1b1f967b053f46ba8c134eaf9864bc209accdd1`; its ARM64 manifest is `sha256:456c1b24bd70b1a011ae642fad3d5d8d51a6ee349a55aa17783b9f6adc0c9a2a`. Verification used a read-only root filesystem and removed its temporary containers. [AWS container deployment](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/getting-started-custom.html).

## 3. AWS resources and execution role

After local image proof, prepare a project-specific ECR repository and execution role. The role must trust AgentCore Runtime and allow pulling this image, logging, and invoking the chosen Bedrock inference profile and its destination models. Deployment credentials also need permission to pass that role. Use [AWS Runtime permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html); inspect the actual role and service quotas before provisioning. Never reuse or delete another project's resources by assumption.

Push the tested image with a commit-specific tag, record its digest, then prepare this Python SDK request with real project-owned identifiers. This is a resource-creating operation, not a diagnostic:

```python
import os
import boto3
control = boto3.client("bedrock-agentcore-control", region_name="us-east-1")
response = control.create_agent_runtime(
    agentRuntimeName="household",
    agentRuntimeArtifact={"containerConfiguration": {"containerUri": os.environ["HOUSEHOLD_IMAGE_URI"]}},
    roleArn=os.environ["HOUSEHOLD_EXECUTION_ROLE_ARN"],
    networkConfiguration={"networkMode": "PUBLIC"},
    environmentVariables={
        "MODEL_PROVIDER": "bedrock",
        "AWS_REGION": "us-east-1",
        "BEDROCK_MODEL_ID": "us.amazon.nova-pro-v1:0",
        "MAX_MODEL_CALLS": "20",
    },
)
print(response["agentRuntimeArn"], response["status"])
```

Do not inject a local `AWS_PROFILE` or static AWS keys into Runtime. Record the returned ID/ARN privately and poll `get_agent_runtime(agentRuntimeId=...)` to READY. A PUBLIC network mode is not proof of anonymous browser access.

## 4. Verify the deployed artifact

Once READY, invoke from an authorized server-side SDK client. Use a fresh UUID (36 characters) for each visit:

```python
import json, os, uuid
import boto3
client = boto3.client("bedrock-agentcore", region_name="us-east-1")
response = client.invoke_agent_runtime(
    agentRuntimeArn=os.environ["HOUSEHOLD_RUNTIME_ARN"],
    runtimeSessionId=str(uuid.uuid4()),
    qualifier="DEFAULT",
    payload=json.dumps({"fixture_id": "ltb-n4", "language": "es"}).encode(),
)
for line in response["response"].iter_lines():
    print(line.decode())
```

Inspect the final SSE result, not just HTTP success. Repeat with school-trip-letter; check draft usefulness, critic approval, dates, amounts and N4 wording. Record commit, image digest, region, model, runtime ID, errors and usage. Configure the website transport below and verify it against the actual AWS endpoint; an SDK invocation alone does not deploy the website. Keep a shared demo limited to synthetic fixtures and bound paid requests.

## 5. Memory, observability and cleanup

Leave `AGENTCORE_MEMORY_ID` unset. The adapter can attach a Memory session manager, but persistence, retrieval, actor isolation and resumption have not been demonstrated. The default actor name is shared; it is not a reviewed multi-visitor identity scheme. Memory-off does not mean data never leaves the machine: Bedrock receives model inputs, and service logs/traces have separate behavior.

Before enabling retention, implement scoped actors/sessions, inspect actual stored events, test deletion and update consent to match observed behavior. Avoid raw visitor documents in logs. Track usage and current [AWS pricing](https://aws.amazon.com/bedrock-agentcore/pricing/); credits are not a guarantee of zero charges. No dollar cost was reconciled with billing for the bounded probes.

For cleanup, inventory the specific Runtime, active sessions, ECR image/repository, role, log groups and any Memory resource created for this project. Stop sessions and remove only those resources after the required judging availability period. Preserve shared credentials and infrastructure. No teardown commands have been run.


## 6. Connect the website to Runtime

Keep the web server's AWS credentials and Runtime ARN in its server environment. The browser only calls `/api/meta` and `/api/run` on the website. `SESSION_BACKEND=local` retains the in-process graph; explicit remote modes never fall back to it.

For a locally running Runtime container on port8080:

```bash
SESSION_BACKEND=runtime-http RUNTIME_HTTP_URL=http://127.0.0.1:8080 \
  MODEL_PROVIDER=fake poetry run uvicorn household.web.app:app --host 127.0.0.1 --port 8011
```

For an IAM-authenticated AWS Runtime, after its deployment and permissions are verified:

```bash
SESSION_BACKEND=agentcore AWS_PROFILE=hackathon-1 AWS_REGION=us-east-1 \
  HOUSEHOLD_RUNTIME_ARN="$HOUSEHOLD_RUNTIME_ARN" HOUSEHOLD_RUNTIME_QUALIFIER=DEFAULT \
  poetry run uvicorn household.web.app:app --host 127.0.0.1 --port 8011
```

The invoking identity needs `bedrock-agentcore:InvokeAgentRuntime` on the selected Runtime. This is the IAM SDK path, not OAuth. The HTTP transport is for a trusted server-configured local/service endpoint; it disables redirects and accepts no target URL from a browser request. The model provider and allowance come from Runtime metadata, regardless of the website's local model settings. The fixture catalog must match.

Before a remote document is sent, the website refreshes Runtime metadata and checks the consent snapshot. Runtime checks it again before graph or Memory work. Changing provider, model, allowance, graph thresholds, region, fixture catalog, or website target requires reloading and reviewing consent. The snapshot is a configuration comparison, not authentication or a stored consent record. `AGENTCORE_MEMORY_ID` must be unset: web connections reject Memory-enabled configurations until isolation and retention are verified.

Each AWS invocation gets a fresh UUID session; SDK invocation retries are disabled to avoid accidentally starting another graph. Metadata calls do not invoke a model, but a deployed Runtime may still bill for executing them. Responses are streamed through the website; a final result is released only after a clean upstream end. Broken streams clear partial visitor/draft/print output. Leaving the page closes the website's upstream connection; already-dispatched Runtime/model work may continue within its per-run allowance. No token/dollar limit, authentication, or cross-request rate limiter is implied.

Reproduce local integration with the built wheel and actual ARM64 image:

```bash
poetry build -f wheel
poetry run python scripts/verify_runtime_bridge.py --image household:runtime \
  --wheel dist/household-0.1.0-py3-none-any.whl
```

The verifier starts owned temporary web servers and two Runtime containers (20 and3 calls), checks local regressions plus remote consent/refusal/routine/limit/recovery and service failure, then removes its services. It does not touch the user preview or create cloud resources. Actual AWS invocation is separately verified below; public web hosting, application access controls and hosted availability remain deployment work.

## 7. Reproduce the fixture-only AWS preview

[`scripts/deploy_runtime_preview.py`](../scripts/deploy_runtime_preview.py) records ownership before creating resources, refuses resources with different ownership tags, and checks the AWS account on each operation. It creates an immutable-tag ECR repository and a Runtime execution role scoped to that repository and the preview's logs. The role has no Bedrock model or Memory permissions. Runtime uses IAM authorization, the fixture provider, a 60-second idle timeout and a 300-second session lifetime. These timeouts bound a session, not total account spending.

Use the full image ID and source commit from a successful local image receipt. Keep `runs/aws-preview/deployment.json` private and preserve it across retries; it contains the exact resource identifiers and ownership tag. For the existing preview, begin with `status` rather than creating another plan:

```bash
python scripts/deploy_runtime_preview.py plan --account "$HOUSEHOLD_AWS_ACCOUNT" \
  --image "$HOUSEHOLD_VERIFIED_IMAGE_ID" --commit "$HOUSEHOLD_VERIFIED_COMMIT"
python scripts/deploy_runtime_preview.py provision --account "$HOUSEHOLD_AWS_ACCOUNT"
python scripts/deploy_runtime_preview.py publish --account "$HOUSEHOLD_AWS_ACCOUNT"
python scripts/deploy_runtime_preview.py create --account "$HOUSEHOLD_AWS_ACCOUNT"
python scripts/deploy_runtime_preview.py status --account "$HOUSEHOLD_AWS_ACCOUNT"
```

`provision`, `publish` and `create` change AWS resources. Publishing uses a disposable Docker authentication config while retaining the local Docker Desktop socket. When Docker publishes an OCI index with provenance attestations, the helper validates that index against the verified local ID and selects its single ARM64 child manifest. Runtime is pinned to that manifest digest, not a mutable tag. The default AWS profile is `hackathon-1`; use `--profile` to select another explicitly.

After `status` reports READY, run the bounded cloud proof:

```bash
poetry run python scripts/verify_aws_runtime.py \
  --wheel dist/household-0.1.0-py3-none-any.whl
```

The verifier checks metadata, N4 refusal, school-letter revision/approval and raw synthetic text, then starts a temporary extracted-wheel website and exercises its IAM connection in Chromium. Install the optional Poetry browser group first. A shared `runs/aws-preview/invocations.jsonl` caps verification at 20 dispatch attempts across retries; a retry needs a fresh `--output` directory under the same parent. This limit belongs to the verifier, not the general website.

September 11 evidence: Runtime version 1 READY, exact ARM64 digest above, five API/browser check groups passed, zero browser errors, 16 invocation attempts with 16 distinct session IDs across two proof attempts. The first browser attempt stopped on an ambiguous test selector; the application had already completed its routine flow. No live model calls occurred. AWS compute, ECR and log charges have not been reconciled with billing. [Runtime pricing](https://aws.amazon.com/bedrock/agentcore/pricing/) is usage-based, and idle/lifetime settings do not cap future invocations.

Sampled Runtime logs contain streaming-response messages and repeated `Invalid HTTP request received` warnings. Their cause is unconfirmed; all captured graph invocations completed. Do not infer complete logging privacy from this sample. The private deployment receipt and log inventory identify the owned Runtime, generated workload identity, ECR repository, execution role and log group for later cleanup. Stop using the Runtime, delete it and wait for deletion before removing its image repository or role. Inspect the generated workload identity after deletion; preserve the shared default identity directory. Remove only log groups and resources matching the receipt's ownership and exact identifiers. No cleanup of the deployed preview has been performed.
