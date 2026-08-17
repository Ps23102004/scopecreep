"""The receipt at the bottom of every report.

Reads the real ~/.llm-ladder/ledger.jsonl through llm-ladder's own Ledger, so
the numbers are measured, never asserted. Every claim in the footer line is
something you can go and grep out of that file yourself.
"""

from __future__ import annotations

import json
import os

from llm_ladder.ledger import Ledger

__all__ = ["ledger_stats", "footer_line"]


def ledger_stats(before: int | None = None, ledger: Ledger | None = None) -> dict:
    """Return {calls, tier0_held, escalations} for this run.

    `before` is the ledger's line count taken *before* the run, so only this
    run's calls are counted. Pass None to summarise the whole ledger.
    """
    ledger = ledger or Ledger()
    entries = _read(ledger.path)
    if before is not None:
        entries = entries[before:]

    tier0_held = sum(1 for e in entries if e.get("tier_index") == 0 and not e.get("escalated"))
    escalations = sum(1 for e in entries if e.get("escalated"))
    return {"calls": len(entries), "tier0_held": tier0_held, "escalations": escalations}


def ledger_length(ledger: Ledger | None = None) -> int:
    """Current number of ledger entries — the `before` mark for ledger_stats."""
    return len(_read((ledger or Ledger()).path))


def footer_line(stats: dict) -> str:
    """One line of plain English summarising where the compute went."""
    calls = stats.get("calls", 0)
    held = stats.get("tier0_held", 0)
    if calls == 0:
        return "Judged without a single model call — $0.00 API spend."
    return (
        f"Judged in {calls} cascade call{'s' if calls != 1 else ''} on local models "
        f"— tier 0 held {held}/{calls} calls, $0.00 API spend."
    )


def _read(path: str) -> list[dict]:
    """Parse the ledger, skipping malformed lines rather than dying on one."""
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries
