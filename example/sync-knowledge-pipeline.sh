#!/usr/bin/env bash
# Triggers a Dify Knowledge Pipeline end-to-end via its public API, so the
# git datasource's incremental sync (list -> select-all -> ingest) can run
# unattended from Jenkins/cron instead of clicking through the Dify web UI.
#
# Flow (per Dify's official Knowledge Pipeline API docs):
#   1. POST .../pipeline/datasource/nodes/{node_id}/run
#        -> streams back the page list our datasource plugin's _get_pages
#           decided still needs syncing (SSE response).
#   2. POST .../pipeline/run
#        -> re-submits that ENTIRE page list as "selected", which actually
#           fetches each file's content and ingests it.
#
# Paste this directly into a Jenkins "Execute shell" build step, or run it
# from cron. Everything you're likely to need to change lives in the
# CONFIG section below.
set -euo pipefail

########################################
# CONFIG - edit these for your setup
########################################

# Dify's public API base URL, INCLUDING the /v1 suffix - matching Dify's
# own convention (their SaaS default is "https://api.dify.ai/v1"). NOT the
# internal docker-network address the plugin itself uses; whatever your
# Jenkins agent can actually reach, e.g. "https://your-dify-host/v1".
DIFY_API_BASE_URL="REPLACE_ME"

# A Dify Knowledge Base API key (Bearer token). Generate one under the
# target Knowledge Base's own "API Access" settings in Dify - this is a
# different key from the plugin's own dify_api_key credential, though it
# can point at the same Dify instance.
DIFY_API_KEY="REPLACE_ME"

# The target Knowledge Base's dataset ID.
DATASET_ID="REPLACE_ME"

# The Git datasource node's ID within the pipeline's workflow graph. Leave
# blank to auto-resolve it via the "List Datasource Plugins" API by
# matching PROVIDER_NAME below - only hardcode NODE_ID if you have more
# than one "git" provider node in the same pipeline and need to pick a
# specific one.
NODE_ID=""
PROVIDER_NAME="git"

# Whether to run the published pipeline version or the current draft.
IS_PUBLISHED=true

# This datasource node's parameters. CONFIRMED BY TESTING (both this
# script's Service API calls AND Dify's own web UI's internal calls): "Run
# Datasource Node" does NOT fall back to this node's saved Pipeline Studio
# configuration when inputs is {} - it errors (KeyError on required fields
# like repo_url) instead. So despite already having configured this node in
# the pipeline UI, its values have to be duplicated here too - that's a
# real rough edge of this Dify API (confirmed against Dify's own frontend
# behavior, not something this script can avoid).
#
# dataset_id is intentionally NOT set here - it's injected automatically
# from DATASET_ID above. Only repo_url has no sensible default and must be
# filled in; everything else below already matches this plugin's own
# schema defaults (datasources/git.yaml) - change a value only if you
# actually want this run to differ from that (e.g. you turned on "Verify
# against existing knowledge base documents" for this node).
read -r -d '' DATASOURCE_INPUTS <<'JSON' || true
{
  "repo_url": "REPLACE_ME",
  "branch": "HEAD",
  "extension_filter": "",
  "path_filter": "",
  "use_dataset_verification": false,
  "tag_filename_with_commit": false,
  "archive_superseded_versions": false
}
JSON

# Set to true to dump the raw SSE response from step 1 to stderr before
# parsing it - use this once to confirm/adjust the jq filter below against
# what your Dify version actually sends, then turn it back off.
DEBUG_RAW=false

########################################
# jq: use it if present, otherwise fetch a static binary
########################################

JQ_VERSION="1.7.1"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

if command -v jq >/dev/null 2>&1; then
  JQ_BIN="$(command -v jq)"
else
  echo "jq not found on PATH - downloading a static binary (v${JQ_VERSION})..." >&2
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) jq_arch="amd64" ;;
    aarch64|arm64) jq_arch="arm64" ;;
    *) echo "Unsupported architecture for auto-download: $arch. Install jq manually." >&2; exit 1 ;;
  esac
  case "$os" in
    linux) jq_asset="jq-linux-${jq_arch}" ;;
    darwin) jq_asset="jq-macos-${jq_arch}" ;;
    *) echo "Unsupported OS for auto-download: $os. Install jq manually." >&2; exit 1 ;;
  esac
  JQ_BIN="${WORKDIR}/jq"
  curl -fsSL -o "$JQ_BIN" \
    "https://github.com/jqlang/jq/releases/download/jq-${JQ_VERSION}/${jq_asset}"
  chmod +x "$JQ_BIN"
