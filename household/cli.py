"""household: run a request through the six-agent graph, list fixtures and providers, print the graph."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .agents.graph import describe_graph
from .agents.roster import ROSTER
from .config import SELECTABLE, load_settings, provider_readiness
from .fixtures import FixtureStore
from .pipeline import SessionResult, stream_session, upload_request
from .skills import ALL_SKILLS


def _wrap(text: str, indent: str = "    ") -> str:
    return textwrap.fill(text, width=100, initial_indent=indent, subsequent_indent=indent)


def render(result: SessionResult) -> str:
    out: list[str] = []
    out.append(f"Household  |  provider: {result.provider} ({result.model_id})  |  execution: {result.execution_mode}  |  {result.elapsed_ms} ms")
    out.append(f"Request {result.request_id} from {result.actor_name} ({result.actor_member_id}, {result.language}):")
    out.append(_wrap(result.request_text))
    if result.upload_name:
        out.append(f"Attachment: {result.upload_name} (only the intake reader saw the file; every other agent read the transcription)")
    trace = " -> ".join(f"{s.node_id}{'#' + str(s.run) if s.run > 1 else ''}" for s in result.roster)
    skipped = [spec.id for spec in ROSTER if spec.id not in result.execution_order]
    out.append(f"Roster trace: {trace}" + (f"   (skipped: {', '.join(skipped)})" if skipped else ""))
    if result.reading:
        r = result.reading
        out.append(f"\n[intake] {r.document_class} from {r.issuer} (confidence {r.confidence})")
        out.append(_wrap(r.summary_en))
        for a in r.amounts:
            out.append(f"    amount: {a.label}: {a.amount_text}")
        for d in r.dates:
            out.append(f"    date: {d.label}: {d.date_text}")
        for issue in result.intake_issues:
            out.append(_wrap("unverified: " + issue))
    if result.assignment:
        a = result.assignment
        out.append(f"[matcher] subject={a.subject_member_id} actor={a.actor_member_id} skill={result.skill_id or a.skill_id} account={a.account_id or '-'}")
    for plan in result.plans:
        out.append(f"\n[plan v{plan.revision}] verdict={plan.verdict or '-'} (model said {plan.model_verdict or '-'})")
        for proposal, decision in zip(plan.proposals, plan.decisions, strict=False):
            amount = f" {proposal.amount} {proposal.currency}" if proposal.amount else ""
            out.append(f"    {proposal.id}: {proposal.action_type} on {proposal.rail} for {proposal.subject_member_id}{amount}"
                       + (f" -> {proposal.recipient}" if proposal.recipient else ""))
            out.append(f"        {decision.outcome.upper()} [{decision.rule_id}] " + "; ".join(decision.reasons))
        for need in plan.needs:
            out.append(f"    needs: {need}")
        for note in plan.notes:
            out.append(f"    note: {note}")
    for receipt in result.receipts:
        out.append(f"[receipt] {receipt.id} {receipt.action_id} {receipt.rail} {receipt.mode} under {receipt.executed_under_grant}: {receipt.label_reason}")
    for decision in result.approvals_needed:
        out.append(f"[approval needed] {decision.action_id}: {', '.join(decision.approver_ids)}")
    g = result.guard
    out.append(f"\nAuthority guard: {g.decisions_checked} decisions checked, {g.overrides} overrides, "
               f"{g.verdict_overrides} verdict overrides, {g.dropped_proposals} proposals dropped, {g.dropped_receipts} receipts dropped")
    for note in g.notes:
        out.append(f"    guard: {note}")
    out.append(f"OUTCOME: {result.outcome.upper()}")
    if result.briefing:
        b = result.briefing
        out.append(f"\nBriefing [EN]: {b.headline_en}")
        if b.headline_target != b.headline_en:
            out.append(f"Briefing [{result.language.upper()}]: {b.headline_target}")
        for line in b.done:
            out.append(_wrap("done: " + line))
        for line in b.waiting_on:
            out.append(_wrap("waiting on: " + line))
        out.append(_wrap("next: " + b.next_step_en))
    for note in result.notes:
        out.append(f"note: {note}")
    return "\n".join(out)


def _serializable(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("event") == "result":
        return {"event": "result", "result": event["result"].model_dump(mode="json")}
    return event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="household", description="A Strands Agents household authority agent")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one request through the graph")
    run.add_argument("--request", default=None, help="request fixture id, a path to a request JSON file, or raw text")
    run.add_argument("--image", default=None, help="a photo (PNG, JPEG, GIF, WebP) or PDF of a document to show the intake reader; "
                                                   "alone, the file stands for the request (needs --actor); with --request, both go in")
    run.add_argument("--actor", default=None, help="member id speaking (defaults to the fixture's actor)")
    run.add_argument("--household", default=None, help="household id (defaults to the fixture's household)")
    run.add_argument("--provider", choices=SELECTABLE, default=None, help="override MODEL_PROVIDER")
    run.add_argument("--persist", action="store_true", help="use and update the working ledger in data/ instead of a session-only copy of the seed")
    run.add_argument("--json", action="store_true", help="print {events, result} as JSON")
    run.add_argument("--trace", action="store_true", help="print roster events as the graph runs (to stderr with --json)")
    sub.add_parser("fixtures", help="list request fixtures")
    sub.add_parser("providers", help="show which model providers are ready (no key values are printed)")
    sub.add_parser("roster", help="show the six agents")
    sub.add_parser("skills", help="show the skills in tie-break order")
    dr = sub.add_parser("doctor", help="check .env, provider readiness, and make one cheap live call")
    dr.add_argument("--provider", choices=SELECTABLE, default=None)
    dr.add_argument("--no-ping", action="store_true")
    sub.add_parser("graph", help="print the real graph as Mermaid")
    sv = sub.add_parser("serve", help="serve the household web UI")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    rl = sub.add_parser("rails", help="show the action rails, their honest labels, and what this environment can complete")
    rl.add_argument("--write-readme", action="store_true", help="regenerate the 'What is real' table in README.md")
    args = parser.parse_args(argv)

    if args.command == "run":
        settings = load_settings(provider=args.provider)
        store = FixtureStore()
        upload = Path(args.image) if args.image else None
        if args.request is None and upload is None:
            parser.error("run needs --request, --image, or both")
        if upload is not None and not upload.is_file():
            parser.error(f"--image {upload} is not a file")
        if args.request is not None:
            fixture = store.request_from(args.request, args.actor)
        else:
            try:
                fixture = upload_request(upload, args.actor, store=store)  # type: ignore[arg-type]
            except KeyError:
                parser.error("--image alone needs --actor (whose photo is this?)")
        if args.household:
            fixture = type(fixture)(**{**fixture.__dict__, "household_id": args.household})
        ledger = None
        if args.persist:
            from .store import JsonLedgerStore

            ledger = JsonLedgerStore()
        trace_out = sys.stderr if args.json else sys.stdout

        async def collect() -> tuple[list[dict[str, Any]], SessionResult]:
            events: list[dict[str, Any]] = []
            final: SessionResult | None = None
            async for event in stream_session(fixture, args.actor, settings=settings, store=store, ledger=ledger, now=datetime.now(UTC),
                                              upload=upload):
                if event["event"] == "result":
                    final = event["result"]
                    continue
                events.append(event)
                if args.trace:
                    if event["event"] == "node_start":
                        print(f"  > {event['node_id']} run {event['run']} started", file=trace_out)
                    elif event["event"] == "node_done":
                        print(f"  < {event['node_id']} run {event['run']} {event['status']} ({event['execution_ms']} ms)", file=trace_out)
                    elif event["event"] == "action":
                        d = event["decision"]
                        print(f"  * {d['action_id']} {d['outcome']} [{d['rule_id']}]", file=trace_out)
                    elif event["event"] in {"approval_needed", "receipt"}:
                        print(f"  * {event['event']}: {json.dumps(event, ensure_ascii=False)[:160]}", file=trace_out)
            assert final is not None
            return events, final

        events, result = asyncio.run(collect())
        if args.json:
            print(json.dumps({"events": events, "result": result.model_dump(mode="json")}, ensure_ascii=False, indent=2))
        else:
            print(render(result))
        return 0
    if args.command == "fixtures":
        for req in FixtureStore().requests():
            exp = req.expected
            routing = ", ".join(exp.allow) or ("approval by " + ", ".join(exp.approval_by) if exp.approval_by else "-")
            print(f"{req.id:24} {req.actor_member_id:8} {exp.kind:12} {exp.skill_id or '-':10} {routing:40} {req.request[:60]}")
        return 0
    if args.command == "providers":
        settings = load_settings()
        for name, status in provider_readiness().items():
            marker = "*" if name == settings.provider else " "
            print(f"{marker} {name:10} ready={status['ready']!s:5} {status['why']}")
        print(f"selected: {settings.requested_provider} -> {settings.provider} -> {settings.model_id}")
        return 0
    if args.command == "doctor":
        from .doctor import run_doctor

        report = run_doctor(load_settings(provider=args.provider), ping=not args.no_ping)
        print(report.render())
        return 0 if report.ping_ok is not False else 1
    if args.command == "rails":
        from .executor import readme as rails_readme

        print(rails_readme.rails_table())
        print()
        print(rails_readme.current_labels(load_settings()))
        if args.write_readme:
            changed = rails_readme.write_readme()
            print("README.md rails block " + ("regenerated" if changed else "already current"))
        return 0
    if args.command == "serve":
        import uvicorn

        uvicorn.run("household.web.app:app", host=args.host, port=args.port, log_level="info")
        return 0
    if args.command == "graph":
        print(describe_graph(load_settings()))
        return 0
    if args.command == "roster":
        for spec in ROSTER:
            print(f"{spec.id:12} {spec.name:16} {'can reject' if spec.can_reject else '':10} {spec.job}")
        return 0
    if args.command == "skills":
        for skill in ALL_SKILLS:
            print(f"{skill.id:12} {skill.name:40} {', '.join(skill.action_types):40} tools: {', '.join(t.name for t in skill.tools) or '-'}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
