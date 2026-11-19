#!/bin/sh
# Run from a disposable clean checkout; pass an external copy of the feature patch.
set -eu
patch=${1:?usage: ROLLBACK.sh PATH_TO_PATCH BASE_COMMIT}
base=${2:?missing base commit}
test -f "$patch"
test -z "$(git rev-parse --show-prefix)"
test -z "$(git status --porcelain --untracked-files=all)"
git diff --quiet "$base" HEAD -- . ':(exclude)deck-ir'
git apply --reverse --index --check --include='deck-ir/**' "$patch"
git apply --reverse --index --include='deck-ir/**' "$patch"
git diff --cached --quiet "$base" --
git diff --quiet --
test ! -f deck-ir/pyproject.toml
printf 'RESTORED_BASE_TREE=%s\n' "$(git write-tree)"
