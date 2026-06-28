# Flat Searcher

Python desktop application for analyzing Riga apartment sale listings from SS.com.

The project goal is to turn listings into structured apartment candidates with explainable ranking: effective private rooms, mortgage risk, price value, location scores, map markers, history and user workflow.

Detailed implementation planning lives in:

- [project_plan/implementation_plan.md](project_plan/implementation_plan.md)

## Current status

The full MVP roadmap (steps 01-20) is implemented:

- SS.com list/detail parsing, synchronization, snapshots and change events.
- SQLite persistence for listings, images, AI analyses, geocoding and scores.
- Two-pass AI contracts with mock, JSON-file and Gemini providers.
- Temporary image cleanup and persistent floor plan caching.
- Exact-address geocoding eligibility and destination distance scores.
- Cached OSM shop/transport POIs feeding location scores.
- Price-value analysis, weighted scoring, filtering and ranking.
- Persistent read models for ranking, detail and map surfaces.
- Embedded Leaflet map with score-coloured markers.
- Desktop UI with workflow tabs, filters, listing actions and notes.
- Nine built-in scoring profiles plus a custom-profile editor.
- Side-by-side comparison of 2-5 apartments and saved search sessions.
- A local typical-layout prior database fed into Gemini Pass 2 as hypotheses.
- SQLite backup/export command.
- End-to-end `process-listings` command.

Windows packaging is documented in [PACKAGING.md](PACKAGING.md) and is the main
remaining step before distribution.

## Installation

Create a virtual environment and install the development dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Install optional integrations as needed:

```powershell
python -m pip install -e ".[ai,ui]"
```

## Local commands

From the repository root:

```powershell
$env:PYTHONPATH = "src"
python -m flat_searcher show-config
python -m flat_searcher init-db --database .\data\flat_searcher.sqlite3
python -m flat_searcher sync-listings --database .\data\flat_searcher.sqlite3 --limit 2
python -m flat_searcher analyze-listings --database .\data\flat_searcher.sqlite3 --mock
python -m flat_searcher geocode-listings --database .\data\flat_searcher.sqlite3
python -m flat_searcher recalculate-location-scores --database .\data\flat_searcher.sqlite3
python -m flat_searcher recalculate-scores --database .\data\flat_searcher.sqlite3
python -m flat_searcher process-listings --database .\data\flat_searcher.sqlite3 --mock
python -m flat_searcher show-ranking --database .\data\flat_searcher.sqlite3 --limit 10
python -m flat_searcher show-detail 1 --database .\data\flat_searcher.sqlite3
python -m flat_searcher show-map-markers --database .\data\flat_searcher.sqlite3
python -m flat_searcher list-profiles --database .\data\flat_searcher.sqlite3
python -m flat_searcher recalculate-scores --all-profiles --database .\data\flat_searcher.sqlite3
python -m flat_searcher seed-layout-priors --database .\data\flat_searcher.sqlite3
python -m flat_searcher backup-db --database .\data\flat_searcher.sqlite3
```

Scoring profiles let you switch search strategy. There are nine built-in profiles
(for example `for_living_mortgage`, `mortgage_first`, `best_price`, `closer_to_rtu`).
Pass `--profile <key>` to ranking, detail, map and score commands, or build custom
profiles in the desktop UI. Use `--all-profiles` to precalculate scores for every
profile so the UI can switch instantly.

`--mock` validates the local pipeline but does not perform real apartment reasoning. To use
Gemini, set the API key and install the AI extra:

```powershell
$env:GEMINI_API_KEY = "your-key"
$env:GEMINI_MODEL = "gemini-2.5-flash"
python -m flat_searcher process-listings `
  --database .\data\flat_searcher.sqlite3 `
  --gemini `
  --version gemini-2.5-flash-v1
```

Geocoding is intentionally separate from `process-listings` because it makes external network
requests. Run `geocode-listings` explicitly before processing when location scores are needed.
Nominatim asks clients to identify themselves; set `FLAT_SEARCHER_GEOCODER_USER_AGENT` (for
example to an address with your contact) to override the default geocoder User-Agent.

The desktop UI command is available when optional UI dependencies are installed:

```powershell
python -m flat_searcher run-ui --database .\data\flat_searcher.sqlite3
```

Run the test suite with:

```powershell
python -m pytest
```

The user-facing application UI and project-facing documentation must use English.
