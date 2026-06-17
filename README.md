# Flat Searcher

Python desktop application for analyzing Riga apartment sale listings from SS.com.

The project goal is to turn listings into structured apartment candidates with explainable ranking: effective private rooms, mortgage risk, price value, location scores, map markers, history and user workflow.

Detailed implementation planning lives in:

- [project_plan/implementation_plan.md](project_plan/implementation_plan.md)

## Current status

Step 01 foundation is in progress:

- Python package skeleton.
- Runtime configuration.
- Basic CLI.
- SQLite schema bootstrap.
- SS.com parser and sync CLI.
- Internal AI schemas and pipeline contract.
- Scoring, filtering, ranking and map payload foundations.

## Local commands

From the repository root:

```powershell
$env:PYTHONPATH = "src"
python -m flat_searcher show-config
python -m flat_searcher init-db --database .\data\flat_searcher.sqlite3
python -m flat_searcher sync-listings --database .\data\flat_searcher.sqlite3 --limit 2
```

The user-facing application UI and project-facing documentation must use English.
