"""Pure-Python git access (via dulwich) for the Git datasource plugin.

No `git` binary and no `subprocess` calls appear anywhere in this module -
that's the whole point of using dulwich: the plugin sandbox is not
guaranteed to have a `git` CLI available, and dulwich's SSH transport is
wired to paramiko (see ssh_vendor.py) instead of shelling out to `ssh`.
"""
from __future__ import annotations

import stat
import urllib.parse
from datetime import datetime, timezone

from dulwich.client import SSHGitClient, get_transport_and_path
from dulwich.object_store import iter_tree_contents
from dulwich.repo import MemoryRepo

from utils.ssh_vendor import build_ssh_vendor


def _is_ssh_url(url: str) -> bool:
    if url.startswith(("http://", "https://")):
        return False
    if url.startswith(("ssh://", "git+ssh://")):
        return True
    # scp-like shorthand: [user@]host:path (and not a bare local path / Windows drive letter)
    first_segment = url.split("/", 1)[0]
    return "@" in first_segment and ":" in first_segment


def _parse_ssh_url(url: str) -> tuple[str, int | None, str, str]:
    if url.startswith("git+ssh://"):
        url = "ssh://" + url[len("git+ssh://") :]
    if url.startswith("ssh://"):
        parsed = urllib.parse.urlparse(url)
        return parsed.hostname or "", parsed.port, parsed.username or "git", parsed.path
    # scp-like shorthand: [user@]host:path
    userhost, _, path = url.partition(":")
    username, _, host = userhost.rpartition("@")
    return host, None, username or "git", path


def _get_client_and_path(repo_url: str, credentials: dict):
    if _is_ssh_url(repo_url):
        host, port, username, path = _parse_ssh_url(repo_url)
        vendor = build_ssh_vendor(credentials)
        client = SSHGitClient(host=host, port=port, username=username, vendor=vendor)
        return client, path
    return get_transport_and_path(repo_url)


def _resolve_branch_ref(branch: str | None) -> bytes:
    branch = (branch or "HEAD").strip() or "HEAD"
    if branch in ("HEAD",):
        return b"HEAD"
    return f"refs/heads/{branch}".encode()


def _is_regular_file(mode: int | None) -> bool:
    return mode is not None and stat.S_ISREG(mode)


def _make_determine_wants(ref: bytes, extra_want: bytes | None = None):
    def determine_wants(refs: dict[bytes, bytes | None], depth: int | None = None, **kwargs) -> list[bytes]:
        target = refs.get(ref)
        if target is None:
            raise ValueError(f"Ref '{ref.decode(errors='replace')}' not found on remote")
        wants = [target]
        if extra_want and extra_want not in wants:
            wants.append(extra_want)
        return wants

    return determine_wants


def fetch_repo_state(repo_url: str, branch: str | None, credentials: dict) -> tuple[MemoryRepo, bytes]:
    """Shallow-fetch the target branch's tip into a fresh in-memory repo.

    Returns (repo, head_sha). A brand-new MemoryRepo is used every call -
    this plugin never keeps a persistent local clone across invocations, and
    since it never diffs against a remembered older commit, only the
    current tip is ever needed (depth=1).
    """
    client, path = _get_client_and_path(repo_url, credentials)
    mem_repo = MemoryRepo()
    ref = _resolve_branch_ref(branch)
    result = client.fetch(path, mem_repo, determine_wants=_make_determine_wants(ref), depth=1)
    head_sha = result.refs.get(ref)
    if head_sha is None:
        raise ValueError(f"Could not resolve branch/ref '{branch or 'HEAD'}' on {repo_url}")
    return mem_repo, head_sha


def list_tree_files(repo: MemoryRepo, tree_sha: bytes) -> list[tuple[str, str]]:
    """Return (path, blob_sha_hex) for every regular file in the tree.

    The blob SHA is git's own content hash for that file - identical bytes
    always produce the same blob SHA regardless of which commit touched it,
    so it's used elsewhere as a content-version fingerprint that only
    changes when a file's actual content changes (unlike the commit SHA of
    HEAD, which changes on every commit anywhere in the repo).
    """
    entries = []
    for entry in iter_tree_contents(repo.object_store, tree_sha):
        if _is_regular_file(entry.mode):
            entries.append((entry.path.decode("utf-8", errors="replace"), entry.sha.decode()))
    return entries


def commit_time_iso(repo: MemoryRepo, commit_sha: bytes) -> str:
    commit = repo[commit_sha]
    dt = datetime.fromtimestamp(commit.commit_time, tz=timezone.utc)
    return dt.isoformat()


def read_blob_at_commit(repo_url: str, commit_sha: str, path: str, credentials: dict) -> bytes:
    """Independently fetch a single file's content at a specific commit.

    Deliberately self-contained: `_get_pages` and `_get_content` may run in
    separate stateless plugin invocations with no shared state, so this does
    its own shallow fetch instead of relying on an earlier one having run.
    """
    client, repo_path = _get_client_and_path(repo_url, credentials)
    target_sha = commit_sha.encode()

    def determine_wants_exact(refs, depth: int | None = None, **kwargs) -> list[bytes]:
        return [target_sha]

    mem_repo = MemoryRepo()
    try:
        client.fetch(repo_path, mem_repo, determine_wants=determine_wants_exact, depth=1)
        head_sha = target_sha
        if head_sha not in mem_repo.object_store:
            raise ValueError("server did not return the requested commit")
    except Exception:
        # Some servers reject fetching a non-tip SHA directly (no
        # uploadpack.allowReachableSHA1InWant). Fall back to the branch
        # tip; this only differs from the exact commit if new commits
        # landed on the branch between listing and this content fetch.
        client, repo_path = _get_client_and_path(repo_url, credentials)
        mem_repo = MemoryRepo()
        result = client.fetch(repo_path, mem_repo, determine_wants=_make_determine_wants(b"HEAD"), depth=1)
        head_sha = result.refs.get(b"HEAD")
        if head_sha is None:
            raise ValueError(f"Could not fetch commit {commit_sha} or branch tip from {repo_url}")

    commit = mem_repo[head_sha]
    tree = mem_repo[commit.tree]
    try:
        mode, blob_sha = tree.lookup_path(mem_repo.object_store.__getitem__, path.encode())
    except Exception as e:
        raise ValueError(f"Path '{path}' not found at commit {head_sha.decode()}") from e
    if not _is_regular_file(mode):
        raise ValueError(f"Path '{path}' is not a regular file")
    return mem_repo[blob_sha].data
