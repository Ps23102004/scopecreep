"""The cascade judge: map every file group to a classification, reduce to a verdict.

Mirrors cvewatch/digest.py's structure — prompt -> _loads -> validate -> raise
JudgeParseError. Small local models are the target, so every response is parsed
defensively and every field is checked before it's trusted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from llm_ladder.config import ChainConfig, load_chains
from llm_ladder.engine import run_cascade
from llm_ladder.ollama_client import OllamaConnectionError, OllamaModelNotFoundError

CHAINS_PATH = Path(__file__).parent.parent / "chains.yaml"
CHAIN_NAME = "judge"

CLASSIFICATIONS = ("core", "supporting", "unrelated")
VERDICTS = ("in_scope", "minor_drift", "scope_creep")

# PR bodies run to essays and file lists to hundreds of entries; a small model's
# context is the scarce resource, and the tail of either rarely changes the call.
_MAX_TEXT = 1200
_MAX_FILES = 20

_MAP_PROMPT = """You judge whether one group of changed files belongs in a pull request.
You are given the PR's title, body, linked issue, and ONE group of changed files.

Classify the group as exactly one of:
- "core": these files directly implement the change the title/issue asks for.
- "supporting": these files exist to make the core change work or land safely —
  tests for it, docs for it, config it needs, refactors it forced.
- "unrelated": these files have nothing to do with the stated title/issue — a
  different feature, a drive-by dependency swap, an opportunistic rewrite.

Judge only against what the title and issue actually say. A change can be good,
useful, and still be "unrelated" — you are measuring scope, not quality.

Respond with STRICT JSON ONLY — no prose, no markdown fences.
Schema (exactly these two keys):
{{"classification": "core|supporting|unrelated", "reason": "<one short line>"}}

