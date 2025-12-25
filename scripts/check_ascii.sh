#!/usr/bin/env bash
set -u

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -z "$repo_root" ]; then
  echo "Error: not in a git repository." >&2
  exit 1
fi

cd "$repo_root"

found=0
while IFS= read -r -d '' file; do
  case "$file" in
    *.md) continue ;;
  esac
  if LC_ALL=C grep -nH --binary-files=without-match '[^ -~\t\r\n]' "$file"; then
    found=1
  fi
done < <(git ls-files -z)

if [ "$found" -ne 0 ]; then
  echo "Error: non-ASCII characters detected. Use ASCII only." >&2
  exit 1
fi
