#!/usr/bin/env bash
# First-time setup for trail-route-finder.
#
# Idempotent: safe to re-run. Creates the data dir, the Python venv,
# downloads BRouter France tiles (~900 MB), and builds the BRouter image.
#
# Override the data dir with TRAIL_ROUTE_FINDER_DATA=... ./setup.sh
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${TRAIL_ROUTE_FINDER_DATA:-${CLAUDE_PLUGIN_DATA:-$HOME/.local/share/trail-route-finder}}"

TILES=(W5_N40 W5_N45 W5_N50 E0_N40 E0_N45 E0_N50 E5_N40 E5_N45)
BROUTER_TILE_URL="https://brouter.de/brouter/segments4"

say() { printf '\033[1;36m▶\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m✓\033[0m %s\n' "$*"; }

say "Data dir: $DATA_DIR"
mkdir -p "$DATA_DIR/segments4" "$DATA_DIR/output"

if [ ! -d "$DATA_DIR/.venv" ]; then
  say "Creating Python venv"
  python3 -m venv "$DATA_DIR/.venv"
fi
say "Installing Python deps"
"$DATA_DIR/.venv/bin/pip" install -q -r "$SKILL_DIR/scripts/requirements.txt"
ok "venv ready: $DATA_DIR/.venv"

say "Checking France BRouter tiles (~900 MB total)"
for t in "${TILES[@]}"; do
  f="$DATA_DIR/segments4/${t}.rd5"
  if [ -s "$f" ]; then
    printf '  ✓ %s.rd5 (%s)\n' "$t" "$(du -h "$f" | cut -f1)"
  else
    say "  Downloading ${t}.rd5..."
    curl -sLf -o "$f" "${BROUTER_TILE_URL}/${t}.rd5"
  fi
done
ok "tiles ready"

say "Writing runtime .env for docker compose"
cat > "$DATA_DIR/.env" <<EOF
SEGMENTS_PATH=$DATA_DIR/segments4
PROFILE_FILE=$SKILL_DIR/profiles/trail-hilly.brf
EOF

say "Building BRouter image (one-time, ~5 min)"
cd "$SKILL_DIR/docker"
docker compose --env-file "$DATA_DIR/.env" build
ok "image built"

cat <<EOF

Setup complete.

Start BRouter:
  cd "$SKILL_DIR/docker" && docker compose --env-file "$DATA_DIR/.env" up -d

Generate routes:
  "$DATA_DIR/.venv/bin/python" "$SKILL_DIR/scripts/find_routes.py" \\
      --address "Annecy" --dplus-min 400 --dist-min 10 --dist-max 15 -v

Data dir (tiles, venv, generated outputs): $DATA_DIR
EOF
