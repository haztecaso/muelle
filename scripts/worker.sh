# muelle-worker ID
# Detached by muelle-receive. Reads the already-validated payload from the
# spool dir, deploys or tears down, and keeps the status file updated.

id=${1:?usage: muelle-worker ID}
spool="$STATE_DIR/spool/$id.json"
payload=$(<"$spool")
rm -f "$spool"

site=$(jget "$payload" site)
action=$(jget "$payload" action)
env=$(jget "$payload" env)
branch=$(jget "$payload" branch)
sha=$(jget "$payload" sha)
site_dir="$BASE_DIR/$site"
url=""
tmp=""

on_exit() {
  local rc=$?
  if [[ -n $tmp && -d $tmp ]]; then rm -rf "$tmp"; fi
  if ((rc != 0)); then
    write_status "$id" failure "failed (exit $rc): $(tail -n 3 "$STATE_DIR/log/$id.log" 2>/dev/null | tr '\n' ' ')" "$url"
  fi
}
trap on_exit EXIT

exec 9>"$STATE_DIR/locks/$site.lock"
flock 9

echo "[$(date -u +%FT%TZ)] $action site=$site env=$env branch=$branch sha=$sha"

# Removes surplus entries in DIR, keeping the newest KEEP (by mtime) and never
# touching PROTECT.
prune() {
  local dir=$1 keep=$2 protect=${3:-} n=0 entry
  [[ -d $dir ]] || return 0
  while IFS= read -r entry; do
    [[ -z $entry ]] && continue
    n=$((n + 1))
    ((n > keep)) || continue
    [[ $entry == "$protect" ]] && continue
    echo "pruning $dir/$entry"
    rm -rf -- "${dir:?}/$entry"
  done < <(find "$dir" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%T@ %f\n' | sort -rn | cut -d' ' -f2-)
}

# Rejects archive members with absolute paths or '..' components.
check_names() {
  local name
  while IFS= read -r name; do
    if [[ $name == /* ]]; then die "absolute path in archive: $name"; fi
    if [[ /$name/ == */../* ]]; then die "'..' in archive: $name"; fi
  done
}

extract() {
  local archive=$1 dest=$2 magic
  magic=$(od -An -tx1 -N4 "$archive" | tr -d ' \n')
  mkdir -p "$dest"
  case $magic in
  504b0304)
    unzip -Z1 "$archive" | check_names
    unzip -q -o "$archive" -d "$dest"
    ;;
  1f8b*)
    tar -tzf "$archive" | check_names
    tar -xzf "$archive" -C "$dest" --no-same-owner --no-same-permissions
    ;;
  *) die "unknown artifact format (magic $magic)" ;;
  esac
  # Static content must be plain files and dirs: symlinks could expose
  # arbitrary host files through nginx.
  if [[ -n $(find "$dest" ! -type f ! -type d -print -quit) ]]; then
    die "archive contains non-regular files (symlinks, devices, ...)"
  fi
  # Unwrap a single top-level directory (e.g. 'dist/').
  local entries=("$dest"/* "$dest"/.[!.]*)
  local real=()
  for e in "${entries[@]}"; do
    if [[ -e $e ]]; then real+=("$e"); fi
  done
  if ((${#real[@]} == 1)) && [[ -d ${real[0]} ]]; then
    mv "${real[0]}" "$dest.unwrap"
    rmdir "$dest"
    mv "$dest.unwrap" "$dest"
  fi
}

deploy() {
  local slot slot_dir rel artifact_url sha256 token max
  slot=$(slot_for "$site" "$env" "$branch" "$sha")
  url=$(url_for "$site" "$slot")
  slot_dir="$site_dir/$slot"
  rel="$slot_dir/releases/$sha"
  artifact_url=$(jget "$payload" artifact_url)
  sha256=$(jget "$payload" sha256)
  token=$(jget "$payload" token)
  max=$(cfg .maxArtifactBytes)

  write_status "$id" in_progress "downloading" "$url"
  mkdir -p "$slot_dir/releases"
  tmp=$(mktemp -d "$slot_dir/.tmp.XXXXXX")

  local curl_args=(-fsSL --max-filesize "$max" --retry 2 -o "$tmp/artifact")
  if [[ $artifact_url == https://* ]]; then curl_args+=(--proto '=https'); fi
  if [[ -n $token ]]; then
    # header file keeps the token out of the process list
    (
      umask 077
      printf 'Authorization: Bearer %s\n' "$token" >"$tmp/headers"
    )
    curl_args+=(-H "@$tmp/headers")
  fi
  curl "${curl_args[@]}" "$artifact_url"
  rm -f "$tmp/headers"

  echo "$sha256  $tmp/artifact" | sha256sum -c --quiet - || die "sha256 mismatch"

  write_status "$id" in_progress "extracting" "$url"
  extract "$tmp/artifact" "$tmp/root"
  chmod -R u=rwX,go=rX "$tmp/root"

  if [[ -e $rel ]]; then rm -rf -- "$rel"; fi
  mv "$tmp/root" "$rel"
  touch "$rel"

  ln -sfn "releases/$sha" "$slot_dir/.current.tmp"
  mv -T "$slot_dir/.current.tmp" "$slot_dir/current"

  jq -n --arg site "$site" --arg env "$env" --arg branch "$branch" --arg sha "$sha" \
    --arg at "$(date -u +%FT%TZ)" '{site: $site, env: $env, branch: $branch, sha: $sha, deployed_at: $at}' \
    >"$slot_dir/meta.json"

  prune "$slot_dir/releases" "$(cfg .keepReleases)" "$sha"
  if [[ $env == preview ]]; then
    prune_previews
  fi
  echo "deployed $url"
}

preview_slots() {
  find "$site_dir" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended -regex '.*/[0-9a-f]{8}' -printf '%f\n'
}

prune_previews() {
  local keep n=0 slot
  keep=$(cfg .keepPreviews)
  while IFS= read -r slot; do
    [[ -z $slot ]] && continue
    n=$((n + 1))
    ((n > keep)) || continue
    echo "pruning preview $slot"
    rm -rf -- "${site_dir:?}/$slot"
  done < <(for s in $(preview_slots); do printf '%s %s\n' "$(stat -c %Y "$site_dir/$s/meta.json" 2>/dev/null || echo 0)" "$s"; done | sort -rn | cut -d' ' -f2)
}

teardown() {
  local slot removed=0
  for slot in $(preview_slots); do
    if [[ $(jq -r .branch "$site_dir/$slot/meta.json" 2>/dev/null) == "$branch" ]]; then
      echo "removing preview $slot"
      rm -rf -- "${site_dir:?}/$slot"
      removed=$((removed + 1))
    fi
  done
  echo "removed $removed preview(s) for branch $branch"
}

mkdir -p "$site_dir"
case $action in
deploy)
  deploy
  write_status "$id" success "deployed $sha" "$url"
  ;;
teardown)
  teardown
  write_status "$id" success "teardown of $branch done"
  ;;
esac
