"""Fetch everything ScopeCreep needs to judge a PR from the GitHub REST API.

Thin wrapper, same shape as llm-ladder's github_client: 404 and 403 become
ValueError with an actionable message, everything else raises HTTPError.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import requests

GITHUB_API_BASE = "https://api.github.com/repos"

# "closes #12", "Fixes: #34", "resolved https://github.com/o/r/issues/56"
_CLOSES_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s*"
    r"(?:https?://github\.com/[\w.-]+/[\w.-]+/issues/|#)(\d+)",
    re.IGNORECASE,
)

# "owner/repo#123", also tolerating a full PR URL.
_PR_REF_RE = re.compile(
    r"^(?:https?://github\.com/)?([\w.-]+/[\w.-]+?)(?:/pull/|#)(\d+)/?$"
)


@dataclass
class PullRequest:
    repo: str
    number: int
    title: str
    body: str
    url: str
    files: list[dict] = field(default_factory=list)
    issue_number: int | None = None
    issue_title: str = ""
    issue_body: str = ""

    @property
    def issue_text(self) -> str:
        """The linked issue as one blob, or "" when the PR links no issue."""
        if self.issue_number is None:
            return ""
        return f"#{self.issue_number} {self.issue_title}\n{self.issue_body}".strip()


def parse_pr_ref(ref: str) -> tuple[str, int]:
    """Parse "owner/repo#123" (or a PR URL) into ("owner/repo", 123)."""
    match = _PR_REF_RE.match(ref.strip())
    if match is None:
        raise ValueError(f"expected owner/repo#123, got: {ref!r}")
    return match.group(1), int(match.group(2))


def parse_linked_issue(body: str) -> int | None:
    """Return the first issue number the PR body says it closes, if any."""
    match = _CLOSES_RE.search(body or "")
    return int(match.group(1)) if match else None


def _get(url: str, token: str | None, params: dict | None = None):
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    response = requests.get(url, headers=headers, params=params, timeout=15)
    if response.status_code == 404:
        raise ValueError(f"not found: {url}")
    if response.status_code == 403:
        raise ValueError("GitHub API rate limit exceeded, set GITHUB_TOKEN")
    response.raise_for_status()
    return response


def fetch_pull_request(
    repo: str, number: int, token: str | None = None, max_files: int = 300
) -> PullRequest:
    """Fetch a PR's title/body, its linked issue, and its changed files.

    `token` defaults to $GITHUB_TOKEN. Raises ValueError on 404/403.
    """
    if token is None:
        token = os.environ.get("GITHUB_TOKEN")

    data = _get(f"{GITHUB_API_BASE}/{repo}/pulls/{number}", token).json()
    body = data.get("body") or ""

    pr = PullRequest(
        repo=repo,
        number=number,
        title=data.get("title") or "",
        body=body,
        url=data.get("html_url") or "",
        issue_number=parse_linked_issue(body),
    )

    if pr.issue_number is not None:
        try:
            issue = _get(
                f"{GITHUB_API_BASE}/{repo}/issues/{pr.issue_number}", token
            ).json()
        except ValueError:
            # A dangling "closes #N" shouldn't sink the whole review — the PR
            # title alone is still enough to judge against.
            pr.issue_number = None
        else:
            pr.issue_title = issue.get("title") or ""
            pr.issue_body = issue.get("body") or ""

    pr.files = _fetch_files(repo, number, token, max_files)
    return pr


def _fetch_files(repo: str, number: int, token: str | None, max_files: int) -> list[dict]:
    """Page through /pulls/{n}/files until exhausted or max_files reached."""
    files: list[dict] = []
    page = 1
    while len(files) < max_files:
        batch = _get(
            f"{GITHUB_API_BASE}/{repo}/pulls/{number}/files",
            token,
            params={"per_page": 100, "page": page},
        ).json()
        if not batch:
            break
        files.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return files[:max_files]
