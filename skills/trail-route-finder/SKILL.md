---
name: trail-route-finder
description: Generate trail running route candidates around a starting address with a minimum elevation gain target (D+). Uses self-hosted BRouter for round-trip generation over OpenStreetMap path/track data, and IGN RGE ALTI 1m for accurate elevation. Returns an HTML map with multiple candidate loops the user can review, plus GPX files. A second step pushes a chosen GPX to Garmin Connect via the gccli skill. Trigger when the user asks for running/trail routes, hilly loops, D+ training routes, dénivelé, or wants to discover new trails around a location.
---

# trail-route-finder

Generates trail running loops around a starting point with a target minimum elevation gain (D+). For trail runners who want variety beyond what Strava/Komoot/Garmin suggest.

## Resolving paths

This skill is a Claude Code plugin. Its bundled code lives at `${CLAUDE_PLUGIN_ROOT}/skills/trail-route-finder/` (or wherever Claude cached the plugin). Mutable runtime data lives at `${TRAIL_ROUTE_FINDER_DATA}` (defaults to `${CLAUDE_PLUGIN_DATA}` if set, else `~/.local/share/trail-route-finder/`).

To resolve them at runtime:
```bash
SKILL_DIR=$(dirname "$(find ${CLAUDE_PLUGIN_ROOT:-~/.claude/plugins} -name SKILL.md -path '*/trail-route-finder/*' -print -quit)")
DATA_DIR="${TRAIL_ROUTE_FINDER_DATA:-${CLAUDE_PLUGIN_DATA:-$HOME/.local/share/trail-route-finder}}"
```

## First-time setup

If `$DATA_DIR/.venv` does not exist, run setup once:
```bash
bash "$SKILL_DIR/setup.sh"
```
This downloads ~900 MB of France BRouter tiles, builds the Docker image, and creates the Python venv. Idempotent — safe to re-run.

## Two-step workflow

**Step 1 — Find candidates**

1. Ask the user for: starting address (or `lat,lon`), D+ minimum (meters), and a distance range in km (e.g. `10-15`).
2. Ensure BRouter is up:
   ```bash
   cd "$SKILL_DIR/docker" && docker compose --env-file "$DATA_DIR/.env" up -d
   ```
3. Run the finder:
   ```bash
   "$DATA_DIR/.venv/bin/python" "$SKILL_DIR/scripts/find_routes.py" \
       --address "<addr>" --dplus-min <m> --dist-min <km> --dist-max <km> -v
   ```
4. Output is written to `$DATA_DIR/output/run_<timestamp>/` (GPX files + `index.html`).
5. Open the HTML for the user to review: `xdg-open "$DATA_DIR/output/run_<timestamp>/index.html"`

**Step 2 — Push chosen route to Garmin**

After the user names which candidate to push:
```bash
"$DATA_DIR/.venv/bin/python" "$SKILL_DIR/scripts/push_to_garmin.py" \
    "$DATA_DIR/output/run_<timestamp>/route_<N>.gpx" \
    --type trail_running [--send-to <device-id>]
```
This delegates to the `gccli` skill to create a Course on Garmin Connect.

## Inputs

- **address**: free-form French address, geocoded via BAN (api-adresse.data.gouv.fr). Or `lat,lon` decimal coordinates.
- **dplus-min**: minimum positive elevation gain in meters (e.g. 600).
- **dist-min / dist-max**: distance range in km (e.g. 10–15).
- **n-candidates** (optional, default 5): how many distinct candidates to return.

## Architecture

```
geocode (BAN)
    │
    ▼
sweep over (12 directions × 3 radii) → BRouter /brouter round-trip (engineMode=4)
    │
    ▼
coarse filter on BRouter SRTM ascent estimate
    │
    ▼
recompute precise D+ via IGN altimetry (RGE ALTI 1m)
    │
    ▼
filter D+ ≥ target → geometric Jaccard dedupe → top N
    │
    ▼
GPX files + folium HTML with IGN Plan + Scan25 tiles
```

## Notes

- BRouter is self-hosted (Docker, port 17777) with mainland France tiles in `$DATA_DIR/segments4/`.
- Custom routing profile `profiles/trail-hilly.brf` favors paths/tracks and zeroes out uphill cost so loops follow terrain rather than avoiding it.
- IGN altimetry is free, rate-limited to ~5 req/s — the script throttles.
- The radius factor in `find_routes.py` (`RADIUS_FACTOR = 0.14`) is empirical; loop perimeter ≈ 7 × roundTripDistance, but direction can swing the result ±50%.

## Tuning

If candidates are too road-heavy, increase the `path_preference` in `trail-hilly.brf`. If they're too short on D+, widen the distance range or pick a higher-altitude start. The HTML shows D+/km — below 25 m/km is "flat", 40+ is hilly, 60+ is mountain.

## Limitations

- France-only (BRouter tiles + IGN endpoint). Other countries need different tiles and an alternative elevation source.
- Starting from a valley floor (lakes, river towns) makes high-D+ short loops physically impossible — the closest hills are too far.
- BRouter doesn't *seek* elevation; it just doesn't avoid it. The 12-direction sweep is what discovers hilly loops.
