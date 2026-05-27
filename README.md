# trail-route-finder

A Claude Code plugin that generates **trail running loops** around a starting point with a **minimum elevation gain (D+) target**.

Built for trail runners frustrated by the homogeneous suggestions of Strava / Komoot / Garmin. Sweeps 12 directions × 3 distances around your start point using a self-hosted **BRouter** (OSM path/track preferred) and recomputes elevation with the **IGN RGE ALTI 1m** dataset for accuracy. Returns 5 distinct loops as GPX + an interactive HTML map with IGN topo tiles. A second step pushes a chosen route to Garmin Connect as a Course via [gccli](https://github.com/bpauli/gccli).

## Install

```bash
# In Claude Code
/plugin marketplace add nicolas-boisseau/trail-route-finder
/plugin install trail-route-finder@nicolas-boisseau
```

Then run the one-time setup (downloads ~900 MB of France BRouter tiles, builds the Docker image, creates a Python venv):

```bash
bash ~/.claude/plugins/marketplaces/nicolas-boisseau/trail-route-finder/skills/trail-route-finder/setup.sh
```

(Path may vary slightly depending on Claude Code's plugin cache layout — `find ~/.claude -name setup.sh -path '*/trail-route-finder/*'` will locate it.)

## Use

Ask Claude:

> Trouve-moi des parcours trail autour d'Annecy, 12-15 km avec D+ minimum 400m

The skill will:
1. Geocode the address (BAN)
2. Sweep BRouter round-trips in 12 directions × 3 radii
3. Coarse-filter on SRTM elevation, then recompute precise D+ via IGN
4. Dedupe geometrically, write top 5 GPX + an HTML map
5. Open the HTML for you to review

Pick a route in the HTML, then ask Claude to push it to Garmin:

> Pousse le parcours 3 sur ma montre Garmin

## What's inside

- **BRouter** (self-hosted Docker, port 17777) with the round-trip engine (PR #759, `engineMode=4`)
- A custom routing profile `trail-hilly.brf` that prefers `path`/`track`/`footway`/`bridleway`, allows SAC T1–T3, and neutralizes uphill cost (`uphillcost=0`) so loops follow terrain rather than avoiding it
- **IGN Géoplateforme altimetry** (`data.geopf.fr`, RGE ALTI 1m, no auth) for precise D+
- **Folium** HTML with IGN Plan + IGN Scan25 (topo) layers + elevation profile per candidate
- **gccli** for the Garmin Connect push step

## Data layout

| What | Where |
|---|---|
| Plugin code (read-only) | wherever Claude caches plugins |
| Tiles (~900 MB), venv, generated GPX/HTML | `$TRAIL_ROUTE_FINDER_DATA` (default `~/.local/share/trail-route-finder/`) |

Override the data dir: `TRAIL_ROUTE_FINDER_DATA=/path/to/data bash setup.sh`.

## Requirements

- Linux or macOS (tested on Ubuntu/Debian)
- Docker
- Python 3.10+
- ~900 MB free disk for tiles + ~2 GB during Docker image build
- France-only coverage (IGN RGE ALTI 1m). For other countries, swap the IGN endpoint for SRTM and pick BRouter tiles for the right region.

## Why not just use Strava / Komoot?

Those tools optimize for a single "best" suggestion per request, biased by their popularity heatmap. Trail runners hunting for D+ variety end up with the same loops over and over. This plugin generates a **diverse set of candidates** (12 directions × 3 distances per run) and exposes the trade-off between distance and elevation directly so you can pick what fits the session.

## License

MIT
