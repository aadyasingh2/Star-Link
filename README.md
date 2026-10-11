# SAPR: Stability-Aware Predictive Routing for a LEO Satellite Constellation

A discrete-event simulator that compares two reactive routing protocols (Link State and Distance Vector) with a predictive router (SAPR) on a Starlink-like constellation, and judges every routing decision against an oracle that knows the true topology.

**Research question.** LEO topology changes follow orbital mechanics and are predictable. Can a router that uses that knowledge beat reactive Link State and Distance Vector on path quality, route stability and control overhead?

The write-up is in `REPORT.md`. This file explains how to run everything.

## What is in the repository

```
config.yaml                 all simulation and routing parameters
docs/PROJECT_SPEC.md        original project specification
src/constellation/          orbits, topology snapshots, ground stations
src/routing/                engine, LinkState, DistanceVector, SAPR, evaluation
scripts/baseline_report.py  3-router comparison against the oracle
scripts/export_viz_data.py  runs the simulator and writes JSON for the web app
scripts/*.py                topology analysis helpers (churn, cutoff, diagnostics)
tests/                      pytest suite
results/                    report output (text, summary CSV, per-sample CSV)
web/                        mission-control visualization (Vite + Three.js + ECharts)
```

## Setup

Requires Python 3.11 or newer. The web app also needs Node.js 18 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

On Linux or macOS, activate with `source .venv/bin/activate`.

## Run the tests

```powershell
python -m pytest -q
```

The test that boots the full 533-node constellation is marked `slow` and skipped by default. Include it with `python -m pytest -q -m slow`.

## Run the comparison (LinkState vs DistanceVector vs SAPR vs Oracle)

```powershell
python scripts\baseline_report.py                       # default: 600 s at a 1 s step
python scripts\baseline_report.py --duration 120 --out results\check.txt
```

The script prints every configuration value and the seed first, then the comparison table, a per-pair breakdown, a diagnostic block, and the total runtime. It also writes `<out>.csv` (table) and `<out>_samples.csv` (one row per router, pair and second). The reference run is stored in `results/comparison_600s.*`.

## Export data for the web app

```powershell
python scripts\export_viz_data.py                       # writes web\public\data\*.json
```

`web/public/data/SCHEMA.md` describes every file and field. All numbers shown in the web app come from these files.

## Reproduce the demo from the repository root

After completing the setup above, run this sequence in PowerShell to regenerate the baseline results and visualization data, then build the web app:

```powershell
python scripts\baseline_report.py --duration 600 --out results\comparison_600s.txt
python scripts\export_viz_data.py
Push-Location web
npm ci
npm run build
Pop-Location
```

The baseline report writes its summary and per-sample CSV files alongside `results\comparison_600s.txt`. The exporter uses its defaults to write the browser's JSON inputs to `web\public\data\`. `npm run build` writes the static site to `web\dist\`. To view it during development, run `npm run dev` from `web\` instead of the build command.

## Run the web app

```powershell
cd web
npm install
npm run dev        # development server
npm run build      # static build in web\dist
```

Views:

| View | Status |
|---|---|
| Constellation | 3D Earth, 528 satellites, links, ground stations, time controls, click inspector |
| Path Explorer | routes of Oracle, LinkState, DistanceVector and SAPR for ground-station pairs, per-hop delay, route-change ticks |
| Analytics | headline table, route classes, delay, overhead, stability timeline, per-pair view, method and limits |
| Network Graph, Events | not included in the current navigation |

Path Explorer covers ground-station pairs only, because those are the pairs the simulator exports. Packet queues, packet loss beyond route-level probes, and TCP throughput are shown as "not measured".

The Earth texture is a NASA Blue Marble image; its source and license are in `web/public/textures/SOURCE.md`.

## Demo walkthrough

Start the app with `cd web` followed by `npm run dev`, then open the local URL printed by Vite.