fi

echo "Using jq: $("$JQ_BIN" --version)" >&2

########################################
# Step 0: resolve NODE_ID if not hardcoded above
########################################

if [ -z "$NODE_ID" ]; then
  echo "Resolving node_id for provider '${PROVIDER_NAME}' via List Datasource Plugins..." >&2
  STEP0_BODY_FILE="${WORKDIR}/step0_body.json"
  HTTP_STATUS="$(
    curl -sS -o "$STEP0_BODY_FILE" -w '%{http_code}' \
      "${DIFY_API_BASE_URL%/}/datasets/${DATASET_ID}/pipeline/datasource-plugins?is_published=${IS_PUBLISHED}" \
      -H "Authorization: Bearer ${DIFY_API_KEY}"
  )"
  if [ "$HTTP_STATUS" != "200" ]; then
    # Deliberately not using `curl -f` here: it discards the response body
    # on HTTP errors, which is exactly the information needed to tell apart
    # "wrong URL/version", "bad dataset_id", and "bad API key" - print it.
    echo "List Datasource Plugins failed: HTTP ${HTTP_STATUS}. Response body:" >&2
    cat "$STEP0_BODY_FILE" >&2
    echo >&2
    exit 1
  fi
  PLUGINS_JSON="$(cat "$STEP0_BODY_FILE")"
  NODE_ID="$("$JQ_BIN" -r --arg provider "$PROVIDER_NAME" \
    '[.[] | select(.provider_name == $provider)] | .[0].node_id // empty' <<<"$PLUGINS_JSON")"
  if [ -z "$NODE_ID" ]; then
    echo "Could not find a datasource node with provider_name='${PROVIDER_NAME}'. Full response:" >&2
    echo "$PLUGINS_JSON" | "$JQ_BIN" . >&2
    exit 1
  fi
  echo "Resolved node_id: ${NODE_ID}" >&2
fi

########################################
# Step 1: run the datasource node, get the page list
########################################

echo "Requesting page list from datasource node ${NODE_ID}..." >&2

STEP1_BODY="$("$JQ_BIN" -n \
  --argjson inputs "$DATASOURCE_INPUTS" \
  --arg dataset_id "$DATASET_ID" \
  --argjson is_published "$IS_PUBLISHED" \
  '{inputs: ($inputs + {dataset_id: $dataset_id}), datasource_type: "online_document", is_published: $is_published}')"

RAW_RESPONSE="${WORKDIR}/step1_raw.txt"
HTTP_STATUS="$(
  curl -sS -N -o "$RAW_RESPONSE" -w '%{http_code}' \
    -X POST \
    "${DIFY_API_BASE_URL%/}/datasets/${DATASET_ID}/pipeline/datasource/nodes/${NODE_ID}/run" \
    -H "Authorization: Bearer ${DIFY_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$STEP1_BODY"
)"
if [ "$HTTP_STATUS" != "200" ]; then
  echo "Run Datasource Node failed: HTTP ${HTTP_STATUS}. Response body:" >&2
  cat "$RAW_RESPONSE" >&2
  echo >&2
  exit 1
fi

if [ "$DEBUG_RAW" = true ]; then
  echo "----- raw SSE response (DEBUG_RAW=true) -----" >&2
  cat "$RAW_RESPONSE" >&2
  echo "----- end raw SSE response -----" >&2
fi

# The stream is a series of "data: {...}" lines (SSE), using a
# datasource-specific event vocabulary that isn't in Dify's public OpenAPI
# spec: "datasource_processing" while running, "datasource_error" on
# failure, "datasource_completed" on success - all three confirmed by
# actually capturing real traffic (both this script's own runs and Dify's
# own web UI), not guessed. On success, event.data is directly the array
# this plugin's _get_pages returns (a list of {workspace_id, pages: [...]}),
# not nested under an "outputs" key.
ALL_EVENTS_JSON="$(
  grep -o '^data: .*' "$RAW_RESPONSE" | sed -e 's/^data: //' | "$JQ_BIN" -s '.'
)"

