#!/usr/bin/env bash
# muelle-client: asks a muelle host to deploy or tear down a site and waits for
# the result. Used by the composite action (action.yml); configured through
# MUELLE_* environment variables so it can also run outside GitHub Actions.
#
# Required: MUELLE_ENDPOINT, MUELLE_SITE, MUELLE_SECRET
# Deploy:   MUELLE_SHA256 and either MUELLE_ARTIFACT_URL or MUELLE_ARTIFACT_ID
#           (resolved against the GitHub API of the current repository)
# Optional: MUELLE_ACTION (deploy), MUELLE_ENV (preview), MUELLE_BRANCH, MUELLE_SHA,
#           MUELLE_TOKEN (sent for the artifact download), MUELLE_TIMEOUT (600),
#           MUELLE_DEPLOYMENTS (true: report GitHub Deployment state with `gh`)
set -euo pipefail

: "${MUELLE_ENDPOINT:?}" "${MUELLE_SITE:?}" "${MUELLE_SECRET:?}"
action=${MUELLE_ACTION:-deploy}
env=${MUELLE_ENV:-preview}
branch=${MUELLE_BRANCH:-${GITHUB_HEAD_REF:-${GITHUB_REF_NAME:-}}}
branch=${branch#refs/heads/}
sha=${MUELLE_SHA:-${GITHUB_SHA:-}}
timeout=${MUELLE_TIMEOUT:-600}
deployments=${MUELLE_DEPLOYMENTS:-true}
endpoint=${MUELLE_ENDPOINT%/}

[[ -n $branch ]] || { echo "::error::cannot determine branch; set MUELLE_BRANCH"; exit 1; }

id="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-${MUELLE_SITE}-$(date +%s)"
id=${id//[^A-Za-z0-9-]/-}

deployment=""
set_state() {
  [[ -n $deployment ]] || return 0
  gh api "repos/$GITHUB_REPOSITORY/deployments/$deployment/statuses" \
    -f state="$1" -f environment_url="${2:-}" \
    -f log_url="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID" >/dev/null || true
}

output() {
  if [[ -n ${GITHUB_OUTPUT:-} ]]; then echo "$1=$2" >>"$GITHUB_OUTPUT"; fi
}

case $action in
deploy)
  [[ $sha =~ ^[0-9a-f]{40}$ ]] || { echo "::error::invalid or missing commit sha"; exit 1; }
  digest=${MUELLE_SHA256:?MUELLE_SHA256 is required for deploy}
  digest=${digest#sha256:}
  if [[ -n ${MUELLE_ARTIFACT_URL:-} ]]; then
    artifact_url=$MUELLE_ARTIFACT_URL
  else
    : "${MUELLE_ARTIFACT_ID:?MUELLE_ARTIFACT_ID or MUELLE_ARTIFACT_URL is required for deploy}"
    artifact_url="${GITHUB_API_URL:-https://api.github.com}/repos/$GITHUB_REPOSITORY/actions/artifacts/$MUELLE_ARTIFACT_ID/zip"
  fi
  if [[ $deployments == true ]]; then
    case $env in
    prod) gh_env=production transient=false production=true ;;
    next) gh_env=next transient=false production=false ;;
    *) gh_env="preview/$branch" transient=true production=false ;;
    esac
    deployment=$(
      jq -n --arg ref "$sha" --arg env "$gh_env" \
        --argjson transient "$transient" --argjson production "$production" \
        '{ref: $ref, environment: $env, auto_merge: false, required_contexts: [],
          transient_environment: $transient, production_environment: $production}' |
        gh api "repos/$GITHUB_REPOSITORY/deployments" --input - --jq .id
    ) || deployment=""
    set_state in_progress
  fi
  body=$(jq -cn --arg id "$id" --arg site "$MUELLE_SITE" --arg env "$env" --arg branch "$branch" \
    --arg sha "$sha" --arg sha256 "$digest" --arg token "${MUELLE_TOKEN:-}" --arg url "$artifact_url" \
    --argjson ts "$(date +%s)" \
    '{protocol: 1, id: $id, site: $site, action: "deploy", env: $env, branch: $branch, sha: $sha,
      sha256: $sha256, artifact_url: $url, token: $token, ts: $ts}')
  ;;
teardown)
  body=$(jq -cn --arg id "$id" --arg site "$MUELLE_SITE" --arg branch "$branch" --argjson ts "$(date +%s)" \
    '{protocol: 1, id: $id, site: $site, action: "teardown", env: "preview", branch: $branch, ts: $ts}')
  ;;
*)
  echo "::error::invalid action '$action'"
  exit 1
  ;;
esac

resp=$(mktemp)
trap 'rm -f "$resp"' EXIT
sig=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$MUELLE_SECRET" -r | cut -d' ' -f1)
# shellcheck disable=SC2086 # MUELLE_CURL_OPTS is a word list on purpose
code=$(curl -sS ${MUELLE_CURL_OPTS:-} -o "$resp" -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' -H "X-Muelle-Signature: sha256=$sig" \
  --data-binary "$body" "$endpoint/hooks/$MUELLE_SITE")
if [[ $code != 200 ]]; then
  echo "::error::muelle rejected the request ($code): $(head -c 500 "$resp")"
  set_state failure
  exit 1
fi

deadline=$(($(date +%s) + timeout))
status='{}'
while :; do
  sleep "${MUELLE_POLL_INTERVAL:-3}"
  # shellcheck disable=SC2086
  status=$(curl -sS ${MUELLE_CURL_OPTS:-} "$endpoint/status/$id" || echo '{}')
  state=$(jq -r '.state // "unknown"' <<<"$status" 2>/dev/null || echo unknown)
  case $state in
  success | failure) break ;;
  esac
  if (($(date +%s) > deadline)); then
    state=timeout
    break
  fi
done

url=$(jq -r '.url // ""' <<<"$status" 2>/dev/null || true)
message=$(jq -r '.message // ""' <<<"$status" 2>/dev/null || true)
output url "$url"
echo "muelle: $state $message"
if [[ $state == success ]]; then
  set_state success "$url"
  if [[ -n $url && -n ${GITHUB_STEP_SUMMARY:-} ]]; then
    echo "Deployed **$MUELLE_SITE** ($env): $url" >>"$GITHUB_STEP_SUMMARY"
  fi
else
  set_state failure
  echo "::error::muelle $state: $message"
  exit 1
fi
