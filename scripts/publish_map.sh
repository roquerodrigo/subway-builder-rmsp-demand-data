#!/usr/bin/env bash
# Publishes out/pops_map.html to GitHub Pages (orphan gh-pages branch).
set -euo pipefail

BRANCH="gh-pages"
MAP="out/pops_map.html"

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ ! -f "$MAP" ]]; then
    echo "error: $MAP not found — run 'uv run demand-data generate' first" >&2
    exit 1
fi

staging="$(mktemp -d)/$BRANCH"
cleanup() {
    git worktree remove --force "$staging" >/dev/null 2>&1 || true
    rm -rf "$(dirname "$staging")"
}
trap cleanup EXIT

git worktree add --detach --no-checkout "$staging" >/dev/null
(
    cd "$staging"
    git checkout --orphan "$BRANCH" >/dev/null 2>&1
    git rm -rfq --ignore-unmatch . >/dev/null

    cp "$repo_root/$MAP" index.html
    touch .nojekyll

    git add -A
    git commit -qm "Publish demand map"
    git push -fq origin "HEAD:refs/heads/$BRANCH"
)

remote_url="$(git remote get-url origin)"
slug="${remote_url#*github.com[:/]}"
slug="${slug%.git}"

page_url="$(gh api "repos/${slug}/pages" --jq .html_url 2>/dev/null || true)"
if [[ -z "$page_url" ]]; then
    page_url="https://${slug%%/*}.github.io/${slug#*/}/"
    echo "note: enable GitHub Pages for branch '$BRANCH' (root) in the repository settings" >&2
fi

echo "published: $page_url"
