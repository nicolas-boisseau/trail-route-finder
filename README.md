# trail-route-finder

A Claude Code plugin that generates **trail running loops** around a starting point with **elevation gain (D+) targets**, using OpenStreetMap trails and IGN RGE ALTI 1m altimetry.

Built for trail runners frustrated by the homogeneous suggestions of Strava / Komoot / Garmin. Four route-discovery modes, each suited to a different terrain:

| Mode | Best for | How it works |
|---|---|---|
| `roundtrip` (default) | Uniformly hilly terrain (Alps, mountain) | BRouter auto-loop in 12 directions × 3 radii, filtered by D+ |
| `hilltop` | Plain with isolated knolls (some viticole, Périgord) | Forces routes through OSM peaks / châteaux / viewpoints |
| `segments` (discovery) | One-time per zone — **builds a local index of climbing trails** | Overpass + IGN scans every path/track, detects every contiguous ascending section ≥ 15 m D+ / 100 m / 4 % slope |
| `chained` | **Low-relief / viticole** (Bordeaux, Champagne) | Picks the best climbs from the segment index and chains them into a loop |

In the Gironde viticole, the segment+chain pipeline lifts the achievable D+ from ~250 m (roundtrip) to **~390 m on 14 km** by stringing together short steep ramps that the other modes miss.

## Install

```bash
# In Claude Code
/plugin marketplace add nicolas-boisseau/trail-route-finder
/plugin install trail-route-finder@nicolas-boisseau
```

Then one-time setup (downloads ~900 MB of France BRouter tiles, builds the Docker image, creates the Python venv):

```bash
bash ~/.claude/plugins/marketplaces/nicolas-boisseau/trail-route-finder/skills/trail-route-finder/setup.sh
```

The exact install path may vary — `find ~/.claude -name setup.sh -path '*/trail-route-finder/*'` will locate it.

## Quick start

Easiest: let Claude drive the skill.

> Trouve-moi des parcours trail autour de Saint-Émilion, 12-15 km, D+ minimum 350m

Claude picks the right mode, runs the command, opens the HTML map. You review, ask for the push.

Behind the scenes the commands below run. You can call them directly too.

## Setup (every shell session needs BRouter running)

```bash
DATA="${TRAIL_ROUTE_FINDER_DATA:-$HOME/.local/share/trail-route-finder}"
SKILL=$(find ~/.claude -name SKILL.md -path '*/trail-route-finder/*' -printf '%h\n' | head -1)
PY="$DATA/.venv/bin/python"

# Start BRouter (one-time per session)
cd "$SKILL/docker" && docker compose --env-file "$DATA/.env" up -d
```

## Mode 1 — `roundtrip` (default, hilly terrain)

Generates loops via BRouter's native round-trip. Best when the terrain has roughly uniform elevation around the start (e.g. Alpine valleys, Annecy lake area).

```bash
$PY "$SKILL/scripts/find_routes.py" \
    --address "Annecy" \
    --dplus-min 400 --dist-min 10 --dist-max 15 \
    -v
```

Output: GPX + HTML in `$DATA/output/run_<timestamp>/`. Up to 5 distinct candidate loops, sorted by D+.

## Mode 2 — `hilltop` (forced passage through peaks)

When the terrain is mostly flat but has named hilltops (small viticole knolls, isolated mottes), this mode queries OSM for `natural=peak`, `tourism=viewpoint`, `historic=castle` and routes loops *through* them.

```bash
$PY "$SKILL/scripts/find_routes.py" \
    --address "Saint-Michel-de-Fronsac" \
    --dplus-min 300 --dist-min 11 --dist-max 16 \
    --mode hilltop -v
```

Better than `roundtrip` in areas where BRouter's auto-waypoint placement misses the climbs — it puts them in valleys to minimize cost.

## Mode 3 — `segments` (discover climbing trails — run once per zone)

This is the foundation for high-quality routes in low-relief areas. Scans every OSM trail in a radius, samples IGN altimetry every 25 m, detects contiguous ascending sections meeting D+/length/slope thresholds.

```bash
$PY "$SKILL/scripts/find_segments.py" \
    --address "Libourne" --radius-km 8 -v
```

First run: ~30-60 s (Overpass + ~50 k IGN points batched). Subsequent runs on the same zone: instant — cached in `$DATA/segments_cache/`.

Output: an HTML map showing every detected climb, colored by slope:
- 🟡 yellow 3-5 % · 🟠 orange 5-8 % · 🔴 red 8-12 % · 🟥 dark red ≥ 12 %

A top-10 legend in the corner lists the steepest climbs by D+ and name. Use this map to **discover** what your area actually has — many "Côte de X" / "Coteau de X" toponyms will surface.

To force a refresh after OSM edits or threshold tuning:
```bash
$PY "$SKILL/scripts/find_segments.py" --address "Libourne" --radius-km 8 --force-refresh -v
```

## Mode 4 — `chained` (build loops from the segment index)

The killer feature for viticole / low-relief areas. Picks climbing segments near the start point, tries 2-4-segment combinations, builds routes that pass through each climb's bottom → top via BRouter.

