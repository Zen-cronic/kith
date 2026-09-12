# Receipts

Every claim in the [README](../../README.md) and [ARCHITECTURE](../ARCHITECTURE.md) traces to a file here. These are sanitized, committed run artifacts: live model traces from Amazon Bedrock, a live voice turn, and the AgentCore deploy plan. Account ids and other sensitive values are masked.

Unless noted, the live traces ran with `MODEL_PROVIDER=bedrock`, `BEDROCK_MODEL_ID=us.amazon.nova-2-lite-v1:0`, `NODE_MODELS=intake=bedrock:us.amazon.nova-pro-v1:0`, `EXECUTION_MODE=simulated`, `AWS_REGION=us-east-1` on 2026-09-12 — so a real Bedrock model chose the actions while nothing left the machine.

## Live model traces

| File | What it proves | Command |
|---|---|---|
| [`live-text-2026-09-12.json`](live-text-2026-09-12.json) | An in-scope text route end-to-end on live Bedrock | `household run --request fixtures/requests/ama-dental-cob.json --actor ama --trace --json` |
| [`live-intake-2026-09-12.json`](live-intake-2026-09-12.json) | Nova Pro reading a document photo live (intake node) | intake trace, `MODEL_PROVIDER=bedrock` |
| [`live-photo-tuition-invoice-2026-09-12.json`](live-photo-tuition-invoice-2026-09-12.json) | Photo → pay-tuition route on live Bedrock | `household run --image fixtures/images/tuition-invoice.png --actor ama --trace --json` |
| [`live-photo-allowance-note-2026-09-12.json`](live-photo-allowance-note-2026-09-12.json) | Photo → allowance route on live Bedrock | `household run --image fixtures/images/allowance-note.png --actor ama --trace --json` |
| [`live-photo-recall-notice-with-injection-2026-09-12.json`](live-photo-recall-notice-with-injection-2026-09-12.json) | A hidden document instruction ignored on live Bedrock | `household run --image fixtures/images/recall-notice-with-injection.png --actor ama --trace --json` |
| [`live-revise-2026-09-12.json`](live-revise-2026-09-12.json) | The authority → planner revise loop on live Bedrock | `household run --request fixtures/requests/daniel-payment-450.json --actor ama --trace --json` |
| [`voice-2026-09-12.json`](voice-2026-09-12.json) | A live Nova 2 Sonic voice turn over the `/api/voice` socket | `python scripts/verify_voice.py` |

## Before/after pairs

Two routes carry a `.before-fix.json` counterpart, kept on purpose so the earlier live run can be read beside the fixed one:

- [`live-revise-2026-09-12.before-fix.json`](live-revise-2026-09-12.before-fix.json)
- [`live-photo-recall-notice-with-injection-2026-09-12.before-fix.json`](live-photo-recall-notice-with-injection-2026-09-12.before-fix.json)

## AgentCore deploy

| File | What it proves | Command |
|---|---|---|
| [`agentcore-2026-09-12.json`](agentcore-2026-09-12.json) | The scoped IAM role/policy (no action wildcards), the runtime config, and the ARM64 image verifier checks. The deploy is a **plan paused at a human approval gate (H6)**; the receipt states no AWS resource was created, modified, or deleted, and records read-only checks only (`sts get-caller-identity`, IAM simulate). | `AWS_PROFILE=hackathon-1 python scripts/deploy_runtime_preview.py plan` |
