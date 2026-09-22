"""Higher-level "what still needs syncing / what's now superseded" logic
for the optional "Verify against existing knowledge base documents" and
"Archive superseded versions" features.

Ties together `dify_dataset_api` (raw Dataset API calls) and `naming` (the
content-hash tag filename scheme) so `datasources/git.py` only has to call
two plain functions instead of juggling both concerns itself.
"""
from __future__ import annotations

from utils.dify_dataset_api import list_existing_documents
from utils.naming import page_name, parse_tagged_name


def filter_unsynced_paths(
    api_base_url: str,
    api_key: str,
    dataset_id: str,
    path_blob_shas: dict[str, str],
    tag_with_commit: bool,
) -> list[str]:
    """Drop paths whose current-content version is already a real document
    in the target Knowledge Base, per Dify's own Dataset API - the ground
    truth, unaffected by a pipeline preview/test-run (which never creates a
    real document).

    `path_blob_shas` maps each path to its own git blob SHA (content hash) -
    each file is checked against its OWN version tag, not a single shared
    commit SHA, so a file whose content hasn't changed is never mistaken for
    "unsynced" just because some other file in the repo got a new commit.
    """
    existing_documents = list_existing_documents(api_base_url, api_key, dataset_id)
    return [
        p for p, blob_sha in path_blob_shas.items() if page_name(p, blob_sha, tag_with_commit) not in existing_documents
    ]


def find_superseded_document_ids(
    api_base_url: str, api_key: str, dataset_id: str, path: str, current_blob_sha: str
) -> list[str]:
    """IDs of existing documents for `path` that are NOT the given content
    version - i.e. older versions that are now safe to archive, since a
    document for `current_blob_sha` is confirmed being ingested right now
    (see the caller in `datasources/git.py::_get_content` for why this is
    only safe to call from there, not from `_get_pages`).
    """
    current_name = page_name(path, current_blob_sha, tag_with_commit=True)
    existing_documents = list_existing_documents(api_base_url, api_key, dataset_id)
    return [
        doc_id
        for name, doc_id in existing_documents.items()
        if name != current_name and parse_tagged_name(name) == path
    ]
