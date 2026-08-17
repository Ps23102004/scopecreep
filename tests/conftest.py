from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def clean_pr() -> dict:
    return load("clean_pr")


@pytest.fixture
def creep_pr() -> dict:
    return load("creep_pr")


@pytest.fixture
def noise_only_pr() -> dict:
    return load("noise_only_pr")


@pytest.fixture
def isolated_ledger(tmp_path, monkeypatch):
    """Point llm-ladder's Ledger at a temp file so tests never touch ~/."""
    path = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("HOME", str(tmp_path))
    return path
