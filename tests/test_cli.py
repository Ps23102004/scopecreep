"""CLI, github parsing, and the ledger footer — all stubbed, all offline."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from scopecreep import check as check_mod
from scopecreep import judge as judge_mod
from scopecreep.cli import app
from scopecreep.github import parse_linked_issue, parse_pr_ref
from scopecreep.ledger_footer import footer_line
from scopecreep.sample import START, END, load_fixture_pr, splice

runner = CliRunner()


@pytest.fixture
def offline_check(monkeypatch):
    """Stub the cascade and GitHub so `scopecreep check` runs with no network."""
    # creep_pr yields exactly 4 signal groups (src/*.py, tests/*.py,
    # marketing/*.html, marketing/*.css) plus one rollup call.
    responses = [
        '{"classification": "core", "reason": "Implements the retry."}',
        '{"classification": "supporting", "reason": "Tests the retry."}',
        '{"classification": "unrelated", "reason": "New marketing page."}',
        '{"classification": "unrelated", "reason": "New marketing styles."}',
        '{"verdict": "scope_creep", "drift_score": 71, '
        '"summary": "Adds webhook retry. Also ships a pricing page and an auth swap."}',
    ]
    queue = list(responses)
    monkeypatch.setattr(
        judge_mod, "_cascade", lambda p, c, ledger=None: queue.pop(0) if queue else responses[-1]
    )
    monkeypatch.setattr(
        check_mod, "fetch_pull_request", lambda repo, n, token=None: load_fixture_pr(repo=repo)
    )


# -- github ref parsing ------------------------------------------------------


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("acme/billing#48", ("acme/billing", 48)),
        ("https://github.com/acme/billing/pull/48", ("acme/billing", 48)),
        ("acme/billing/pull/48", ("acme/billing", 48)),
    ],
)
def test_parse_pr_ref(ref, expected):
    assert parse_pr_ref(ref) == expected


def test_parse_pr_ref_rejects_junk():
    with pytest.raises(ValueError, match="owner/repo#123"):
        parse_pr_ref("just-a-repo")


@pytest.mark.parametrize(
    "body,expected",
    [
        ("Closes #11", 11),
        ("fixes: #2049", 2049),
        ("Resolved https://github.com/o/r/issues/7", 7),
        ("Mentions #5 but closes nothing", None),
        ("", None),
    ],
)
def test_parse_linked_issue(body, expected):
    assert parse_linked_issue(body) == expected


# -- ledger footer -----------------------------------------------------------


def test_footer_line_reports_real_numbers():
    line = footer_line({"calls": 6, "tier0_held": 6, "escalations": 0})
    assert "6 cascade calls" in line and "tier 0 held 6/6" in line and "$0.00" in line


def test_footer_line_with_no_calls():
    assert "without a single model call" in footer_line({"calls": 0, "tier0_held": 0})


def test_ledger_stats_counts_only_this_run(tmp_path):
    """Numbers come off the real ledger file, and only this run's slice of it."""
    from llm_ladder.ledger import Ledger
    from scopecreep.ledger_footer import ledger_length, ledger_stats

    ledger = Ledger(path=str(tmp_path / "ledger.jsonl"))
    ledger.record("judge", 0, "m", 1.0, 3, False, False, 0.1)  # a previous run
    before = ledger_length(ledger)

    ledger.record("judge", 0, "m", 1.0, 3, False, False, 0.1)
    ledger.record("judge", 1, "big", 0.9, 3, True, True, 0.4)

    stats = ledger_stats(before=before, ledger=ledger)
    assert stats == {"calls": 2, "tier0_held": 1, "escalations": 1}
    assert "2 cascade calls" in footer_line(stats)


def test_ledger_stats_survives_a_corrupt_line(tmp_path):
    from llm_ladder.ledger import Ledger
    from scopecreep.ledger_footer import ledger_stats

    path = tmp_path / "ledger.jsonl"
    path.write_text('{"tier_index": 0, "escalated": false}\nnot json at all\n')
    assert ledger_stats(ledger=Ledger(path=str(path)))["calls"] == 1


# -- readme sample -----------------------------------------------------------


def test_splice_replaces_only_between_markers():
    out = splice(f"before\n{START}\nold\n{END}\nafter", "NEW OUTPUT")
    assert "old" not in out and "NEW OUTPUT" in out
    assert out.startswith("before") and out.rstrip().endswith("after")


def test_splice_requires_markers():
    with pytest.raises(ValueError, match="markers"):
        splice("no markers here", "x")


def test_load_fixture_pr_carries_the_linked_issue():
    pr = load_fixture_pr()
    assert pr.number == 48 and pr.issue_number == 47
    assert "give up after one failure" in pr.issue_text


# -- cli ---------------------------------------------------------------------


def test_check_prints_a_table(offline_check):
    result = runner.invoke(app, ["check", "acme/billing#48"])
    assert result.exit_code == 0, result.output
    assert "SCOPE CREEP" in result.output
    assert "drift score 71/100" in result.output
    assert "excluded: lockfile" in result.output
    # The stub never reaches the cascade, so nothing lands in the ledger and the
    # footer says so — which is the point: the numbers are read, not asserted.
    assert "$0.00 API spend" in result.output
    assert "without a single model call" in result.output


def test_check_json_matches_the_api_contract(offline_check):
    result = runner.invoke(app, ["check", "acme/billing#48", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["verdict"] == "scope_creep"
    assert set(data["ledger"]) == {"calls", "tier0_held", "escalations"}
    assert all(g["classification"] in {"core", "supporting", "unrelated"} for g in data["groups"])


def test_check_md_writes_a_file(offline_check, tmp_path):
    out = tmp_path / "review.md"
    result = runner.invoke(app, ["check", "acme/billing#48", "--md", "--out", str(out)])
    assert result.exit_code == 0, result.output
    text = out.read_text()
    assert text.startswith("## ScopeCreep — acme/billing#48")
    assert "| Group | Churn | Classification | Reason |" in text


def test_check_reports_a_bad_ref_without_a_traceback(monkeypatch):
    result = runner.invoke(app, ["check", "not-a-pr"])
    assert result.exit_code == 1
    assert "error:" in result.output
