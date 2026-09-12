"""The README "What is real" block is generated from the same RAILS metadata the executor uses, so the table cannot
drift from the code. Regenerate with `household rails --write-readme`; the block is deterministic (no dates, no env)."""

from __future__ import annotations

import re
from pathlib import Path

from ..config import ROOT, Settings
from . import RAILS, rails
from .receipts import label_for

START, END = "<!-- rails:start -->", "<!-- rails:end -->"
# Only markers on their own line delimit the block: the README prose mentions them in backticks.
BLOCK = re.compile(r"^" + re.escape(START) + r"\n.*?^" + re.escape(END) + r"$", re.S | re.M)
README = ROOT / "README.md"
INTRO = (
    "Every action ends in a receipt whose label is decided **in code from the environment, never by the model**: "
    "`COMPLETE` (a real side effect happened; the provider's own reference is on the receipt), "
    "`PREPARE-ONLY` (rendered for a human to review and file; never submitted), "
    "`SIMULATED-replay` (a recorded response was replayed; the receipt says when it was fetched) and "
    "`SIMULATED` (nothing left the machine; the receipt carries the sha256 of the exact request it would have sent). "
    "Consumer bank rails have no APIs, so \"complete\" never means a fabricated bank transfer. "
    "The same request (household, action, subject, recipient, amount, evidence, day) returns the receipt already issued "
    "instead of acting twice. `EXECUTION_MODE=simulated` is the default; regenerate this table with `household rails --write-readme`."
)


def rails_table() -> str:
    rows = ["| Rail | What is real | Label | Receipt `label_reason` values |", "|---|---|---|---|"]
    for rail, meta in RAILS.items():
        reasons = " · ".join(f"`{reason}`" for reason in meta["reasons"])
        rows.append(f"| {meta['name']} (`{rail}`) | {meta['real']} | {meta['label']} | {reasons} |")
    return "\n".join(rows)


def render_block() -> str:
    return f"{START}\n{INTRO}\n\n{rails_table()}\n{END}"


def write_readme(readme: Path = README) -> bool:
    """Replace the block between the markers (or append a section when they are missing). Returns whether it changed."""
    text = readme.read_text(encoding="utf-8")
    block = render_block()
    if BLOCK.search(text):
        updated = BLOCK.sub(lambda _: block, text, count=1)
    else:
        updated = text.rstrip("\n") + "\n\n## What is real\n\n" + block + "\n"
    if updated == text:
        return False
    temp = readme.with_suffix(".tmp")
    temp.write_text(updated, encoding="utf-8")
    temp.replace(readme)
    return True


def current_labels(settings: Settings, recall_number: str = "26639") -> str:
    """What each rail would earn right now, from the loaded settings (printed by the CLI, never written to README)."""
    head = (
        f"In this environment (EXECUTION_MODE={settings.execution_mode}, SES_FROM {'set' if settings.ses_from else 'unset'}, "
        f"{len(settings.ses_verified_identities)} verified SES identities, Stripe key {'present' if settings.stripe_secret_key_present else 'absent'}, "
        f"AeroAPI key {'present' if settings.aeroapi_key_present else 'absent'}):"
    )
    lines = [head]
    for rail in RAILS:
        context = {"fetched": rails.recorded_fetch_date(rails.cpsc_fixture_path(recall_number))} if rail == "external-api-readonly" else {}
        label = label_for(rail, settings, context)
        lines.append(f"  {rail:22} {label.mode:16} {label.reason}")
    return "\n".join(lines)