```bash
$PY "$SKILL/scripts/find_chained.py" \
    --address "Saint-Émilion" --zone-address "Libourne" \
    --dist-min 11 --dist-max 16 --dplus-min 350 \
    --n-candidates 5 -v
```

`--zone-address` selects which segment cache to use. If omitted, the index is built (or reused) centered on `--address`. This lets you index a wide zone once (`--zone-address "Libourne" --zone-radius-km 8`) then build loops from any nearby start.

Sample output line:
```
#1: 14.1 km · D+ 386 m · 27 m/km
    via: Route de la Côte de la Jeune Vigne → ? → Chemin de Larsis → Chemin de Badette
```

## Visualizing the result

Each run produces an interactive HTML in `$DATA/output/<run_tag>/index.html`:

```bash
xdg-open "$DATA/output/$(ls -t "$DATA/output" | head -1)/index.html"
```

Or for a specific run:
```bash
xdg-open "$DATA/output/chained_20260528_074559/index.html"
```

The HTML embeds:
- **IGN Plan + IGN Scan25 + OSM** layers (toggle top-right)
- Each candidate loop with a distinct color
- Popup per loop with km / D+ / D+ per km + a small SVG elevation profile
- A "download GPX" link inline

For the segments map (mode 3), see the colour scheme in the legend.

## Presets — try several start points automatically

A preset names a zone with multiple candidate start points. The skill tries each, falling back to "stretch zones" (further drive) only when the locals can't meet the target.

```bash
$PY "$SKILL/scripts/find_routes.py" --preset libourne \
    --dplus-min 300 --dist-min 12 --dist-max 16
```

Presets are JSON files. Bundled: `skills/trail-route-finder/presets/libourne.json`. User-extensible by dropping new files in `$DATA/presets/<name>.json` — user files take precedence.

The bundled `libourne` preset tries Moulon / Fronsac / Saint-Émilion locally, then Aubeterre / Brantôme (Périgord) as stretch.

## Push to Garmin

When you've picked a route from the HTML, push it to Garmin Connect as a Course:

```bash
$PY "$SKILL/scripts/push_to_garmin.py" \
    "$DATA/output/chained_<ts>/route_<N>.gpx" \
    --type trail_running --name "Boucle Saint-Émilion D+390"
```

Add `--send-to <device-id>` (from `gccli devices list`) to push directly to the watch. Requires the [gccli](https://github.com/bpauli/gccli) skill installed and authenticated.

## Architecture

```
input (address / preset)
        │
        ▼
   geocode (BAN)
        │
   ┌────┼──────────────────────────────────────────────────────┐
   │    │                                                       │
   ▼    ▼                                                       ▼
roundtrip                hilltop                      segments (1× / zone)
sweep dirs × radii    OSM peaks + IGN              Overpass + IGN dense scan
   │                     │                                       │
   │                     │                                       ▼
   │                     │                                   climb index (cached)
   │                     │                                       │
   │                     │                              ┌────────┘
   │                     │                              ▼
   │                     │                            chained
   │                     │                       combos × multi-route
   │                     │                              │
   └─────────────────────┴──────────────────────────────┘
                              │
                              ▼
                IGN RGE ALTI 1m precise D+
                              │
                              ▼
              filter + Jaccard dedupe → top N
                              │
                              ▼
              GPX + folium HTML (IGN Plan + Scan25)
                              │
                              ▼ (optional)
                push_to_garmin.py → gccli courses import
```

## Data layout

| What | Where |
|---|---|
| Plugin code (read-only) | wherever Claude caches plugins |
| BRouter tiles (~900 MB), venv, generated outputs, segment cache | `$TRAIL_ROUTE_FINDER_DATA` (default `~/.local/share/trail-route-finder/`) |

Override the data dir: `TRAIL_ROUTE_FINDER_DATA=/path/to/data bash setup.sh`.

## Tuning

If a zone returns thin segments, edit `scripts/segments.py`:
- `MIN_DPLUS_M` (default 15) — lower to catch shorter ramps
- `MIN_LENGTH_M` (default 100) — lower to catch raidillons
- `MIN_AVG_SLOPE_PCT` (default 4) — raise to filter gentle slopes
- `SAMPLE_STEP_M` (default 25) — lower for finer slope detection (more IGN calls)

Then `--force-refresh` to rebuild the cache. The Overpass query at the top of `segments.py` controls which OSM `highway=*` tags are scanned — useful if your zone uses `service` or other tags for trails.

For BRouter tile freshness (weekly updates by upstream), redownload to `$DATA/segments4/`:
```bash
for t in W5_N40 W5_N45 W5_N50 E0_N40 E0_N45 E0_N50 E5_N40 E5_N45; do
  curl -sLo "$DATA/segments4/$t.rd5" "https://brouter.de/brouter/segments4/$t.rd5"
done
docker compose --env-file "$DATA/.env" restart
```

## Requirements

- Linux or macOS · Docker · Python 3.10+
- ~900 MB disk for tiles + ~2 GB during Docker image build
- France-only coverage (IGN RGE ALTI 1m). For other countries, swap the IGN endpoint for SRTM/OpenTopo and pick BRouter tiles for the right region.

## License

MIT
