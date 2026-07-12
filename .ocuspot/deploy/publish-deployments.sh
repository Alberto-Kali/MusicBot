#!/bin/sh
set -eu

PROJECT="music-bot"
CARRIER_ID="backend-kvm"
GATEWAY_ID="pconf-bus"
SUBDOMAIN="music"
DOMAIN="pconf.ru"
FRP_PROXY="music_bot_backend_kvm_pconf_bus_3123"
DESIRED_STATE="present"
PORTS_JSON='[3123]'
CONTROL_PROJECT_DEFAULT="Alberto-Genuardy/ocuspot-control"
CONTROL_REF_DEFAULT="main"
GITLAB_BASE_DEFAULT="https://gitlab.com"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing required command: $1" >&2; exit 11; }
}

need_cmd curl
need_cmd jq
need_cmd sha256sum
need_cmd base64
need_cmd date

if [ -z "${OCUSPOT_GITLAB_TOKEN:-}" ]; then
  echo "OCUSPOT_GITLAB_TOKEN is required to publish authoritative deployments.json" >&2
  exit 12
fi

CONTROL_PROJECT="${OCUSPOT_CONTROL_PROJECT:-$CONTROL_PROJECT_DEFAULT}"
if [ -z "$CONTROL_PROJECT" ]; then
  echo "Skipping deployments.json publication: control repo is not configured." >&2
  exit 0
fi

CONTROL_REF="${OCUSPOT_CONTROL_REF:-$CONTROL_REF_DEFAULT}"
GITLAB_BASE="${OCUSPOT_GITLAB_BASE_URL:-$GITLAB_BASE_DEFAULT}"
if [ -z "$GITLAB_BASE" ]; then
  GITLAB_BASE="https://gitlab.com"
fi
GITLAB_BASE="${GITLAB_BASE%/api/v4}"
GITLAB_BASE="${GITLAB_BASE%/}"
API_BASE="${GITLAB_BASE}/api/v4"

IMAGE_REF="${OCUSPOT_IMAGE_REF:-__OCUSPOT_IMAGE__}"
IMAGE_DIGEST=""
case "$IMAGE_REF" in
  *@sha256:*) IMAGE_DIGEST="${IMAGE_REF##*@}" ;;
  *@sha512:*) IMAGE_DIGEST="${IMAGE_REF##*@}" ;;
esac

PROJECT_ENC="$(jq -rn --arg v "$CONTROL_PROJECT" '$v|@uri')"
FILE_ENC="$(jq -rn --arg v "deployments.json" '$v|@uri')"
TMP_RAW="$(mktemp)"
TMP_JSON="$(mktemp)"
trap 'rm -f "$TMP_RAW" "$TMP_JSON"' EXIT

HTTP_CODE="$(curl -sSL -o "$TMP_RAW" -w '%{http_code}' -H "PRIVATE-TOKEN: ${OCUSPOT_GITLAB_TOKEN}" "${API_BASE}/projects/${PROJECT_ENC}/repository/files/${FILE_ENC}?ref=$(jq -rn --arg v "$CONTROL_REF" '$v|@uri')")"
ACTION="update"
case "$HTTP_CODE" in
  200)
    CONTENT="$(jq -r '.content // empty' "$TMP_RAW")"
    if [ -n "$CONTENT" ]; then
      printf '%s' "$CONTENT" | tr -d '\n' | base64 -d > "$TMP_JSON"
    else
      printf '{"deployments":[]}\n' > "$TMP_JSON"
    fi
    ;;
  404)
    ACTION="create"
    printf '{"deployments":[]}\n' > "$TMP_JSON"
    ;;
  *)
    echo "Failed to fetch authoritative deployments.json from ${CONTROL_PROJECT}@${CONTROL_REF} (HTTP ${HTTP_CODE})" >&2
    cat "$TMP_RAW" >&2 || true
    exit 13
    ;;
esac

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
record_checksum() {
  jq -r '[.project,.image_ref,(.image_digest // ""),.carrier_id,.gateway_id,.subdomain,(.domain // ""),((.ports // [])|map(tostring)|join(",")),(.frp_proxy // .rathole_service // ""),.desired_state] | join("|")' | \
    sha256sum | cut -c1-16
}

