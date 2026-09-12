#!/usr/bin/env python3
"""Turn a raw `household run --json` trace into a sanitized receipt under docs/receipts/.

    python scripts/sanitize_receipt.py runs/live/*.json --out docs/receipts/ [--date YYYY-MM-DD] [--suffix before-fix]

Every string in the trace is scrubbed: AWS ARNs, 12-digit account ids, access key ids, email addresses, session ids
(the hex prefix code puts in every action id), bare UUIDs / 32-hex tokens, and absolute local paths. The receipt keeps
what a judge needs: the exact command, the model id and call count per roster node (from an optional `<name>.stats.json`
sidecar written beside the trace), the shared budget snapshot, latency, the outcome, the guard report, and for a photo
the extraction score against the fixture's truth file. The sanitizer re-scans its own output and refuses to write a
receipt that still carries any of the patterns above, so a receipt in docs/receipts/ is clean by construction.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from household.intake import load_truth, score_extraction  # noqa: E402
from household.schemas import IntakeReading  # noqa: E402

SHA256 = re.compile(r"\b[0-9a-f]{64}\b")  # request digests are evidence, not identifiers; they stay
PATTERNS: dict[str, re.Pattern[str]] = {
    "arns": re.compile(r"arn:aws[a-zA-Z-]*:[^\s\"'<>]+"),
    "aws_access_key_ids": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "aws_account_ids": re.compile(r"\b\d{12}\b"),
    "emails": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "uuids": re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    "hex32": re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])"),
    "session_ids": re.compile(r"\bact-([0-9a-f]{8})-"),
    "paths": re.compile(r"(?:/home/[^\s\"'<>]+|/tmp/[^\s\"'<>]+|/Users/[^\s\"'<>]+)"),
}


class Scrubber:
    """Stable replacements within one receipt: the same email or session id always becomes the same token."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {name: 0 for name in PATTERNS}
        self.tokens: dict[str, dict[str, str]] = {name: {} for name in PATTERNS}

    def _token(self, kind: str, raw: str, label: str) -> str:
        table = self.tokens[kind]
        if raw not in table:
            table[raw] = f"<{label}-{len(table) + 1}>"
        self.counts[kind] += 1
        return table[raw]

    def text(self, value: str) -> str:
        digests = {m.group(0): f"\x00{i}\x00" for i, m in enumerate(SHA256.finditer(value))}
        for digest, placeholder in digests.items():
            value = value.replace(digest, placeholder)
        value = PATTERNS["arns"].sub(lambda m: self._token("arns", m.group(0), "arn"), value)
        value = PATTERNS["aws_access_key_ids"].sub(lambda m: self._token("aws_access_key_ids", m.group(0), "aws-access-key-id"), value)
        value = PATTERNS["aws_account_ids"].sub(lambda m: self._token("aws_account_ids", m.group(0), "aws-account-id"), value)
        value = PATTERNS["emails"].sub(lambda m: self._token("emails", m.group(0).lower(), "email"), value)
        value = PATTERNS["uuids"].sub(lambda m: self._token("uuids", m.group(0), "uuid"), value)
        value = PATTERNS["hex32"].sub(lambda m: self._token("hex32", m.group(0), "id"), value)
        value = PATTERNS["session_ids"].sub(lambda m: "act-" + self._token("session_ids", m.group(1), "session").strip("<>") + "-", value)
        value = PATTERNS["paths"].sub(lambda m: self._path(m.group(0)), value)
        for digest, placeholder in digests.items():
            value = value.replace(placeholder, digest)
        return value

    def _path(self, raw: str) -> str:
        self.counts["paths"] += 1
        root = str(ROOT)
        if raw.startswith(root):
            return raw[len(root):].lstrip("/") or "."
        return "<local-path>"

    def walk(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.walk(v) for v in value]
        if isinstance(value, dict):
            return {self.text(str(k)): self.walk(v) for k, v in value.items()}
        return value


def residue(text: str) -> dict[str, int]:
    """Patterns still present after scrubbing (session ids are checked on the hex prefix code would have written)."""
    found = {name: len(pattern.findall(text)) for name, pattern in PATTERNS.items() if name not in ("paths", "session_ids")}
    found["session_ids"] = len(re.findall(r"\bact-[0-9a-f]{8}-", text))
    found["paths"] = len(re.findall(r"/home/|/Users/|/tmp/", text))
    return {k: v for k, v in found.items() if v}


def roster_trace(result: dict[str, Any]) -> str:
    return " -> ".join(f"{s['node_id']}{'#' + str(s['run']) if s['run'] > 1 else ''}" for s in result.get("roster", []))


def extraction(result: dict[str, Any]) -> dict[str, Any] | None:
    """Score the intake reading (as bound by code) against the fixture truth when the upload is a rendered fixture."""
    upload = result.get("upload_name") or ""
    reading = result.get("reading")
    if not upload or reading is None:
        return None
    truth_path = ROOT / "fixtures" / "images" / (Path(upload).stem + ".truth.json")
    if not truth_path.exists():
        return None
    score = score_extraction(IntakeReading.model_validate(reading), load_truth(truth_path))
    return {"truth": truth_path.relative_to(ROOT).as_posix(), "correct": score.correct, "total": score.total, "misses": list(score.misses),
            "note": "scored on the reading after bind_image_values; anything the binder removed is listed under intake_issues"}


