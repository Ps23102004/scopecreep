"""Judge validation, with the cascade stubbed — fast, offline, no Ollama."""

from __future__ import annotations

import pytest

from scopecreep import judge as judge_mod
from scopecreep.groups import FileGroup
from scopecreep.judge import (
    GroupVerdict,
    JudgeParseError,
    classify_group,
    judge,
    rollup,
)


@pytest.fixture
def stub_cascade(monkeypatch):
    """Replace _cascade with a scripted list of raw model responses."""

    def install(*responses):
        queue = list(responses)
        calls = []

        def fake(prompt, chain_config, ledger=None):
            calls.append(prompt)
            return queue.pop(0) if queue else responses[-1]

        monkeypatch.setattr(judge_mod, "_cascade", fake)
        return calls

    return install


GROUP = FileGroup(key="src/*.py", files=["src/a.py"], adds=10, dels=2)


def test_classify_group_happy_path(stub_cascade):
    stub_cascade('{"classification": "core", "reason": "Implements the fix."}')
    verdict = classify_group("t", "b", "i", GROUP, chain_config=None)
    assert verdict == GroupVerdict("src/*.py", "core", "Implements the fix.")


def test_classify_group_strips_prose_and_fences(stub_cascade):
    stub_cascade(
        'Sure! Here is the JSON:\n```json\n'
        '{"classification": "Supporting", "reason": "Tests the change.",}\n```'
    )
    verdict = classify_group("t", "b", "i", GROUP, chain_config=None)
    assert verdict.classification == "supporting"


def test_classify_group_rejects_unknown_classification(stub_cascade):
    stub_cascade('{"classification": "maybe", "reason": "unsure"}')
    with pytest.raises(JudgeParseError, match="classification"):
        classify_group("t", "b", "i", GROUP, chain_config=None)


def test_classify_group_rejects_empty_reason(stub_cascade):
    stub_cascade('{"classification": "core", "reason": "   "}')
    with pytest.raises(JudgeParseError, match="reason"):
        classify_group("t", "b", "i", GROUP, chain_config=None)


def test_classify_group_rejects_non_json(stub_cascade):
    stub_cascade("I think this file is probably fine honestly")
    with pytest.raises(JudgeParseError, match="no JSON object"):
        classify_group("t", "b", "i", GROUP, chain_config=None)


def test_rollup_happy_path(stub_cascade):
    stub_cascade('{"verdict": "scope_creep", "drift_score": 62, "summary": "A. B."}')
    result = rollup("t", "b", "i", [GroupVerdict("src/*.py", "core", "x")], None)
    assert (result.verdict, result.drift_score) == ("scope_creep", 62)


@pytest.mark.parametrize("raw", ['"62"', "62.0"])
def test_rollup_coerces_numeric_drift_score(stub_cascade, raw):
    stub_cascade(f'{{"verdict": "in_scope", "drift_score": {raw}, "summary": "A. B."}}')
    assert rollup("t", "b", "i", [], None).drift_score == 62


@pytest.mark.parametrize("raw", ["101", "-1", "true", '"high"', "62.5"])
def test_rollup_rejects_bad_drift_score(stub_cascade, raw):
    stub_cascade(f'{{"verdict": "in_scope", "drift_score": {raw}, "summary": "A. B."}}')
    with pytest.raises(JudgeParseError, match="drift_score"):
        rollup("t", "b", "i", [], None)


def test_rollup_rejects_unknown_verdict(stub_cascade):
    stub_cascade('{"verdict": "looks_fine", "drift_score": 3, "summary": "A. B."}')
    with pytest.raises(JudgeParseError, match="verdict"):
        rollup("t", "b", "i", [], None)


def test_judge_emits_one_event_per_group_as_it_goes(stub_cascade):
    """The live dashboard depends on this: incremental, not batched."""
    stub_cascade(
        '{"classification": "core", "reason": "one"}',
        '{"classification": "unrelated", "reason": "two"}',
        '{"verdict": "minor_drift", "drift_score": 20, "summary": "A. B."}',
    )
    groups = [
        FileGroup(key="src/*.py", files=["src/a.py"], adds=1, dels=0),
        FileGroup(key="web/*.css", files=["web/a.css"], adds=1, dels=0),
    ]
    seen = []
    verdicts, result = judge("t", "b", "i", groups, chain_config=None,
                             on_group=lambda g, v: seen.append((g.key, v.classification)))

    assert seen == [("src/*.py", "core"), ("web/*.css", "unrelated")]
    assert len(verdicts) == 2
    assert result.verdict == "minor_drift"


def test_judge_skips_the_model_entirely_when_all_noise(stub_cascade):
    calls = stub_cascade('{"classification": "core", "reason": "never called"}')
    verdicts, result = judge("t", "b", "i", [], chain_config=None)
    assert calls == [], "an all-noise PR must not spend a single token"
    assert (verdicts, result.verdict, result.drift_score) == ([], "in_scope", 0)


def test_judge_reduce_prompt_names_every_group(stub_cascade):
    prompts = stub_cascade(
        '{"classification": "core", "reason": "one"}',
        '{"classification": "unrelated", "reason": "two"}',
        '{"verdict": "scope_creep", "drift_score": 70, "summary": "A. B."}',
    )
    judge(
        "t",
        "b",
        "i",
        [
            FileGroup(key="src/*.py", files=["src/a.py"], adds=1, dels=0),
            FileGroup(key="web/*.css", files=["web/a.css"], adds=1, dels=0),
        ],
        chain_config=None,
    )
    reduce_prompt = prompts[-1]
    assert "src/*.py" in reduce_prompt and "web/*.css" in reduce_prompt
    assert "classification=unrelated" in reduce_prompt
