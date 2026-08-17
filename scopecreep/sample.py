"""Keep README.md's example block honest.

`scopecreep readme-sample` reruns a real check and pastes the real output
between the sample markers, so the example in the README can never drift from
what the tool actually prints. Offline (or if GitHub says no), it falls back to
a bundled fixture rather than leaving a stale block in place.
"""

from __future__ import annotations

import json
from pathlib import Path

from scopecreep.github import PullRequest, parse_pr_ref

START = "<!-- sample:start -->"
END = "<!-- sample:end -->"

# The PR the README shows off. Swap this for a PR in this repo once one exists.
PINNED_PR = "acme/billing#48"

FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "creep_pr.json"

__all__ = ["START", "END", "PINNED_PR", "load_fixture_pr", "splice", "refresh_readme"]


def load_fixture_pr(path: Path = FIXTURE, repo: str = "acme/billing") -> PullRequest:
    """Build a PullRequest from a canned GitHub payload — no network."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pr_data, issue = data["pr"], data.get("issue")
    pr = PullRequest(
        repo=repo,
        number=pr_data["number"],
        title=pr_data["title"],
        body=pr_data.get("body") or "",
        url=pr_data.get("html_url") or "",
        files=data["files"],
    )
    if issue:
        pr.issue_number = issue["number"]
        pr.issue_title = issue.get("title") or ""
        pr.issue_body = issue.get("body") or ""
    return pr


def splice(readme_text: str, sample: str) -> str:
    """Replace whatever sits between the markers with `sample`."""
    start = readme_text.find(START)
    end = readme_text.find(END)
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"README is missing the {START} / {END} markers")
    head = readme_text[: start + len(START)]
    tail = readme_text[end:]
    return f"{head}\n\n```\n{sample.rstrip()}\n```\n\n{tail}"


def refresh_readme(readme: Path, pr: str | None = None) -> tuple[Path, str]:
    """Rewrite the README's sample block. Returns (path, what-we-sampled).

    Tries the real PR first; falls back to the bundled fixture whenever GitHub
    or the network is unavailable, so this command works on a plane.
    """
    from scopecreep.check import check, check_pr
    from scopecreep.report import render_table

    target = pr or PINNED_PR
    try:
        result = check(target)
        source = target
    except Exception as exc:  # noqa: BLE001 - any fetch failure means fall back
        repo, _ = parse_pr_ref(target) if "#" in target else ("acme/billing", 0)
        result = check_pr(load_fixture_pr(repo=repo))
        source = f"fixture (live check failed: {type(exc).__name__})"

    readme = Path(readme)
    readme.write_text(
        splice(readme.read_text(encoding="utf-8"), render_table(result)),
        encoding="utf-8",
    )
    return readme, source
