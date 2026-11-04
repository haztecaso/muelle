# muelle-receive SITE
# Run by webhook after the HMAC rule has passed. The request body is in
# $MUELLE_PAYLOAD. Validates it against the Nix-declared site table, queues
# it in the spool dir and detaches the worker. Output is the HTTP response.

hook_site=${1:?usage: muelle-receive SITE}
payload=${MUELLE_PAYLOAD:?no payload}
unset MUELLE_PAYLOAD

jq -e 'type == "object"' >/dev/null <<<"$payload" || die "payload is not a JSON object"

id=$(jget "$payload" id)
site=$(jget "$payload" site)
action=$(jget "$payload" action)
env=$(jget "$payload" env)
branch=$(jget "$payload" branch)
sha=$(jget "$payload" sha)
ts=$(jget "$payload" ts)

# Bumped together with the workflow's major version on breaking payload changes.
# Missing means a workflow from before the field existed (protocol 1).
protocol=$(jget "$payload" protocol)
[[ ${protocol:-1} == 1 ]] || die "unsupported protocol '$protocol' (this host speaks 1)"

valid '^[A-Za-z0-9-]{8,96}$' "$id" || die "invalid id"
[[ $site == "$hook_site" ]] || die "site does not match hook"
jq -e --arg s "$site" '.sites | has($s)' "$MUELLE_CONFIG" >/dev/null || die "unknown site"
valid '^[0-9]{9,11}$' "$ts" || die "invalid ts"
skew=$(cfg .maxSkew)
now=$(date +%s)
((ts <= now + skew && ts >= now - skew)) || die "stale or future request (ts=$ts now=$now)"
valid '^[A-Za-z0-9._/-]{1,200}$' "$branch" || die "invalid branch"
[[ $branch != *..* ]] || die "invalid branch"

case $action in
deploy)
  valid '^(prod|next|preview)$' "$env" || die "invalid env"
  jq -e --arg s "$site" --arg e "$env" '.sites[$s].envs | index($e) != null' "$MUELLE_CONFIG" >/dev/null ||
    die "env '$env' not allowed for site '$site'"
  valid '^[0-9a-f]{40}$' "$sha" || die "invalid sha"
  valid '^[0-9a-f]{64}$' "$(jget "$payload" sha256)" || die "invalid sha256"
  artifact_url=$(jget "$payload" artifact_url)
  prefix=$(jq -r --arg s "$site" '.sites[$s].artifactUrlPrefix' "$MUELLE_CONFIG")
  [[ $artifact_url == "$prefix"* ]] || die "artifact_url not allowed (must start with $prefix)"
  valid '^[A-Za-z0-9._/:?=&%-]+$' "$artifact_url" || die "invalid artifact_url"
  [[ $artifact_url != *..* ]] || die "invalid artifact_url"
  slot=$(slot_for "$site" "$env" "$branch" "$sha")
  url=$(url_for "$site" "$slot")
  ;;
teardown)
  [[ $env == preview ]] || die "teardown only applies to previews"
  url=""
  ;;
*) die "invalid action" ;;
esac

# The spool file doubles as the replay/uniqueness gate: noclobber makes the
# create fail if this id was already queued.
spool="$STATE_DIR/spool/$id.json"
[[ ! -e "$(status_path "$id")" ]] || die "id already used"
(
  umask 077
  set -o noclobber
  printf '%s' "$payload" >"$spool"
) 2>/dev/null || die "id already used"

write_status "$id" queued "queued" "$url"

setsid -f "$MUELLE_WORKER" "$id" >>"$STATE_DIR/log/$id.log" 2>&1 </dev/null

jq -cn --arg id "$id" --arg url "$url" --arg status "https://$(cfg .domain)/status/$id" \
  '{id: $id, state: "queued", url: $url, status_url: $status}'
