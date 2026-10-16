#!/bin/bash
fetch_readme() {
  repo="$1"; br="$2"
  safe=$(echo "$repo" | tr / __)
  for name in README.md readme.md README.rst README.txt Readme.md; do
    body=$(curl -sf --max-time 20 "https://raw.githubusercontent.com/$repo/$br/$name" | head -c 6000)
    if [ -n "$body" ]; then printf '%s' "$body" > "$RD/readmes/$safe.txt"; return; fi
  done
}
export -f fetch_readme; export RD
xargs -a live_branches.txt -P 10 -n2 bash -c 'fetch_readme "$@"' _
