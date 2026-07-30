#!/usr/bin/env bash

set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1

cd "$REPOSITORY_ROOT"

python3 -m unittest discover \
  --start-directory scripts/tests \
  --pattern 'test_*.py'
ruby scripts/check_generated_music.rb
ruby scripts/validate_music_pages.rb
python3 scripts/responsive_images.py --check
python3 scripts/validate_site.py --fail-on-warnings "$@"
