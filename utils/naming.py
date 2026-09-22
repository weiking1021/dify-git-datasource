"""Display naming for workspaces/pages, and the content-hash tag filename scheme.

The content-hash tag scheme (`page_name`/`parse_tagged_name`) is what lets
"Verify against existing knowledge base documents" tell apart different
versions of the same file, since the Dify Dataset API only exposes document
names - see utils/dataset_sync.py for how these get used together.
"""
from __future__ import annotations

import hashlib
import re

_TAGGED_NAME_RE = re.compile(r"^(?P<stem>.*)@(?P<tag>[0-9a-f]{12})(?P<ext>\.[^./]*)?$")


def workspace_id(repo_url: str, branch: str) -> str:
    return hashlib.sha256(f"{repo_url}|{branch}".encode()).hexdigest()[:16]


def repo_display_name(repo_url: str) -> str:
    name = repo_url.rstrip("/").rsplit("/", 1)[-1]
    return name[: -len(".git")] if name.endswith(".git") else name or repo_url


def page_name(path: str, version_tag: str, tag_with_commit: bool) -> str:
    """`version_tag` is expected to be a file's git blob SHA (content hash) -
    see utils/git_client.py:list_tree_files - not a commit SHA, so the tag
    only changes when this file's own content changes, not on every commit
    anywhere else in the repo.
    """
    if not tag_with_commit:
        return path
    # Insert the tag before the extension, in the file's own name only (not
    # the directory prefix), so a naive extension-based type check (e.g.
    # ".md") still sees the real extension: "docs/guide.md" becomes
    # "docs/guide@<tag>.md", not "docs/guide.md@<tag>".
    directory, _, filename = path.rpartition("/")
    stem, dot, ext = filename.rpartition(".")
    tagged = f"{stem}@{version_tag[:12]}.{ext}" if dot else f"{filename}@{version_tag[:12]}"
    return f"{directory}/{tagged}" if directory else tagged


def parse_tagged_name(name: str) -> str | None:
    """Reverse of `page_name`: returns the untagged base path if `name` is
    commit-tagged (e.g. "docs/guide@a1b2c3d4e5f6.md" -> "docs/guide.md"),
    else None.
    """
    directory, _, filename = name.rpartition("/")
    match = _TAGGED_NAME_RE.match(filename)
    if not match:
        return None
    base_filename = match.group("stem") + (match.group("ext") or "")
    return f"{directory}/{base_filename}" if directory else base_filename
