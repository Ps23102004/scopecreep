"""groups.py is pure, so these are the cheap tests that catch the most."""

from __future__ import annotations

import pytest

from scopecreep.groups import (
    build_groups,
    group_key,
    is_formatting_only,
    is_generated,
    is_lockfile,
    split_noise,
    top_level_dir,
)


@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/app/main.py", "src"),
        ("README.md", ""),
        ("./src/x.py", "src"),
        ("Dockerfile", ""),
    ],
)
def test_top_level_dir(path, expected):
    assert top_level_dir(path) == expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("src/app/main.py", "src/*.py"),
        ("README.md", "*.md"),
        ("Dockerfile", "*"),
        ("scripts/deploy", "scripts/*"),
        ("src/App.TSX", "src/*.tsx"),
        (".gitignore", "*"),
    ],
)
def test_group_key(path, expected):
    assert group_key(path) == expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("package-lock.json", True),
        ("PNPM-lock.yaml", True),
        ("sub/dir/poetry.lock", True),
        ("something.lock", True),
        ("requirements.txt", False),
        ("src/main.py", False),
    ],
)
def test_is_lockfile(path, expected):
    assert is_lockfile(path) is expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("node_modules/lodash/index.js", True),
        ("dist/bundle.js", True),
        ("src/app.min.js", True),
        ("api/service_pb2.py", True),
        ("db/migrations/0001_init.py", True),
        ("src/main.py", False),
        # A file merely *named* "build" is source, not a build directory.
        ("scripts/build", False),
    ],
)
def test_is_generated(path, expected):
    assert is_generated(path) is expected


def test_is_formatting_only_reindent():
    assert is_formatting_only("@@ -1,2 +1,2 @@\n-  foo(x)\n+foo(x)\n") is True


def test_is_formatting_only_rejects_real_change():
    assert is_formatting_only("@@ -1,1 +1,1 @@\n-foo(x)\n+bar(x)\n") is False


def test_is_formatting_only_rejects_missing_patch():
    assert is_formatting_only(None) is False
    assert is_formatting_only("") is False


def test_is_formatting_only_rejects_pure_addition():
    """New lines with nothing removed is a real change, not a reformat."""
    assert is_formatting_only("@@ -0,0 +1,1 @@\n+new_line()\n") is False


def test_build_groups_sums_and_sorts(clean_pr):
    groups = {g.key: g for g in build_groups(clean_pr["files"])}
    assert groups["src/*.py"].adds == 27
    assert groups["src/*.py"].dels == 10
    assert groups["src/*.py"].files == ["src/billing/models.py", "src/billing/due_dates.py"] or (
        groups["src/*.py"].files == sorted(groups["src/*.py"].files)
    )
    # churn-descending: the lockfile has the most churn by far
    assert build_groups(clean_pr["files"])[0].key == "*.lock"


def test_build_groups_tags_noise(clean_pr):
    groups = {g.key: g for g in build_groups(clean_pr["files"])}
    assert groups["*.lock"].noise and groups["*.lock"].noise_reason == "lockfile"
    assert groups["dist/*.js"].noise and groups["dist/*.js"].noise_reason == "generated"
    assert not groups["src/*.py"].noise
    assert not groups["tests/*.py"].noise


def test_build_groups_tags_formatting_only(creep_pr):
    groups = {g.key: g for g in build_groups(creep_pr["files"])}
    assert groups["src/*.py"].noise is False, "a real code change must survive"
    # src/utils/format.py is a pure reindent but shares a group with real
    # changes, so the group as a whole is still signal.
    only_format = build_groups([f for f in creep_pr["files"] if "format.py" in f["filename"]])
    assert only_format[0].noise and only_format[0].noise_reason == "formatting-only"


def test_group_of_mixed_files_is_not_noise():
    """One hand-written file keeps a generated-looking group in play."""
    groups = build_groups(
        [
            {"filename": "src/vendor.min.js", "additions": 5, "deletions": 5},
            {"filename": "src/handwritten.js", "additions": 2, "deletions": 0},
        ]
    )
    assert len(groups) == 1 and groups[0].noise is False


def test_split_noise(noise_only_pr):
    signal, noise = split_noise(build_groups(noise_only_pr["files"]))
    assert signal == []
    assert {g.noise_reason for g in noise} == {"lockfile", "generated"}


def test_build_groups_tolerates_missing_counts():
    groups = build_groups([{"filename": "src/a.py"}])
    assert groups[0].adds == 0 and groups[0].dels == 0
