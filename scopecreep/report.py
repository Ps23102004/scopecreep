"""Rendering a CheckResult three ways: terminal table, JSON, markdown."""

from __future__ import annotations

import json

__all__ = ["render_table", "render_json", "render_markdown", "VERDICT_LABELS"]

VERDICT_LABELS = {
    "in_scope": "IN SCOPE",
    "minor_drift": "MINOR DRIFT",
    "scope_creep": "SCOPE CREEP",
}

_CLASS_LABELS = {"core": "core", "supporting": "supporting", "unrelated": "UNRELATED"}


def _churn(group) -> str:
    return f"+{group.adds}/-{group.dels}"


def render_table(result) -> str:
    """The default terminal view: one row per file group, then the verdict."""
    lines = [
        f"{result.repo}#{result.number}  {result.title}",
    ]
    if result.issue_number is not None:
        lines.append(f"linked issue: #{result.issue_number}")
    lines.append("")

    by_key = {v.key: v for v in result.verdicts}
    width = max([len(g.key) for g in result.groups + result.noise] + [10])
    lines.append(f"{'GROUP'.ljust(width)}  {'CHURN':<12} {'CLASS':<11} REASON")
    for group in result.groups:
        verdict = by_key.get(group.key)
        classification = _CLASS_LABELS.get(
            verdict.classification if verdict else "", "?"
        )
        reason = verdict.reason if verdict else ""
        lines.append(
            f"{group.key.ljust(width)}  {_churn(group):<12} {classification:<11} {reason}"
        )
    for group in result.noise:
        lines.append(
            f"{group.key.ljust(width)}  {_churn(group):<12} {'filtered':<11} "
            f"excluded: {group.noise_reason}"
        )

    lines.append("")
    label = VERDICT_LABELS.get(result.rollup.verdict, result.rollup.verdict)
    lines.append(f"{label}  —  drift score {result.rollup.drift_score}/100")
    lines.append(result.rollup.summary)
    lines.append("")
    lines.append(result.footer)
    return "\n".join(lines)


def render_json(result) -> str:
    return json.dumps(result.to_dict(), indent=2)


def render_markdown(result) -> str:
    """A markdown report — paste-able straight into a PR comment."""
    label = VERDICT_LABELS.get(result.rollup.verdict, result.rollup.verdict)
    lines = [
        f"## ScopeCreep — {result.repo}#{result.number}",
        "",
        f"**{result.title}**",
        "",
        f"**Verdict: {label}** · drift score {result.rollup.drift_score}/100",
        "",
        result.rollup.summary,
        "",
        "| Group | Churn | Classification | Reason |",
        "| --- | --- | --- | --- |",
    ]
    by_key = {v.key: v for v in result.verdicts}
    for group in result.groups:
        verdict = by_key.get(group.key)
        classification = verdict.classification if verdict else "?"
        reason = (verdict.reason if verdict else "").replace("|", "\\|")
        lines.append(
            f"| `{group.key}` | {_churn(group)} | {classification} | {reason} |"
        )
    for group in result.noise:
        lines.append(
            f"| `{group.key}` | {_churn(group)} | _filtered_ | excluded: {group.noise_reason} |"
        )
    lines += ["", f"_{result.footer}_"]
    return "\n".join(lines)
