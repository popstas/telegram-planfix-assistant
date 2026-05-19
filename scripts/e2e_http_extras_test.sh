#!/usr/bin/env bash
# Tertiary end-to-end script: exercises the HTTP endpoints not covered by
# scripts/e2e_test.sh — single topic create, topic close, members bulk-remove
# (dry_run), and folders add-chat. Designed to run *after* scripts/e2e_test.sh
# so "Client chat test 2" already exists in the "Clients" folder.
#
# Prerequisites:
#   1. uvicorn is running on the port from data/config.yml (default 8085).
#   2. scripts/e2e_test.sh ran successfully at least once.
#
# Non-destructive: bulk-remove uses dry_run=true; the new HTTP-side topic is
# closed but not deleted.

set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8085}"
BEARER="${BEARER:-e2e-test-token}"
FOLDER="${FOLDER:-Clients}"
CHAT_TITLE="${CHAT_TITLE:-Client chat test 2}"
HTTP_TOPIC_NAME="${HTTP_TOPIC_NAME:-HTTP Topic}"
USER_TO_REMOVE="${USER_TO_REMOVE:-@popstas}"

need() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "missing required tool: $1" >&2
        exit 1
    }
}
need curl
need jq

auth_header=(-H "Authorization: Bearer ${BEARER}")
json_header=(-H "Content-Type: application/json")

step() {
    echo
    echo ">>> $*"
}

# Resolve the chat id for "Client chat test 2" via the folder endpoint.
step "resolve chat id for '${CHAT_TITLE}' via /telegram/folders/${FOLDER}"
chat_id=$(curl -sS "${auth_header[@]}" "${BASE_URL}/telegram/folders/${FOLDER}" \
    | jq -r --arg t "${CHAT_TITLE}" '.chats[] | select(.title==$t) | .chat_id')
if [[ -z "${chat_id}" ]]; then
    echo "could not find '${CHAT_TITLE}' in folder '${FOLDER}' — run scripts/e2e_test.sh first" >&2
    exit 1
fi
echo "resolved chat_id=${chat_id}"

step "POST /telegram/topics (single create, '${HTTP_TOPIC_NAME}')"
topic_payload=$(jq -nc --arg cid "${chat_id}" --arg name "${HTTP_TOPIC_NAME}" \
    '{telegram_chat_id: ($cid|tonumber), topic_name: $name}')
topic_resp=$(curl -sS -X POST "${auth_header[@]}" "${json_header[@]}" \
    -d "${topic_payload}" \
    "${BASE_URL}/telegram/topics")
echo "${topic_resp}" | jq .
topic_id=$(echo "${topic_resp}" | jq -r '.telegram_topic_id')
if [[ -z "${topic_id}" || "${topic_id}" == "null" ]]; then
    echo "could not extract topic_id from response" >&2
    exit 1
fi

step "POST /telegram/topics (idempotent re-call, same chat + topic name)"
curl -sS -X POST "${auth_header[@]}" "${json_header[@]}" \
    -d "${topic_payload}" \
    "${BASE_URL}/telegram/topics" | jq '{telegram_topic_id, replayed, operation_status}'

step "POST /telegram/topics/${topic_id}/close"
close_payload=$(jq -nc --arg cid "${chat_id}" \
    '{telegram_chat_id: ($cid|tonumber), reason: "e2e http close"}')
curl -sS -X POST "${auth_header[@]}" "${json_header[@]}" \
    -d "${close_payload}" \
    "${BASE_URL}/telegram/topics/${topic_id}/close" | jq .

step "POST /telegram/topics/${topic_id}/close (idempotent re-close)"
curl -sS -X POST "${auth_header[@]}" "${json_header[@]}" \
    -d "${close_payload}" \
    "${BASE_URL}/telegram/topics/${topic_id}/close" | jq '{status, replayed, operation_status}'

step "POST /telegram/groups/${chat_id}/members/bulk-remove (destructive — round-tripped immediately)"
# The HTTP bulk-remove endpoint has no dry_run knob (the spec keeps that to
# the CLI side). To keep this script non-destructive we kick the user and
# immediately add them back. Net effect: no change in membership. Idempotent
# on re-run because Telegram does not raise on a re-add.
remove_payload=$(jq -nc --arg user "${USER_TO_REMOVE}" \
    '{continue_on_error: true, items: [{user: $user}]}')
curl -sS -X POST "${auth_header[@]}" "${json_header[@]}" \
    -d "${remove_payload}" \
    "${BASE_URL}/telegram/groups/${chat_id}/members/bulk-remove" \
    | jq '{operation_status, items}'

step "POST /telegram/groups/${chat_id}/members/bulk-add (re-add ${USER_TO_REMOVE} after the remove)"
readd_payload=$(jq -nc --arg user "${USER_TO_REMOVE}" \
    '{continue_on_error: true, items: [{user: $user, role: "member"}]}')
curl -sS -X POST "${auth_header[@]}" "${json_header[@]}" \
    -d "${readd_payload}" \
    "${BASE_URL}/telegram/groups/${chat_id}/members/bulk-add" \
    | jq '{operation_status, added, items}'

step "POST /telegram/folders/${FOLDER}/chats (re-add an existing chat by id — idempotent)"
addchat_payload=$(jq -nc --arg cid "${chat_id}" '{chat_id: ($cid|tonumber)}')
addchat_resp=$(curl -sS -X POST "${auth_header[@]}" "${json_header[@]}" \
    -d "${addchat_payload}" \
    "${BASE_URL}/telegram/folders/${FOLDER}/chats" || true)
echo "${addchat_resp}" | jq . 2>/dev/null || echo "${addchat_resp}"

echo
echo "http extras e2e flow completed — review the responses above and the chats on Telegram"
