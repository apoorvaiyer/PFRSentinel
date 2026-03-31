#!/usr/bin/env bash
#
# wiki-push.sh — Push wiki/ folder to the GitHub wiki repo.
#
# First run clones the wiki repo into wiki/. Subsequent runs just commit and push.
#
# Usage:
#   ./scripts/wiki-push.sh              # push with auto-generated commit message
#   ./scripts/wiki-push.sh "my message" # push with custom commit message
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WIKI_DIR="$REPO_ROOT/wiki"
WIKI_REPO="https://github.com/englishfox90/PFRSentinel.wiki.git"

# First run: clone the wiki repo directly into wiki/
if [ ! -d "$WIKI_DIR/.git" ]; then
    echo "Wiki repo not found locally. Cloning into wiki/..."
    git clone --quiet "$WIKI_REPO" "$WIKI_DIR"
    echo "Cloned. Edit files in wiki/ then run this script again to push."
    exit 0
fi

COMMIT_MSG="${1:-Update wiki $(date '+%Y-%m-%d %H:%M:%S')}"

cd "$WIKI_DIR"

if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
    echo "No changes to push — wiki is up to date."
    exit 0
fi

git add -A
git commit -m "$COMMIT_MSG"
git push

echo "Wiki updated successfully."
