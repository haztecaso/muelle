# muelle-index
# Regenerates the listing of deployed next/preview environments served at the
# muelle domain: <stateDir>/index/{index.json,index.html}.

out="$STATE_DIR/index"
mkdir -p "$out"
exec 8>"$STATE_DIR/locks/index.lock"
flock 8

entries=$(
  for site in $(jq -r '.sites | keys[]' "$MUELLE_CONFIG"); do
    repo=$(jq -r --arg s "$site" '.sites[$s].repo' "$MUELLE_CONFIG")
    prod=$(jq -r --arg s "$site" '.sites[$s].prodUrl' "$MUELLE_CONFIG")
    for dir in "$BASE_DIR/$site"/*/; do
      slot=$(basename "$dir")
      valid '^(next|[0-9a-f]{8})$' "$slot" || continue
      [[ -L $dir/current && -f $dir/meta.json ]] || continue
      jq -c --arg url "$(url_for "$site" "$slot")" --arg repo "$repo" --arg prod "$prod" --arg slot "$slot" \
        '{site, env, branch, sha, deployed_at, slot: $slot, url: $url, repo: $repo, prod: $prod}' "$dir/meta.json"
    done
  done | jq -s 'sort_by(.deployed_at) | reverse | sort_by(.site, (if .env == "next" then 0 else 1 end))'
)
workflows=$(
  for st in "$STATE_DIR"/status/*.json; do
    [[ -f $st ]] || continue
    jq -c '
      select(
        .action == "deploy" and
        (.state == "queued" or .state == "in_progress" or .state == "failure") and
        (.site // "" | length) > 0 and
        (.env // "" | length) > 0 and
        (.branch // "" | length) > 0 and
        (.sha // "" | length) > 0 and
        (.repo // "" | length) > 0
      ) |
      {
        id,
        site,
        env,
        branch,
        sha,
        repo,
        url,
        workflow_url,
        state,
        message,
        updated
      }
    ' "$st"
  done | jq -s 'sort_by(.updated) | reverse'
)

tmp=$(mktemp "$out/.tmp.XXXXXX")
jq -n --argjson entries "$entries" --argjson workflows "$workflows" \
  '{generated: (now | todate), entries: $entries, workflows: $workflows}' >"$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$out/index.json"

tmp=$(mktemp "$out/.tmp.XXXXXX")
{
  cat <<'EOF'
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>muelle previews</title>
<style>
:root { --bg: #fff; --fg: #1d1d1f; --muted: #6e6e73; --line: #e5e5ea; --accent: #0a66c2; --tag: #f2f2f7; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #111113; --fg: #f2f2f7; --muted: #98989f; --line: #2c2c2e; --accent: #64a8ff; --tag: #1c1c1e; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 32px 0 8px; }
h2 a { font-weight: normal; font-size: 14px; margin-left: 8px; }
p.meta { color: var(--muted); margin: 0; font-size: 13px; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
table { width: 100%; border-collapse: collapse; }
td, th { text-align: left; padding: 8px 8px 8px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: normal; font-size: 13px; }
code { font-size: 13px; }
.env { display: inline-block; padding: 0 8px; border-radius: 4px; background: var(--tag); font-size: 13px; }
.empty { color: var(--muted); }
@media (max-width: 600px) { .when { display: none; } }
</style>
</head>
<body>
<main>
<h1>muelle previews</h1>
EOF
  jq -r '
    def h: tostring | @html;
    def a($href; $text): "<a href=\"\($href | h)\" target=\"_blank\" rel=\"noopener noreferrer\">\($text)</a>";
    "<p class=\"meta\">\(.entries | length) deployment(s) &middot; \(.workflows | length) in-progress/failed workflow(s) &middot; generated \(now | todate)</p>",
    (if (.workflows | length) > 0 then
      "<h2>deploy workflows</h2>",
      "<table><tr><th>site</th><th>env</th><th>branch</th><th>commit</th><th>state</th><th class=\"when\">updated</th></tr>",
      (.workflows[] |
        "<tr><td>\(.site | h)</td>" +
        "<td>\(if (.url // "") != "" then a(.url; "<span class=\"env\">\(.env | h)</span>") else "<span class=\"env\">\(.env | h)</span>" end)</td>" +
        "<td>\(a("https://github.com/\(.repo)/tree/\(.branch)"; .branch | h))</td>" +
        "<td>\(a("https://github.com/\(.repo)/commit/\(.sha)"; "<code>\(.sha[0:7] | h)</code>"))</td>" +
        "<td>\(if (.workflow_url // "") != "" then a(.workflow_url; .state | h) else (.state | h) end)</td>" +
        "<td class=\"when\">\(.updated | h)</td></tr>"
      ),
      "</table>"
    else empty end),
    (if (.entries | length) == 0 then "<p class=\"empty\">Nothing deployed to next or preview.</p>" else empty end),
    (.entries | group_by(.site)[] |
      "<h2>\(.[0].site | h)\(a(.[0].prod; "prod"))</h2>",
      "<table><tr><th>env</th><th>branch</th><th>commit</th><th class=\"when\">deployed</th></tr>",
      (.[] |
        "<tr><td>\(a(.url; "<span class=\"env\">\(if .env == "next" then "next" else .slot end | h)</span>"))</td>" +
        "<td>\(a("https://github.com/\(.repo)/tree/\(.branch)"; .branch | h))</td>" +
        "<td>\(a("https://github.com/\(.repo)/commit/\(.sha)"; "<code>\(.sha[0:7] | h)</code>"))</td>" +
        "<td class=\"when\">\(.deployed_at | h)</td></tr>"),
      "</table>")
  ' <<<"$(jq -n --argjson entries "$entries" --argjson workflows "$workflows" '{entries: $entries, workflows: $workflows}')"
  printf '</main>\n</body>\n</html>\n'
} >"$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$out/index.html"
