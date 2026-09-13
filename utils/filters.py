"""Extension/path filtering for files listed from a git tree."""
from __future__ import annotations

import fnmatch


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def matches_extension(path: str, extensions: list[str]) -> bool:
    if not extensions:
        return True
    lowered = path.lower()
    return any(lowered.endswith(ext.lower()) for ext in extensions)


def matches_path(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            if fnmatch.fnmatch(path, pattern):
                return True
        else:
            prefix = pattern.strip("/")
            if path == prefix or path.startswith(prefix + "/"):
                return True
    return False


def filter_paths(paths: list[str], extensions: list[str], path_patterns: list[str]) -> list[str]:
    return [p for p in paths if matches_extension(p, extensions) and matches_path(p, path_patterns)]