ERROR_MSG="$("$JQ_BIN" -r '[.[] | select(.event == "datasource_error")] | last | .error // empty' <<<"$ALL_EVENTS_JSON")"
if [ -n "$ERROR_MSG" ]; then
  echo "Datasource node reported an error: ${ERROR_MSG}" >&2
  exit 1
fi

if ! PAGES_JSON="$(
  "$JQ_BIN" '
    def to_infos:
      [ .[] | .workspace_id as $wsid | (.pages // [])[] | {workspace_id: $wsid, page: {page_id: .page_id, page_name: .page_name, type: .type}} ];

    [ .[] | select(.event == "datasource_completed") ] | last | .data | to_infos
  ' <<<"$ALL_EVENTS_JSON"
)"; then
  echo "Failed to parse the datasource_completed event - see the jq error above." >&2
  echo "Re-run with DEBUG_RAW=true and check the raw SSE output." >&2
  exit 1
fi

PAGE_COUNT="$("$JQ_BIN" 'length' <<<"$PAGES_JSON")"
echo "Datasource returned ${PAGE_COUNT} page(s) needing sync." >&2

if [ "$PAGE_COUNT" -eq 0 ]; then
  echo "Nothing to do - exiting." >&2
  exit 0
fi

########################################
# Step 2: re-submit the full page list to actually run the pipeline
########################################

echo "Triggering pipeline run for all ${PAGE_COUNT} page(s)..." >&2

STEP2_BODY="$("$JQ_BIN" -n \
  --argjson datasource_info_list "$PAGES_JSON" \
  --arg start_node_id "$NODE_ID" \
  --argjson is_published "$IS_PUBLISHED" \
  '{
    datasource_type: "online_document",
    datasource_info_list: $datasource_info_list,
    start_node_id: $start_node_id,
    is_published: $is_published,
    response_mode: "blocking",
    inputs: {}
  }')"

STEP2_RESPONSE_FILE="${WORKDIR}/step2_body.json"
HTTP_STATUS="$(
  curl -sS -o "$STEP2_RESPONSE_FILE" -w '%{http_code}' \
    -X POST \
    "${DIFY_API_BASE_URL%/}/datasets/${DATASET_ID}/pipeline/run" \
    -H "Authorization: Bearer ${DIFY_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$STEP2_BODY"
)"
RUN_RESPONSE="$(cat "$STEP2_RESPONSE_FILE")"
if [ "$HTTP_STATUS" != "200" ]; then
  echo "Run Pipeline failed: HTTP ${HTTP_STATUS}. Response body:" >&2
  echo "$RUN_RESPONSE" >&2
  exit 1
fi

echo "$RUN_RESPONSE" | "$JQ_BIN" .

# Confirmed by real response (this doesn't match the generic
# {task_id, data: {status, ...}} shape the OpenAPI docs show for a blocking
# workflow run - for online_document specifically it's actually
# {batch, dataset, documents: [{id, error, indexing_status, ...}]}):
# success means a non-empty documents array with no per-document errors.
# indexing_status will be "waiting" right after this call - indexing itself
# runs asynchronously in Dify, this call only confirms the document(s) were
# accepted and queued.
DOC_ERRORS="$(echo "$RUN_RESPONSE" | "$JQ_BIN" -r '[.documents[]? | select(.error != null) | .error] | join("; ")')"
DOC_COUNT="$(echo "$RUN_RESPONSE" | "$JQ_BIN" '.documents | length')"

if [ -n "$DOC_ERRORS" ]; then
  echo "Pipeline run reported document error(s): ${DOC_ERRORS}" >&2
  exit 1
fi
if [ "$DOC_COUNT" -eq 0 ]; then
  echo "Pipeline run returned no documents - unexpected response shape, see above." >&2
  exit 1
fi

echo "Done: ${DOC_COUNT} document(s) accepted and queued for indexing." >&2
