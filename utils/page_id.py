"""Encoding/decoding for this datasource's opaque `page_id`.

`_get_pages` and `_get_content` may run in entirely separate stateless
plugin invocations with no shared state and no metadata side-channel
(`OnlineDocumentPage` has no metadata field) - so every piece of information
`_get_content` (or the archive-superseded-versions step) needs has to be
packed into the page_id itself.
"""
from __future__ import annotations

import base64
import json
from typing import Any


def encode_page_id(
    repo_url: str,
    branch: str,
    sha: str,
    path: str,
    dataset_id: str,
    archive_superseded: bool,
    blob_sha: str,
) -> str:
    payload = json.dumps(
        {
            "repo_url": repo_url,
            "branch": branch,
            "sha": sha,
            "path": path,
            "dataset_id": dataset_id,
            "archive_superseded": archive_superseded,
            "blob_sha": blob_sha,
        }
    )
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_page_id(page_id: str) -> dict[str, Any]:
    payload = base64.urlsafe_b64decode(page_id.encode()).decode()
    return json.loads(payload)
