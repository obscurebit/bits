#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export ALLOW_CROSS_THEME_CORPUS_LINKS="${ALLOW_CROSS_THEME_CORPUS_LINKS:-1}"
export OPENAI_REQUEST_TIMEOUT="${OPENAI_REQUEST_TIMEOUT:-240}"
export OPENAI_COMPLETION_ATTEMPTS="${OPENAI_COMPLETION_ATTEMPTS:-3}"
export OPENAI_RETRY_BACKOFF_SECONDS="${OPENAI_RETRY_BACKOFF_SECONDS:-20}"

if [[ $# -gt 0 ]]; then
  dates=("$@")
else
  dates=(
    2026-02-18
    2026-03-25
    2026-03-26
    2026-04-04
    2026-04-23
    2026-04-24
    2026-04-25
    2026-04-26
    2026-04-27
    2026-04-29
    2026-05-02
    2026-05-03
    2026-05-04
  )
fi

today="${dates[$((${#dates[@]} - 1))]}"

for target_date in "${dates[@]}"; do
  echo
  echo "=== Backfilling ${target_date} ==="
  prepare_args=(--date "$target_date")
  if [[ "${BACKFILL_FORCE:-0}" == "1" ]]; then
    prepare_args+=(--force)
  fi
  uv run --python .venv/bin/python scripts/prepare_queue.py "${prepare_args[@]}"
  uv run --python .venv/bin/python scripts/publish_prepared.py --date "$target_date"
done

echo
echo "=== Refreshing homepage and registry for ${today} ==="
uv run --python .venv/bin/python scripts/publish_prepared.py --date "$today" --update-home
uv run --python .venv/bin/python scripts/backfill_registry.py

echo
echo "=== Verifying gaps through ${today} ==="
python3 - "$today" <<'PY'
from datetime import date, timedelta
from pathlib import Path
import sys

launch = date(2026, 1, 30)
through = date.fromisoformat(sys.argv[1])
missing = []

for i in range((through - launch).days + 1):
    d = launch + timedelta(days=i)
    date_str = d.isoformat()
    bits = bool(list(Path("docs/bits/posts").glob(f"{date_str}-*.md")))
    links = (Path("docs/links/posts") / f"{date_str}-daily-links.md").exists()
    edition = bool(list(Path("docs/editions/posts").glob(f"{date_str}-edition-*.md")))
    if not (bits and links and edition):
        missing.append((date_str, bits, links, edition))

if missing:
    print("Remaining gaps:")
    for date_str, bits, links, edition in missing:
        print(f"  {date_str}: bits={bits} links={links} edition={edition}")
    sys.exit(1)

print("No missing bit/link/edition files found.")
PY