Example input:
PR title: Add retry logic to the payment gateway client
PR body: Wraps the HTTP call in a retry with exponential backoff.
Issue: #418 Payment gateway calls fail transiently; add retry.
Group: src/*.py  files=[src/payment/gateway.py]  adds=47 dels=3
Example output:
{{"classification": "core", "reason": "Implements the retry and backoff the issue asks for."}}

Example input:
PR title: Add retry logic to the payment gateway client
PR body: Wraps the HTTP call in a retry with exponential backoff.
Issue: #418 Payment gateway calls fail transiently; add retry.
Group: tests/*.py  files=[tests/test_gateway.py]  adds=61 dels=0
Example output:
{{"classification": "supporting", "reason": "Tests covering the new retry path."}}

Example input:
PR title: Add retry logic to the payment gateway client
PR body: Wraps the HTTP call in a retry with exponential backoff.
Issue: #418 Payment gateway calls fail transiently; add retry.
Group: src/*.py  files=[src/auth/session.py]  adds=96 dels=88
Example output:
{{"classification": "unrelated", "reason": "Rewrites session auth, which the retry fix never touches."}}

Now classify this group.
PR title: {title}
PR body: {body}
Issue: {issue}
Group: {group_key}  files=[{group_files}]  adds={group_adds} dels={group_dels}
Output:"""

_REDUCE_PROMPT = """You are summarising a pull request scope review.
You are given the PR's title, body, linked issue, and one classification per group
of changed files. Produce the overall verdict.

verdict — exactly one of:
- "in_scope": every group is core or supporting. The PR does what it says.
- "minor_drift": one small unrelated group rode along, but the core change is
  intact and a reviewer would wave it through.
- "scope_creep": unrelated work is substantial — several unrelated groups, or one
  big one. The PR bundles changes its title never mentions.

drift_score — an integer 0-100. 0 means the diff matches the title exactly; 100
means the title describes almost none of the diff. Weight by how much churn sits
in unrelated groups, not just how many groups there are.

summary — exactly 2 sentences of plain English. First sentence: what the PR
actually does. Second: what, if anything, drifted, named specifically.

Respond with STRICT JSON ONLY — no prose, no markdown fences.
Schema (exactly these three keys):
{{"verdict": "in_scope|minor_drift|scope_creep", "drift_score": <int 0-100>, "summary": "<2 sentences>"}}

Example input:
PR title: Add retry logic to the payment gateway client
Issue: #418 Payment gateway calls fail transiently; add retry.
Group classifications:
1. group=src/*.py classification=core reason=Implements the retry and backoff.
2. group=tests/*.py classification=supporting reason=Tests the new retry path.
3. group=src/*.py classification=unrelated reason=Rewrites session auth.
Example output:
{{"verdict": "scope_creep", "drift_score": 62, "summary": "The PR adds retry with backoff to the payment gateway client and tests it. It also rewrites session authentication, which the title and issue never mention and which a reviewer looking at a retry fix would not expect to check."}}

Now roll up this review.
PR title: {title}
PR body: {body}
Issue: {issue}
Group classifications:
{group_lines}
Output:"""

_EMPTY_ROLLUP_SUMMARY = (
    "No substantive file groups to review; everything changed was filtered as "
    "noise (lockfiles, generated files, or formatting-only edits). Nothing in "
    "this PR drifts from its stated purpose."
)


class JudgeParseError(Exception):
    """The model's answer could not be parsed or failed validation."""


@dataclass(frozen=True)
class GroupVerdict:
    key: str
    classification: str
    reason: str


@dataclass(frozen=True)
class Rollup:
    verdict: str
    drift_score: int
    summary: str


# -- parsing ----------------------------------------------------------------


def _loads(text: str) -> dict:
    """Parse JSON from a model response, with one repair pass for stray prose."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise JudgeParseError(f"model response contained no JSON object: {text!r}")
    # Small local models routinely emit a trailing comma before ] or } — strip
    # it rather than lose the whole verdict to one stray character.
    candidate = re.sub(r",(\s*[\]}])", r"\1", match.group(0))
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise JudgeParseError(f"model response was not valid JSON: {text!r}") from exc


def _text(value: str, field: str) -> str:
    """Require a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise JudgeParseError(f"'{field}' must be a non-empty string, got {value!r}")
    return value.strip()


def _choice(value, field: str, allowed: tuple[str, ...]) -> str:
    """Require a string from `allowed`, compared case-insensitively."""
    if not isinstance(value, str):
        raise JudgeParseError(f"'{field}' must be a string, got {value!r}")
    normalized = value.strip().lower()
    if normalized not in allowed:
        raise JudgeParseError(f"'{field}' must be one of {allowed}, got {value!r}")
    return normalized


def _score(value) -> int:
    """Require an integer 0-100, tolerating "62" and 62.0 but never a bool."""
    if isinstance(value, bool):
        raise JudgeParseError(f"'drift_score' must be a number, got bool {value!r}")
    try:
        as_float = float(value)
    except (TypeError, ValueError) as exc:
        raise JudgeParseError(
            f"'drift_score' must be an integer 0-100, got {value!r}"
        ) from exc
    if not as_float.is_integer():
        raise JudgeParseError(f"'drift_score' must be a whole number, got {value!r}")
    score = int(as_float)
    if not 0 <= score <= 100:
        raise JudgeParseError(f"'drift_score' must be within 0-100, got {value!r}")
    return score


# -- formatting -------------------------------------------------------------


def _clip(text: str, limit: int = _MAX_TEXT) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " […]"


def _fmt_files(files: list[str]) -> str:
    if len(files) <= _MAX_FILES:
        return ", ".join(files)
    shown = ", ".join(files[:_MAX_FILES])
    return f"{shown}, … and {len(files) - _MAX_FILES} more"


def _fmt_groups(verdicts: list[GroupVerdict]) -> str:
    """One line per verdict, for the reduce prompt."""
    return "\n".join(
        f"{i}. group={v.key} classification={v.classification} "
        f"reason={v.reason.replace(chr(10), ' ')}"
        for i, v in enumerate(verdicts, start=1)
    )


# -- cascade ----------------------------------------------------------------


def _cascade(prompt: str, chain_config: ChainConfig, ledger=None) -> str:
    """Run the cascade, turning an unreachable model into a JudgeParseError."""
    try:
        result = run_cascade(prompt, CHAIN_NAME, chain_config, ledger=ledger)
    except (OllamaConnectionError, OllamaModelNotFoundError) as exc:
        raise JudgeParseError(
            f"local model inference is unavailable (is Ollama running with the "
            f"{CHAIN_NAME} chain's models pulled?): {exc}"
        ) from exc
    return result.answer


# -- map --------------------------------------------------------------------


def classify_group(
    title: str,
    body: str,
    issue: str,
    group,
    chain_config: ChainConfig,
    ledger=None,
) -> GroupVerdict:
    """Classify one FileGroup as core, supporting, or unrelated."""
    prompt = _MAP_PROMPT.format(
        title=_clip(title, 300),
        body=_clip(body),
        issue=_clip(issue) or "(none linked)",
        group_key=group.key,
        group_files=_fmt_files(group.files),
        group_adds=group.adds,
        group_dels=group.dels,
    )
    data = _loads(_cascade(prompt, chain_config, ledger=ledger))
    return GroupVerdict(
        key=group.key,
        classification=_choice(data.get("classification"), "classification", CLASSIFICATIONS),
        reason=_text(data.get("reason"), "reason"),
    )


# -- reduce -----------------------------------------------------------------


def rollup(
    title: str,
    body: str,
    issue: str,
    verdicts: list[GroupVerdict],
    chain_config: ChainConfig,
    ledger=None,
) -> Rollup:
    """Turn per-group classifications into one verdict, score, and summary."""
    prompt = _REDUCE_PROMPT.format(
        title=_clip(title, 300),
        body=_clip(body),
        issue=_clip(issue) or "(none linked)",
        group_lines=_fmt_groups(verdicts),
    )
    data = _loads(_cascade(prompt, chain_config, ledger=ledger))
    return Rollup(
        verdict=_choice(data.get("verdict"), "verdict", VERDICTS),
        drift_score=_score(data.get("drift_score")),
        summary=_text(data.get("summary"), "summary"),
    )


# -- orchestration ----------------------------------------------------------


def judge(
    title: str,
    body: str,
    issue: str,
    groups: list,
    chain_config: ChainConfig | None = None,
    ledger=None,
    on_group: Callable | None = None,
) -> tuple[list[GroupVerdict], Rollup]:
    """Map every group to a classification, then reduce to an overall verdict.

    `on_group(group, verdict)` fires immediately after each group is classified
    — not batched at the end — so a live UI can fill in rows as they land.

    An all-noise PR short-circuits: nothing left to judge means no model call.
    """
    if not groups:
        return [], Rollup(
            verdict="in_scope", drift_score=0, summary=_EMPTY_ROLLUP_SUMMARY
        )

    if chain_config is None:
        chain_config = load_chains(str(CHAINS_PATH))[CHAIN_NAME]

    verdicts: list[GroupVerdict] = []
    for group in groups:
        verdict = classify_group(title, body, issue, group, chain_config, ledger=ledger)
        verdicts.append(verdict)
        if on_group is not None:
            on_group(group, verdict)

    return verdicts, rollup(title, body, issue, verdicts, chain_config, ledger=ledger)
