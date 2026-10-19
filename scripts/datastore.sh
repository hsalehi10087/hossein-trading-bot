#!/usr/bin/env bash
# شاخه‌ی data را در پوشه‌ی datastore/ باز می‌کند (pull) یا تغییراتش را push می‌کند.
# usage: scripts/datastore.sh pull | push "<commit message>"
set -euo pipefail
case "$1" in
  pull)
    git fetch -q origin data
    git worktree add -q --detach datastore FETCH_HEAD ;;
  push)
    cd datastore
    git config user.email "action@github.com"; git config user.name "Trading Bot"
    git add -A
    git diff --cached --quiet && { echo "datastore: no changes"; exit 0; }
    git commit -q -m "$2"
    for i in 1 2 3 4 5; do
      git fetch -q origin data && git rebase -q FETCH_HEAD && git push -q origin HEAD:data && exit 0
      sleep $((i * 5))
    done
    echo "datastore push failed" >&2; exit 1 ;;
esac
