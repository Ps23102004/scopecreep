"""Deterministic pre-pass: group a PR's changed files and tag the noise.

Pure functions — no LLM, no network, no I/O. Everything here runs before a
single token is spent, so the judge only ever sees file groups that could
plausibly say something about scope.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

__all__ = [
    "FileGroup",
    "top_level_dir",
    "group_key",
    "is_lockfile",
    "is_generated",
    "is_formatting_only",
    "build_groups",
    "split_noise",
]


# Basenames treated as lockfiles (case-insensitive). requirements.txt is
# deliberately excluded: it is hand-edited, so a change to it is real signal.
_LOCKFILE_BASENAMES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pipfile.lock",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
        "uv.lock",
        "bun.lockb",
    }
)

# Path segments that mark a vendored or build-output tree.
_GENERATED_SEGMENTS = frozenset(
    {
        "node_modules",
        "vendor",
        "dist",
        "build",
        ".next",
        "__pycache__",
        "target",
        "out",
        "coverage",
        ".venv",
        "venv",
        "migrations",
    }
)

_GENERATED_SUFFIXES = (".min.js", ".min.css", ".map", ".pb.go", "_pb2.py", ".snap")


@dataclass
class FileGroup:
    """Changed files sharing a (top-level dir, extension) key."""

    key: str
    files: list[str] = field(default_factory=list)
    adds: int = 0
    dels: int = 0
    noise: bool = False
    noise_reason: str | None = None


def _segments(path: str) -> list[str]:
    """Split a path into segments, dropping '.' and empty components."""
    return [seg for seg in path.replace("\\", "/").split("/") if seg and seg != "."]


def top_level_dir(path: str) -> str:
    """First path segment, or "" for a root-level file.

    "src/app/main.py" -> "src";  "README.md" -> "";  "./src/x.py" -> "src".
    """
    segs = _segments(path)
    return segs[0] if len(segs) > 1 else ""


def group_key(path: str) -> str:
    """Group key for a path: "src/*.py", "*.md", "scripts/*", "*"."""
    segs = _segments(path)
    base = segs[-1] if segs else ""
    prefix = top_level_dir(path)
    dot = base.rfind(".")
    # dot > 0 so a leading-dot file (.gitignore) counts as extensionless.
    ext = base[dot:].lower() if dot > 0 else ""
    return f"{prefix}/*{ext}" if prefix else f"*{ext}"


def is_lockfile(path: str) -> bool:
    """True for well-known dependency lockfiles, matched by basename."""
    segs = _segments(path)
    base = (segs[-1] if segs else path).lower()
    return base in _LOCKFILE_BASENAMES or base.endswith(".lock")


def is_generated(path: str) -> bool:
    """True for vendored, build-output, or codegen'd paths."""
    segs = _segments(path)
    if any(seg in _GENERATED_SEGMENTS for seg in segs[:-1]):
        return True
    base = (segs[-1] if segs else path).lower()
    return base.endswith(_GENERATED_SUFFIXES) or ".generated." in base


def is_formatting_only(patch: str | None) -> bool:
    """True when a unified diff only moves whitespace around.

    Added and removed lines are compared as multisets of their stripped,
    non-empty content: if they match exactly, nothing semantic changed.
    Catches reindents and reflows; deliberately misses same-line whitespace
    edits inside a line, which is fine — those still read as a real change.
    """
    if not patch:
        return False
    added: Counter[str] = Counter()
    removed: Counter[str] = Counter()
    for line in patch.splitlines():
        if not line or line.startswith(("+++", "---", "@@")):
            continue
        if line[0] == "+":
            bucket = added
        elif line[0] == "-":
            bucket = removed
        else:
            continue  # context line
        content = line[1:].strip()
        if content:
            bucket[content] += 1
    return bool(added) and added == removed


def build_groups(files: list[dict]) -> list[FileGroup]:
    """Turn GitHub's PR-files payload into churn-ranked, noise-tagged groups.

    `files` entries carry at least `filename`, `additions`, `deletions`, and
    optionally `patch`. Groups are sorted by churn descending, then key.

    A group is noise only when *every* file in it is noise, so one real source
    file living in a directory of generated ones still reaches the judge.
    """
    buckets: dict[str, FileGroup] = {}
    patches: dict[str, list[str | None]] = {}

    for entry in files:
        filename = entry.get("filename", "")
        key = group_key(filename)
        group = buckets.setdefault(key, FileGroup(key=key))
        group.files.append(filename)
        group.adds += int(entry.get("additions") or 0)
        group.dels += int(entry.get("deletions") or 0)
        patches.setdefault(key, []).append(entry.get("patch"))

    for key, group in buckets.items():
        group.files.sort()
        group_patches = patches[key]
        if all(is_lockfile(p) for p in group.files):
            group.noise, group.noise_reason = True, "lockfile"
        elif all(is_generated(p) for p in group.files):
            group.noise, group.noise_reason = True, "generated"
        elif all(is_formatting_only(p) for p in group_patches):
            # is_formatting_only(None) is False, so a group holding a binary or
            # truncated diff can never be written off as reformatting.
            group.noise, group.noise_reason = True, "formatting-only"

    return sorted(buckets.values(), key=lambda g: (-(g.adds + g.dels), g.key))


def split_noise(groups: list[FileGroup]) -> tuple[list[FileGroup], list[FileGroup]]:
    """Split into (signal, noise), preserving order."""
    return [g for g in groups if not g.noise], [g for g in groups if g.noise]
