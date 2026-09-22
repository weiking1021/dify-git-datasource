"""Optional integration with Dify's own Dataset REST API.

Used only when "Verify against existing knowledge base documents" is
enabled. It queries the dataset this pipeline feeds into directly, to
check which files have already been ingested as real documents, and
(optionally) to archive superseded versions of a file once a newer content
version is confirmed present. Unlike the plugin's own session.storage-based
tracking, this is immune to the preview button/wizard triggering a false
"already synced" state, since previewing a file does not create a real
document in the dataset - only genuine ingestion does.

Requires a Dify API key + base URL (provider credentials) and the target
dataset's ID (datasource parameter) - none of which the plugin can discover
automatically. Dify gives datasource plugins no way to learn which dataset
they're feeding into, and (as of the plugin SDK/daemon version this was
built against) no backwards-invocation channel exists for a plugin to query
or manage dataset documents without going through this public REST API and
its own API key. See PRIVACY.md/README.md for this trade-off.
"""
from __future__ import annotations

import requests


def list_existing_documents(base_url: str, api_key: str, dataset_id: str) -> dict[str, str]:
    """Return {document_name: document_id} for every document in the dataset, paginated."""
    documents: dict[str, str] = {}
    page = 1
    while True:
        response = requests.get(
            f"{base_url.rstrip('/')}/v1/datasets/{dataset_id}/documents",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"page": page, "limit": 100},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        for doc in payload.get("data", []):
            doc_id, name = doc.get("id"), doc.get("name")
            if name and doc_id:
                documents[name] = doc_id
        if not payload.get("has_more"):
            break
        page += 1
    return documents


def archive_documents(base_url: str, api_key: str, dataset_id: str, document_ids: list[str]) -> None:
    """Archive (soft-disable, not delete) the given documents in the dataset."""
    if not document_ids:
        return
    response = requests.patch(
        f"{base_url.rstrip('/')}/v1/datasets/{dataset_id}/documents/status/archive",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"document_ids": document_ids},
        timeout=15,
    )
    response.raise_for_status()
