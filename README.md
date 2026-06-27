# Flat Searcher

Python desktop application for analyzing Riga apartment sale listings from SS.com.

The project goal is to turn listings into structured apartment candidates with explainable ranking: effective private rooms, mortgage risk, price value, location scores, map markers, history and user workflow.

Detailed implementation planning lives in:

- [project_plan/implementation_plan.md](project_plan/implementation_plan.md)

## Current status

The core local processing path is operational:

- SS.com list/detail parsing, synchronization, snapshots and change events.
- SQLite persistence for listings, images, AI analyses, geocoding and scores.
- Two-pass AI contracts with mock, JSON-file and Gemini providers.
- Temporary image cleanup and persistent floor plan caching.
- Exact-address geocoding eligibility and destination distance scores.
- Price-value analysis, weighted scoring, filtering and ranking.
- Persistent read models for ranking, detail and map surfaces.
- Optional PySide6 ranking/detail desktop shell.
- End-to-end `process-listings` command.

Shop and public transport POI collection, the embedded Leaflet map and the complete desktop
workflow are still pending.

## Installation

Create a virtual environment and install the development dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Install optional integrations as needed:

```powershell
python -m pip install -e ".[ai,geo,ui]"
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
```

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

The desktop UI command is available when optional UI dependencies are installed:

```powershell
python -m flat_searcher run-ui --database .\data\flat_searcher.sqlite3
```

Run the test suite with:

```powershell
python -m pytest
```

The user-facing application UI and project-facing documentation must use English.