1. **Constellation:** show the satellite, plane, station, and active-link counts; choose a ground station or click a visible satellite to inspect it; scrub or play the 600-second simulation.
2. **Path Explorer:** choose a source and destination from the five exported ground stations. Compare the four router cards, inspect per-hop delays and route-change ticks, then toggle a router to show or hide its path.
3. **Analytics:** choose a station pair and compare the exported headline metrics, quality/delay/overhead charts, and per-second route-class timeline.

The navigation exposes only these three implemented views. Network Graph and Events are not part of the current demo. Existing reference captures are in `web/screenshots/` (`v2_main.png`, `v3_paths.png`, and `v4_analytics.png`).

## Architecture and backend scope

```text
config.yaml + src/                 Python simulation model and routing algorithms
        |
        +-- scripts/baseline_report.py  -> results/comparison_600s.*
        |
        +-- scripts/export_viz_data.py  -> web/public/data/*.json
                                            |
                                            v
                                  Vite-served browser app
                                  (Three.js + ECharts)
```

This is an offline-compute/static-data architecture: the Python simulator runs when explicitly invoked, and the exporter writes JSON files that the browser loads. There is no live Python API, server-side application, or database in the current project. Vite's development server serves the frontend and exported files; the production build is static output in `web/dist/`.

A live backend would only be needed if the site must launch simulations on demand, accept user uploads or configuration, or coordinate work across users. A database would only be needed if runs, user accounts, or editable results must persist and be queried. Those requirements are outside this project's current scope, so they are not represented as implemented features.

## Configuration (`config.yaml`)

| Key | Meaning |
|---|---|
| `constellation.*` | 24 planes x 22 satellites, 550 km, 53 degrees, phasing 1, ISL range 3000 km, minimum elevation 25 degrees, inter-plane ISL latitude cutoff 45 degrees |
| `simulation.*` | step 1 s, seed 42 |
| `routing.detection_delay_s` | time for a router to notice a link change (0.1 s) |
| `routing.lsa_processing_delay_ms`, `dv_processing_delay_ms` | per-hop processing delay (1 ms each) |
| `routing.spf_holddown_s` | Link State SPF hold-down (1 s) |
| `routing.dv_update_interval_s`, `dv_triggered_holdoff_s` | periodic update (30 s) and triggered-update damping (1 s) |
| `routing.dv_infinity` | DV infinity metric, in ms (10000) |
| `routing.dv_poison_reverse` | poison reverse at send time |
| `routing.cost_change_threshold_ms` | advertise a changed link cost only if it moved more than this (2 ms) |
| `routing.boot_window_s` | convergence time on the frozen t=0 topology, seconds (60) |
| `routing.zero_delay` | zero propagation and processing delay (used only by equality tests) |
| `routing.bytes_per_*` | message size model for control-byte counts |
| `sapr.*` | look-ahead window, alpha, guard time, hysteresis margin, latency bound |
| `evaluation.near_optimal_tolerance_ms` | tolerance for the near-optimal metric (1 ms) |

## How results are judged

Every second, for each ground-station pair, each router's route is checked against the true topology snapshot and put into exactly one class:

- **correct**: same path as the oracle, or equal delay
- **stale_working**: a valid path that is not the best one
- **loop**: following next hops cycles
- **blackhole**: no route, or the route uses a link that is not in the true snapshot

Path delay is always computed from the true link delays, never from advertised costs. `%near_optimal` counts valid paths within `evaluation.near_optimal_tolerance_ms` of the oracle delay.

## Reproducibility

The seed is fixed (`simulation.random_seed`), iteration over nodes and edges is sorted, and DV jitter is drawn per node, so runs do not depend on Python's hash seed. Two 120 s runs under different `PYTHONHASHSEED` values produced identical output CSVs.

## Limitations

- SAPR is evaluated with perfect orbit prediction and no signalling (zero control messages by assumption).
- Delay and loss are route-level probes, not a packet simulation with queues.
- TCP throughput is not measured.
- One seed, one 600 s window, one latitude cutoff (45 degrees).
- Orbits are circular and ground stations sit at altitude 0.