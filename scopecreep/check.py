"""The pipeline: fetch a PR, filter the noise, judge what's left.

Shared by the CLI and the dashboard server so both take exactly the same path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from llm_ladder.config import ChainConfig, TierConfig, load_chains
from llm_ladder.ledger import Ledger

from scopecreep.github import PullRequest, fetch_pull_request, parse_pr_ref
from scopecreep.groups import FileGroup, build_groups, split_noise
from scopecreep.judge import CHAIN_NAME, CHAINS_PATH, GroupVerdict, Rollup, judge
from scopecreep.ledger_footer import footer_line, ledger_length, ledger_stats

__all__ = ["CheckResult", "check_pr", "check"]


@dataclass
class CheckResult:
    repo: str
    number: int
    title: str
    url: str
    issue_number: int | None
    groups: list[FileGroup]
    noise: list[FileGroup]
    verdicts: list[GroupVerdict]
    rollup: Rollup
    ledger: dict = field(default_factory=dict)

    @property
    def footer(self) -> str:
        return footer_line(self.ledger)

    def to_dict(self) -> dict:
        """The wire format — matches the frozen /api/status result contract."""
        by_key = {v.key: v for v in self.verdicts}
        return {
            "repo": self.repo,
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "issue_number": self.issue_number,
            "verdict": self.rollup.verdict,
            "drift_score": self.rollup.drift_score,
            "summary": self.rollup.summary,
            "groups": [
                {
                    "group": g.key,
                    "files": g.files,
                    "adds": g.adds,
                    "dels": g.dels,
                    "classification": by_key[g.key].classification if g.key in by_key else None,
                    "reason": by_key[g.key].reason if g.key in by_key else None,
                }
                for g in self.groups
            ],
            "noise": [
                {"group": g.key, "files": g.files, "adds": g.adds, "dels": g.dels,
                 "reason": g.noise_reason}
                for g in self.noise
            ],
            "ledger": self.ledger,
            "footer": self.footer,
        }


def _chain_config(model: str | None) -> ChainConfig:
    """A one-off single-tier chain for `model`, else the configured cascade."""
    if model:
        return ChainConfig(
            name="user-selected",
            tiers=[TierConfig(model=model, samples=1, threshold=0.0)],
        )
    return load_chains(str(CHAINS_PATH))[CHAIN_NAME]


def check_pr(
    pr: PullRequest,
    model: str | None = None,
    on_group: Callable[[FileGroup, GroupVerdict], None] | None = None,
    on_noise: Callable[[FileGroup], None] | None = None,
) -> CheckResult:
    """Judge an already-fetched PR. `on_group` fires per group, as it lands."""
    all_groups = build_groups(pr.files)
    signal, noise = split_noise(all_groups)

    if on_noise is not None:
        for group in noise:
            on_noise(group)

    ledger = Ledger()
    before = ledger_length(ledger)
    verdicts, rollup = judge(
        title=pr.title,
        body=pr.body,
        issue=pr.issue_text,
        groups=signal,
        chain_config=_chain_config(model),
        ledger=ledger,
        on_group=on_group,
    )

    return CheckResult(
        repo=pr.repo,
        number=pr.number,
        title=pr.title,
        url=pr.url,
        issue_number=pr.issue_number,
        groups=signal,
        noise=noise,
        verdicts=verdicts,
        rollup=rollup,
        ledger=ledger_stats(before=before, ledger=ledger),
    )


def check(
    ref: str,
    token: str | None = None,
    model: str | None = None,
    on_group: Callable[[FileGroup, GroupVerdict], None] | None = None,
    on_noise: Callable[[FileGroup], None] | None = None,
) -> CheckResult:
    """Fetch "owner/repo#123" from GitHub and judge it."""
    repo, number = parse_pr_ref(ref)
    pr = fetch_pull_request(repo, number, token=token)
    return check_pr(pr, model=model, on_group=on_group, on_noise=on_noise)
