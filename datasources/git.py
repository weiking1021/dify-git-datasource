from collections.abc import Generator
from typing import Any

from dify_plugin.entities.datasource import (
    DatasourceGetPagesResponse,
    DatasourceMessage,
    GetOnlineDocumentPageContentRequest,
    OnlineDocumentInfo,
)
from dify_plugin.interfaces.datasource.online_document import OnlineDocumentDatasource

from utils.dataset_sync import filter_unsynced_paths, find_superseded_document_ids
from utils.dify_dataset_api import archive_documents
from utils.filters import filter_paths, parse_csv
from utils.git_client import commit_time_iso, fetch_repo_state, list_tree_files, read_blob_at_commit
from utils.naming import page_name, repo_display_name, workspace_id
from utils.page_id import decode_page_id, encode_page_id

# Fallback when the "Dify API base URL" provider credential is left blank.
# A schema-declared `default:` in provider/git.yaml only pre-fills the
# credential *form field* - it is not applied to what the plugin actually
# receives if the user saves the credential without touching that field, so
# the fallback has to be enforced here in code too (confirmed by observing
# the same gap for datasource-parameter defaults like extension_filter).
_DEFAULT_DIFY_API_BASE_URL = "http://api:5001"


class GitDataSource(OnlineDocumentDatasource):
    def _get_pages(self, datasource_parameters: dict[str, Any]) -> DatasourceGetPagesResponse:
        credentials = dict(self.runtime.credentials)
        repo_url = datasource_parameters["repo_url"]
        branch = datasource_parameters.get("branch") or "HEAD"
        extension_filter = datasource_parameters.get("extension_filter") or ""
        path_filter = datasource_parameters.get("path_filter") or ""
        use_dataset_verification = bool(datasource_parameters.get("use_dataset_verification", False))
        tag_with_commit = bool(datasource_parameters.get("tag_filename_with_commit", False))
        archive_superseded = bool(datasource_parameters.get("archive_superseded_versions", False))
        dataset_id = datasource_parameters.get("dataset_id") or ""

        if archive_superseded and not (use_dataset_verification and tag_with_commit):
            raise ValueError(
                "'Archive superseded versions' requires both 'Verify against existing knowledge base "
                "documents' and 'Append content hash to file names' to also be enabled."
            )

        extensions = parse_csv(extension_filter)
        path_patterns = parse_csv(path_filter)

        # Standard behavior: list whatever git currently has, every time.
        # No state is tracked anywhere, so there's nothing that can get out
        # of sync with reality (unlike a plugin-side "last synced" marker,
        # which a mere pipeline preview/test-run could silently corrupt).
        repo, head_sha = fetch_repo_state(repo_url, branch, credentials)
        path_blob_shas = dict(list_tree_files(repo, repo[head_sha].tree))
        matched_paths = filter_paths(list(path_blob_shas), extensions, path_patterns)
        head_sha_str = head_sha.decode()

        if use_dataset_verification:
            api_base_url = credentials.get("dify_api_base_url") or _DEFAULT_DIFY_API_BASE_URL
            api_key = credentials.get("dify_api_key")
            if not api_key or not dataset_id:
                raise ValueError(
                    "'Verify against existing knowledge base documents' is enabled but "
                    "dify_api_key (provider credentials) or dataset_id (this parameter) is missing."
                )
            matched_path_blob_shas = {p: path_blob_shas[p] for p in matched_paths}
            matched_paths = filter_unsynced_paths(
                api_base_url, api_key, dataset_id, matched_path_blob_shas, tag_with_commit
            )

        last_edited_time = commit_time_iso(repo, head_sha)

        pages = [
            {
                "page_id": encode_page_id(
                    repo_url, branch, head_sha_str, path, dataset_id, archive_superseded, path_blob_shas[path]
                ),
                "page_name": page_name(path, path_blob_shas[path], tag_with_commit),
                "type": "file",
                "last_edited_time": last_edited_time,
            }
            for path in matched_paths
        ]

        online_document_info = OnlineDocumentInfo(
            workspace_id=workspace_id(repo_url, branch),
            workspace_name=f"{repo_display_name(repo_url)}@{branch}",
            workspace_icon="",
            pages=pages,
            total=len(pages),
        )
        return DatasourceGetPagesResponse(result=[online_document_info])

    def _get_content(self, page: GetOnlineDocumentPageContentRequest) -> Generator[DatasourceMessage, None, None]:
        credentials = dict(self.runtime.credentials)
        info = decode_page_id(page.page_id)
        blob = read_blob_at_commit(info["repo_url"], info["sha"], info["path"], credentials)
        try:
            content = blob.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError(
                f"'{info['path']}' does not look like a UTF-8 text file; "
                "this datasource only supports text content."
            ) from e

        if info.get("archive_superseded"):
            # Archiving here, rather than in `_get_pages`, is what lets a
            # single pipeline run both ingest a new version AND archive the
            # old one - `_get_pages` runs before any file's content is
            # actually fetched, so it can never know "this run's sync will
            # succeed," only what a *previous* run already got in. Being
            # called here means this exact version is being ingested right
            # now, so any other existing document for the same path is
            # definitely superseded.
            api_base_url = credentials.get("dify_api_base_url") or _DEFAULT_DIFY_API_BASE_URL
            api_key = credentials.get("dify_api_key")
            dataset_id = info.get("dataset_id")
            if api_key and dataset_id:
                to_archive = find_superseded_document_ids(
                    api_base_url, api_key, dataset_id, info["path"], info["blob_sha"]
                )
                archive_documents(api_base_url, api_key, dataset_id, to_archive)

        yield self.create_variable_message("content", content)
        yield self.create_variable_message("page_id", page.page_id)
        yield self.create_variable_message("workspace_id", page.workspace_id)