def node_calls(stats: dict[str, Any] | None) -> dict[str, Any] | None:
    if not stats:
        return None
    nodes = {}
    for node, v in stats.get("nodes", {}).items():
        nodes[node] = {"model_id": v.get("model_id"), "attempted": v.get("attempted"), "completed": v.get("completed"),
                       "errored": v.get("errored"), "latency_ms": list(v.get("latency_ms", [])),
                       "input_tokens": v.get("input_tokens"), "output_tokens": v.get("output_tokens")}
    return nodes


def build_receipt(path: Path, date: str, suffix: str | None, note: str | None) -> tuple[dict[str, Any], Scrubber]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not (isinstance(raw, dict) and "result" in raw and "events" in raw):
        raise SystemExit(f"{path}: not a `household run --json` trace ({{events, result}})")
    stats_path = path.with_name(path.stem + ".stats.json")
    stats = json.loads(stats_path.read_text(encoding="utf-8")) if stats_path.exists() else None
    result = raw["result"]
    guard = result.get("guard", {})
    receipt: dict[str, Any] = {
        "kind": "live-trace" + (f" ({suffix})" if suffix else ""),
        "date_utc": date,
        "source": path.as_posix() if not path.is_absolute() else str(path),
        "command": (stats or {}).get("command"),
        "env": (stats or {}).get("env"),
        "provider": result.get("provider"),
        "model_id": result.get("model_id"),
        "execution_mode": result.get("execution_mode"),
        "request_id": result.get("request_id"),
        "actor": result.get("actor_member_id"),
        "upload": result.get("upload_name") or None,
        "roster_trace": roster_trace(result),
        "outcome": result.get("outcome"),
        "graph_status": result.get("graph_status"),
        "elapsed_ms": result.get("elapsed_ms"),
        "wall_ms": (stats or {}).get("wall_ms"),
        "model_calls": result.get("model_calls"),
        "node_calls": node_calls(stats),
        "tokens": None if not stats else {"input": stats.get("total_input_tokens"), "output": stats.get("total_output_tokens")},
        "guard": guard,
        "intake_issues": result.get("intake_issues", []),
        "plans": [{"revision": p["revision"], "verdict": p["verdict"], "model_verdict": p["model_verdict"],
                   "actions": [f"{a['action_type']} {a.get('amount') or ''} {a.get('currency') or ''} on {a['rail']} -> {d['outcome']} [{d['rule_id']}]".replace("  ", " ")
                               for a, d in zip(p["proposals"], p["decisions"], strict=False)],
                   "needs": p.get("needs", []), "notes": p.get("notes", [])} for p in result.get("plans", [])],
        "receipts": [{"id": r["id"], "action_id": r["action_id"], "rail": r["rail"], "mode": r["mode"], "label_reason": r["label_reason"]}
                     for r in result.get("receipts", [])],
        "approvals_needed": [{"action_id": a["action_id"], "approver_ids": a["approver_ids"]} for a in result.get("approvals_needed", [])],
        "briefing": result.get("briefing"),
        "extraction_score": extraction(result),
        "note": note,
        "trace": raw,
    }
    scrubber = Scrubber()
    clean = scrubber.walk(receipt)
    clean["redactions"] = dict(scrubber.counts)
    clean["sanitizer"] = "scripts/sanitize_receipt.py: ARNs, AWS account ids, access key ids, emails, session ids, UUIDs and local paths replaced by stable tokens; sha256 request digests kept as evidence"
    return clean, scrubber


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("inputs", nargs="+", help="raw trace JSON files (a *.stats.json sidecar beside each is attached automatically)")
    parser.add_argument("--out", required=True, help="directory for the receipts")
    parser.add_argument("--date", default=dt.datetime.now(dt.UTC).date().isoformat())
    parser.add_argument("--suffix", default=None, help="extra name part, e.g. before-fix for a first attempt kept as evidence")
    parser.add_argument("--note", default=None, help="a sentence recorded on every receipt written in this call")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name in args.inputs:
        path = Path(name)
        if path.name.endswith(".stats.json") or path.suffix != ".json":
            continue
        receipt, scrubber = build_receipt(path, args.date, args.suffix, args.note)
        text = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        left = residue(text)
        target = out / f"live-{path.stem}-{args.date}{'.' + args.suffix if args.suffix else ''}.json"
        if left:
            failed += 1
            print(f"REFUSED {target}: residue after scrubbing {left}", file=sys.stderr)
            continue
        target.write_text(text, encoding="utf-8")
        calls = receipt.get("model_calls") or {}
        print(f"{target}: outcome={receipt['outcome']} calls={calls.get('attempted')}/{calls.get('limit')} "
              f"nodes={ {k: v['attempted'] for k, v in (receipt.get('node_calls') or {}).items()} } redactions={ {k: v for k, v in scrubber.counts.items() if v} }")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
