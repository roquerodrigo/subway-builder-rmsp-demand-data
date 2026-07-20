#!/usr/bin/env bash
# Publishes out/pops_map.html to GitHub Pages as a single-commit gh-pages branch.
set -euo pipefail

BRANCH="gh-pages"
MAP="out/pops_map.html"

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ ! -f "$MAP" ]]; then
    echo "error: $MAP not found — run 'uv run demand-data generate' first" >&2
    exit 1
fi

# Builds the commit through a scratch index so no branch, worktree or checkout is touched.
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
export GIT_INDEX_FILE="$scratch/index"

map_blob="$(git hash-object -w "$MAP")"
nojekyll_blob="$(git hash-object -w --stdin </dev/null)"
git update-index --add --cacheinfo "100644,$map_blob,index.html"
git update-index --add --cacheinfo "100644,$nojekyll_blob,.nojekyll"

commit="$(git commit-tree "$(git write-tree)" -m "Publish demand map")"
git push -fq origin "$commit:refs/heads/$BRANCH"

remote_url="$(git remote get-url origin)"
slug="${remote_url#*github.com[:/]}"
slug="${slug%.git}"

page_url="$(gh api "repos/${slug}/pages" --jq .html_url 2>/dev/null || true)"
# a API responde erro em JSON sem status de falha; sem isso a mensagem virava a "URL"
if [[ "$page_url" != http* ]]; then
    page_url="https://${slug%%/*}.github.io/${slug#*/}/"
    echo "note: enable GitHub Pages for branch '$BRANCH' (root) in the repository settings" >&2
fi

echo "published: $page_url"
