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
  home_date="${BACKFILL_HOME_DATE:-${dates[$((${#dates[@]} - 1))]}}"
else
  start_date="${BACKFILL_START_DATE:-2026-01-30}"
  end_date="${BACKFILL_END_DATE:-$(date +%F)}"
  home_date="${BACKFILL_HOME_DATE:-$end_date}"
  dates=()
  while IFS= read -r target_date; do
    [[ -n "$target_date" ]] && dates+=("$target_date")
  done < <(python3 - "$start_date" "$end_date" <<'PY'
from datetime import date, timedelta
from pathlib import Path
import sys

start = date.fromisoformat(sys.argv[1])
end = date.fromisoformat(sys.argv[2])
if end < start:
    raise SystemExit(f"end date {end} is before start date {start}")

for offset in range((end - start).days + 1):
    target = start + timedelta(days=offset)
    date_str = target.isoformat()
    bits = bool(list(Path("docs/bits/posts").glob(f"{date_str}-*.md")))
    links = (Path("docs/links/posts") / f"{date_str}-daily-links.md").exists()
    edition = bool(list(Path("docs/editions/posts").glob(f"{date_str}-edition-*.md")))
    if not (bits and links and edition):
        print(date_str)
PY
)
fi

if [[ ${#dates[@]} -eq 0 ]]; then
  echo "No missing bit/link/edition dates found through ${home_date}."
  echo
  echo "=== Refreshing homepage and registry for ${home_date} ==="
  uv run --python .venv/bin/python scripts/publish_prepared.py --date "$home_date" --update-home
  uv run --python .venv/bin/python scripts/backfill_registry.py
  exit 0
fi

echo "Backfilling ${#dates[@]} missing date(s): ${dates[*]}"

if [[ "${BACKFILL_DRY_RUN:-0}" == "1" ]]; then
  exit 0
fi

failed=()

for target_date in "${dates[@]}"; do
  echo
  echo "=== Backfilling ${target_date} ==="
  prepare_args=(--date "$target_date")
  if [[ "${BACKFILL_FORCE:-0}" == "1" ]]; then
    prepare_args+=(--force)
  fi
  if ! uv run --python .venv/bin/python scripts/prepare_queue.py "${prepare_args[@]}"; then
    failed+=("${target_date}: prepare failed")
    continue
  fi
  if ! uv run --python .venv/bin/python scripts/publish_prepared.py --date "$target_date"; then
    failed+=("${target_date}: publish failed")
    continue
  fi
done

echo
echo "=== Refreshing homepage and registry for ${home_date} ==="
uv run --python .venv/bin/python scripts/publish_prepared.py --date "$home_date" --update-home
uv run --python .venv/bin/python scripts/backfill_registry.py

echo
echo "=== Verifying gaps through ${home_date} ==="
python3 - "$home_date" <<'PY'
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

if [[ ${#failed[@]} -gt 0 ]]; then
  echo
  echo "Backfill command failures:"
  printf '  %s\n' "${failed[@]}"
  exit 1
fi
