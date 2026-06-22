---
name: trail-route-finder
description: Generate trail running route candidates around a starting address with a minimum elevation gain target (D+). Five modes — roundtrip (BRouter auto-loop), hilltop (via OSM peaks/châteaux), segments (index climbing trails in a zone via Overpass + IGN RGE ALTI 1m), chained (build loops by linking pre-indexed climbs), and hill-repeats (repeat climbs to pump D+ — auto by default when a chain falls short, or manual via --repeat-segments). Outputs interactive HTML maps with IGN topo tiles plus GPX. A second step pushes a chosen GPX to Garmin Connect via gccli. Trigger when the user asks for running/trail routes, hilly loops, D+ training routes, dénivelé, climbing segments, hill repeats, répétitions de côtes, or wants to discover new trails around a location.
---

# trail-route-finder

Generates trail running loops with elevation gain targets. Four modes, picked based on terrain.

## Resolving paths (do this once at the start of any session)

```bash
DATA="${TRAIL_ROUTE_FINDER_DATA:-${CLAUDE_PLUGIN_DATA:-$HOME/.local/share/trail-route-finder}}"
SKILL=$(find ~/.claude -name SKILL.md -path '*/trail-route-finder/*' -printf '%h\n' | head -1)
PY="$DATA/.venv/bin/python"
```

If `$DATA/.venv` does not exist: `bash "$SKILL/setup.sh"` (one-time, ~5 min).

## Start BRouter (idempotent)

```bash
cd "$SKILL/docker" && docker compose --env-file "$DATA/.env" up -d
```

## Picking the mode

Use this decision tree. When uncertain, ask the user about terrain or try modes in this order: chained → hilltop → roundtrip.

1. **User wants a quick loop in clearly hilly terrain** (mountains, well-known trail running spots) → mode `roundtrip` (default)
2. **Terrain is flat with isolated landmarks (châteaux, viewpoints)** → mode `hilltop`
3. **Terrain is low-relief but locally known to have climbs** (Bordeaux viticole, Champagne, etc.) → **first run `find_segments.py`** on the zone (cached), then `find_chained.py` for routes
4. **User explicitly asks for "the best climbs around X"** → `find_segments.py`, no chaining

## Mode commands

### roundtrip / hilltop (single-step)

```bash
$PY "$SKILL/scripts/find_routes.py" \
    --address "<addr>" \
    --dplus-min <m> --dist-min <km> --dist-max <km> \
    [--mode hilltop] [--preset <name>] [-v]
```

Outputs `$DATA/output/run_<ts>/` (GPX + index.html). Show the HTML path to the user.

### segments discovery (one-time per zone, cacheable)

```bash
$PY "$SKILL/scripts/find_segments.py" \
    --address "<zone center>" --radius-km <r> [-v]
```

Outputs `$DATA/output/segments_<ts>/index.html` — colored map of every climbing trail. Use it when the user wants to see what climbs exist around them, before deciding on a route. The cache key is hashed from rounded (lat, lon, radius); subsequent runs are instant.

### chained (build loop from segment index)

```bash
$PY "$SKILL/scripts/find_chained.py" \
    --address "<loop start>" --zone-address "<segment-cache center>" \
    --dist-min <km> --dist-max <km> --dplus-min <m> \
    [--n-candidates 5] [-v]
```

`--zone-address` lets the user index a wide zone once (e.g. "Libourne", radius 8 km), then start loops from different points within it (Saint-Émilion, Fronsac, etc.) reusing the same cache.

### hill-repeats (boost D+ via segment repetitions)

**Auto-boost** is ON by default in `chained` mode whenever `--dplus-min` is set. When a base loop falls short of the target, the system tries one boost attempt on its steepest in-chain climb (adding reps that fit within `--dist-max × 1.15`). The `🔁` flag and `×N` annotations in the output indicate reps are active.

Disable with `--no-hill-repeats` (e.g. when the user only wants pure single-pass loops).

**Manual mode** — when the user names the climbs and reps explicitly:

```bash
$PY "$SKILL/scripts/find_chained.py" \
    --address "<start>" --zone-address "<cache center>" \
    --dist-max <km> \
    --repeat-segments "<name1>:<reps1>, <name2>:<reps2>, ..."
```

Names are matched as case-insensitive substrings of OSM `way_name`. On ambiguous matches, the highest-D+ climb wins. Each rep = one full ascension; total ascensions = N, distance added = `(2N-1) × climb.length_m`, D+ added = `N × climb.dplus_m`.

Example for a D+ session: `--repeat-segments "Église:4, Larsis:3, Côte de la Jeune:3"` → 4 ascensions on Route de l'Église, 3 on Chemin de Larsis, 3 on Route de la Côte de la Jeune Vigne.

## Choosing the start point

If the user gives a home address in a low-relief area (Bordeaux, plains), home-as-start often yields disappointing D+. Two paths:
- Suggest a drive-to start point that's known to be hillier (Saint-Émilion, Fronsac for Libourne home; preset zones list candidates).
- Or run with `--preset <name>` which auto-tries the preset's local starts and falls back to stretch starts.

## Visualizing results

After any successful run, open the HTML map:
```bash
xdg-open "$DATA/output/<run_tag>/index.html"
```

Tell the user what to look at: the IGN Scan25 layer toggle (top-right) for topo, the popups for D+ / distance / elevation profile.

## Pushing to Garmin (only when user explicitly asks)

```bash
$PY "$SKILL/scripts/push_to_garmin.py" \
    "$DATA/output/<run_tag>/route_<N>.gpx" \
    --type trail_running --name "<course name>" \
    [--send-to <device-id>]
```

Delegates to the gccli skill. Confirm with the user which candidate to push before running this.

## What achievable D+ looks like

Rough ceiling per 12-16 km loop, based on observed runs:

| Zone | Mode roundtrip | Mode hilltop | Mode chained | + Hill-repeats |
|---|---|---|---|---|
| Annecy / Alps | 400-700 m | (not needed) | (not needed) | (not needed) |
| Libourne viticole | 250 m | 300 m | **~390 m** | up to ~600 m on 25 km |
| Libourne city center (plain) | 40 m | 80 m | 250 m | not enough density to boost |

When a target seems unreachable, suggest:
1. A drive-to start point in the same preset
2. A wider distance range (e.g. 12-18 km instead of 12-16)
3. A lower D+ target with honest framing about the local geography
4. Auto hill-repeats (already on by default) or manual reps via `--repeat-segments` — works when the gap is ≤ a few hundred meters and the zone has steep enough climbs in the chain

## Garmin barometric inflation

User's Garmin watch (or Strava) often reports D+ ~25-35 % higher than the geometric D+ on low-relief terrain (barometric noise accumulates). If the user says "I usually do 375 m D+ on this 13 km route" and our computation says 290 m, that's likely the same route — just measured differently.

## Tuning the segment thresholds

In `scripts/segments.py`:
- `MIN_DPLUS_M` (default 15) — raise for fewer / longer climbs; lower for short raidillons
- `MIN_LENGTH_M` (default 100) — minimum climb length
- `MIN_AVG_SLOPE_PCT` (default 4) — minimum average slope
- `SAMPLE_STEP_M` (default 25) — IGN sample resolution

After editing, re-run with `--force-refresh`. Lowering thresholds dramatically increases IGN calls.

## Limitations

- France-only (IGN RGE ALTI 1m + BRouter France tiles). Other countries need different elevation source + tiles.
- Coverage limited to OSM trail tags. Unmapped vineyard tracks won't appear.
- Garmin push needs the gccli skill installed and authenticated (`gccli auth status`).
