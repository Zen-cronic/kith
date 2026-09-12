"""household: run a session, list fixtures, languages and providers."""

from __future__ import annotations

import argparse
import sys
import textwrap

from .agents.graph import describe_graph
from .agents.roster import ROSTER
from .config import SELECTABLE, load_settings, provider_readiness
from .fixtures import FixtureStore
from .languages import LANGUAGES
from .pipeline import SessionResult, run_session, stream_session


def _wrap(text: str, indent: str = "    ") -> str:
    return textwrap.fill(text, width=100, initial_indent=indent, subsequent_indent=indent)


def render(result: SessionResult) -> str:
    out: list[str] = []
    out.append(f"Front Desk  |  provider: {result.provider} ({result.model_id})  |  language: {result.language}  |  {result.elapsed_ms} ms")
    out.append(f"Document: {result.fixture_title}  [{result.fixture_source}]")
    trace = " -> ".join(f"{s.node_id}{'#' + str(s.run) if s.run > 1 else ''}" for s in result.roster)
    skipped = [spec.id for spec in ROSTER if spec.id not in result.execution_order]
    out.append(f"Roster trace: {trace}" + (f"   (skipped: {', '.join(skipped)})" if skipped else ""))
    if result.reading:
        r = result.reading
        out.append(f"\n[EN] {r.title}  (class {r.document_class}, stakes {r.stakes}, confidence {r.confidence:.2f})")
        out.append(_wrap(r.what_it_is))
        out.append(_wrap(r.what_it_asks))
        for d in r.deadlines:
            out.append(f"    date: {d.label}: {d.date_text}")
        if r.amounts:
            out.append(f"    amounts: {', '.join(r.amounts)}")
    if result.interpretation:
        i = result.interpretation
        out.append(f"\n[{i.language.upper()}] " + i.target_text[:600] + ("..." if len(i.target_text) > 600 else ""))
        out.append("\n[back-translation] " + i.back_translation[:600] + ("..." if len(i.back_translation) > 600 else ""))
        f = result.fidelity
        if f:
            missing = f", missing numbers: {f['numbers_missing']}" if f.get("numbers_missing") else ""
            out.append(f"Fidelity gauge: {f['score']:.2f}  band={f['band']}{missing}")
    for d in result.drafts:
        out.append(f"\n[draft v{d.revision}] {d.kind}: {d.title}" + (f"  assumptions: {d.assumptions}" if d.assumptions else ""))
    for n, v in enumerate(result.verdicts, 1):
        out.append(f"[critic #{n}] {v.decision.upper()}" + (f" rule={v.rule_id}" if v.rule_id else ""))
        for c in v.checks:
            out.append(f"    {'ok ' if c.passed else 'FAIL'} {c.name}: {c.detail}")
        for note in v.revision_notes:
            out.append(f"    -> revise: {note}")
    g = result.guard
    out.append(f"\nPolicy guard: rule={g.rule_id} policy={g.rule_policy} fidelity={g.fidelity_band} -> {g.policy_outcome}; model -> {g.model_outcome}; {g.detail}")
    out.append(f"OUTCOME: {result.outcome.upper()}")
    if result.card:
        c = result.card
        out.append(f"\nCard [EN]: {c.headline_en}")
        out.append(_wrap(c.statement_en))
        out.append(f"Card [{result.language.upper()}]: {c.headline_target}")
        out.append(_wrap(c.statement_target))
        if c.rule_citation:
            out.append(f"    rule: {c.rule_citation}")
        out.append(f"    who: {c.who}")
        out.append(f"    next: {c.next_step_en}  |  when: {c.when}")
        out.append(f"    safe today: {c.safe_today_en}")
    for note in result.notes:
        out.append(f"note: {note}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="household", description="Front Desk: a Strands Agents household interpreter")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one session on a fixture document")
    run.add_argument("--fixture", required=True)
    run.add_argument("--lang", default="es")
    run.add_argument("--provider", choices=SELECTABLE, default=None, help="override MODEL_PROVIDER")
    run.add_argument("--json", action="store_true", help="print the SessionResult as JSON")
    run.add_argument("--trace", action="store_true", help="print roster events as the graph runs")
    sub.add_parser("fixtures", help="list fixture documents")
    sub.add_parser("languages", help="list languages and their speech coverage")
    sub.add_parser("providers", help="show which model providers are ready (no key values are printed)")
    sub.add_parser("roster", help="show the five agents")
    dr = sub.add_parser("doctor", help="check .env, provider readiness, and make one cheap live call")
    dr.add_argument("--provider", choices=SELECTABLE, default=None)
    dr.add_argument("--no-ping", action="store_true")
    sub.add_parser("graph", help="print the real graph as Mermaid")
    sv = sub.add_parser("serve", help="serve the household web UI")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    ts = sub.add_parser("trapset", help="replay the refusal trap-set and score it")
    ts.add_argument("--provider", choices=SELECTABLE, default=None)
    ts.add_argument("--lang", default="es")
    ts.add_argument("--write-readme", action="store_true", help="regenerate the results table in README.md")
    ts.add_argument("--all", action="store_true", help="include fixtures not tagged 'trapset'")
    rl = sub.add_parser("rails", help="show the action rails, their honest labels, and what this environment can complete")
    rl.add_argument("--write-readme", action="store_true", help="regenerate the 'What is real' table in README.md")
    args = parser.parse_args(argv)

    if args.command == "run":
        settings = load_settings(provider=args.provider)
        if args.trace:
            import asyncio

            async def traced() -> SessionResult:
                final: SessionResult | None = None
                async for event in stream_session(args.fixture, args.lang, settings=settings):
                    if event["event"] == "node_start":
                        print(f"  > {event['node_id']} run {event['run']} started")
                    elif event["event"] == "node_done":
                        print(f"  < {event['node_id']} run {event['run']} {event['status']} ({event['execution_ms']} ms)")
                    elif event["event"] == "result":
                        final = event["result"]
                assert final is not None
                return final

            result = traced_result = asyncio.run(traced())
            del traced_result
        else:
            result = run_session(args.fixture, args.lang, settings=settings)
        print(result.model_dump_json(indent=2, exclude_none=True) if args.json else render(result))
        return 0
    if args.command == "fixtures":
        for doc in FixtureStore().list():
            exp = doc.expected
            print(f"{doc.id:32} {exp.stakes:6} {'escalate' if exp.must_escalate else 'assist':8} {doc.source[:40]:40} {doc.title}")
        return 0
    if args.command == "languages":
        for lang in LANGUAGES.values():
            print(f"{lang.code:3} {lang.name_en:11} {lang.mode:15} transcribe={lang.transcribe!s:5} polly={lang.polly_voice or '-':22} nova_sonic={lang.nova_sonic}")
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
    if args.command == "trapset":
        from .trapset.harness import markdown_table, run_trapset, save_results, write_readme

        settings = load_settings(provider=args.provider)
        summary = run_trapset(settings, args.lang, only_tag=None if args.all else "trapset")
        path = save_results(summary)
        print(markdown_table(summary))
        print(f"results: {path}")
        if args.write_readme:
            write_readme(summary)
            print("README.md trap-set table regenerated")
        return 0
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
            print(f"{spec.id:12} {spec.name:28} {'can reject' if spec.can_reject else '':10} {spec.job}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