EXISTING="$(jq -c --arg project "$PROJECT" '.deployments[]? | select(.project == $project)' "$TMP_JSON" | head -n1 || true)"
CREATED_AT="$NOW"
REVISION=1
if [ -n "$EXISTING" ]; then
  EXISTING_CREATED="$(printf '%s' "$EXISTING" | jq -r '.created_at // empty')"
  if [ -n "$EXISTING_CREATED" ]; then
    CREATED_AT="$EXISTING_CREATED"
  fi
  EXISTING_REVISION="$(printf '%s' "$EXISTING" | jq -r '.revision // 0')"
  EXISTING_CHECKSUM="$(printf '%s' "$EXISTING" | record_checksum)"
else
  EXISTING_REVISION=0
  EXISTING_CHECKSUM=""
fi

CANDIDATE_BASE="$(jq -nc \
  --arg project "$PROJECT" \
  --arg image_ref "$IMAGE_REF" \
  --arg image_digest "$IMAGE_DIGEST" \
  --arg carrier_id "$CARRIER_ID" \
  --arg gateway_id "$GATEWAY_ID" \
  --arg subdomain "$SUBDOMAIN" \
  --arg domain "$DOMAIN" \
  --arg frp_proxy "$FRP_PROXY" \
  --arg desired_state "$DESIRED_STATE" \
  --argjson ports "$PORTS_JSON" \
  '{project:$project,image_ref:$image_ref,image_digest:$image_digest,carrier_id:$carrier_id,gateway_id:$gateway_id,subdomain:$subdomain,domain:$domain,ports:$ports,frp_proxy:$frp_proxy,rathole_service:$frp_proxy,desired_state:$desired_state}')"
CANDIDATE_CHECKSUM="$(printf '%s' "$CANDIDATE_BASE" | record_checksum)"
if [ "$EXISTING_CHECKSUM" = "$CANDIDATE_CHECKSUM" ]; then
  REVISION="$EXISTING_REVISION"
else
  REVISION=$((EXISTING_REVISION + 1))
  if [ "$REVISION" -lt 1 ]; then REVISION=1; fi
fi

FINAL_RECORD="$(printf '%s' "$CANDIDATE_BASE" | jq -c \
  --arg created_at "$CREATED_AT" \
  --arg updated_at "$NOW" \
  --arg checksum "$CANDIDATE_CHECKSUM" \
  --arg status "deployed" \
  --argjson revision "$REVISION" \
  '. + {revision:$revision,created_at:$created_at,updated_at:$updated_at,checksum:$checksum,status:$status}')"

UPDATED="$(jq -c \
  --arg project "$PROJECT" \
  --argjson record "$FINAL_RECORD" \
  '.deployments = ((.deployments // []) | map(select(.project != $project)) + [$record] | sort_by(.project)) | .' "$TMP_JSON")"

CONTENT_HASH="$(printf '%s' "$UPDATED" | jq -r '[(.deployments // [])[] | (.project + ":" + (.checksum // "") + ":" + .desired_state)] | join(";")' | sha256sum | cut -d" " -f1)"
FINAL_JSON="$(printf '%s' "$UPDATED" | jq --arg now "$NOW" --arg hash "$CONTENT_HASH" '.meta = {content_hash:$hash,updated_at:$now}')"

COMMIT_JSON="$(jq -nc \
  --arg branch "$CONTROL_REF" \
  --arg message "ocuspot ci deploy: update deployments.json for project music-bot" \
  --arg action "$ACTION" \
  --arg content "$FINAL_JSON" \
  '{branch:$branch,commit_message:$message,actions:[{action:$action,file_path:"deployments.json",content:$content}]}' )"

HTTP_CODE="$(printf '%s' "$COMMIT_JSON" | curl -sSL --post301 --post302 --post303 -o "$TMP_RAW" -w '%{http_code}' \
  -X POST \
  -H "PRIVATE-TOKEN: ${OCUSPOT_GITLAB_TOKEN}" \
  -H "Content-Type: application/json" \
  --data @- \
  "${API_BASE}/projects/${PROJECT_ENC}/repository/commits")"
case "$HTTP_CODE" in
  200|201)
    echo "Published authoritative deployments.json to ${CONTROL_PROJECT}@${CONTROL_REF} for project ${PROJECT} (rev=${REVISION})."
    ;;
  *)
    echo "Failed to commit deployments.json to ${CONTROL_PROJECT}@${CONTROL_REF} (HTTP ${HTTP_CODE})" >&2
    cat "$TMP_RAW" >&2 || true
    exit 14
    ;;
esac
