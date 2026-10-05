# Flat Searcher

A daily indexer and shortlist for apartments for sale in Riga, built on SS.com listings.

It crawls every Riga apartment listing, keeps every field in a plain JSONL library with a change
log, measures public-transport journeys from each flat to the places that matter on the real
timetable, and turns the result into a ranked shortlist: a Markdown digest and a static,
phone-friendly page with a map. Every rule-based criterion — price, rooms, floor, building type,
layout, heating, hot water, replanning, land tenure, building wear, travel time, a gym and a
grocery nearby — is decided in
code, so each flat's position can be explained in words, and risks that can block a purchase or a
mortgage are shown as alerts at the top of each card.
A local language model is optional and only reads what rules cannot: floor plans, condition and
red flags in the description.

Python 3.12, standard library only.

## How it works

```
SS.com ──index──► library ──judge──► verdicts          local Ollama model, optional
                     │
                     ├──transit──► journeys to each target
                     │
                     └──digest───► shortlist page ──publish──► GitHub Pages
```

| Command | What it does |
| --- | --- |
| `index` | Crawls SS.com through one throttled client, parses every field of every listing, diffs against the library, logs changes, computes district €/m² medians, merges verdicts and queues listings worth judging. A listing is marked removed only after a complete crawl. |
| `judge` | Judges the day's queue with a local Ollama model under a structured-output schema. Price comparisons are computed in code and override the model's answer. |
| `transit` | Geocodes listings against the state address register (VZD), attaches the building's wear group, age and walls from the VZD cadastre, measures what is near on foot and by car from OpenStreetMap (groceries, gyms, DIY stores, malls, what lies on the way to each target, nuisances around), then runs RAPTOR over the Rīgas Satiksme GTFS timetable backwards from each target, with walks along the OpenStreetMap pedestrian graph. Each option is measured on the timetable: median door-to-door minutes and how often it runs. |
| `digest` | Applies the gates and a transparent ordering (no score) from `criteria.toml`, merges duplicate ads of one flat, and writes `digest/<day>.md` and a static page in `digest/<day>/`. |
| `publish` | Force-pushes the page to a GitHub Pages repository as a single commit. |

The page opens on **Today**: flats that pass a few soft rules and that no other flat beats at
once on price, travel time, floor area and building wear. **All** lists every candidate, sortable by journey
time, transfers, frequency, walking, price and area. Favourites, hidden flats and opened ads are
remembered in the browser; the map colours flats by travel time and draws the chosen routes.

## Quick start

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

$env:FLAT_SEARCHER_LIBRARY_PATH = "data\library"
python -m flat_searcher index --limit 20 --request-delay 1.0
```

For the shortlist, put a `criteria.toml` with your budget, room counts and targets into the
library (the format is in [docs/pipeline.md](docs/pipeline.md#shortlist-transit-and-digest)), then:

```powershell
python -m flat_searcher transit
python -m flat_searcher digest
```

`judge` needs [Ollama](https://ollama.com) with `qwen3.5:9b` pulled.

## Configuration

| Variable | Purpose |
| --- | --- |
| `FLAT_SEARCHER_LIBRARY_PATH` | Library directory. |
| `FLAT_SEARCHER_LIBRARY_REPO`, `_TOKEN`, `_BRANCH` | Instead of a directory, a private git repository holding the library: pulled before each command, pushed after the ones that write. |
| `FLAT_SEARCHER_SS_START_URL` | Crawl start page; defaults to all Riga apartment sale listings. |
| `FLAT_SEARCHER_HOME` | Caches and logs; defaults to `~/.flat_searcher`. |
| `FLAT_SEARCHER_PAGES_REPO`, `_PAGES_TOKEN` | GitHub Pages repository for `publish`. |

Variables may also come from `.env` in the working directory or a file passed with `--env-file`.
The CLI prints counts and stage outcomes only — never a URL, token, path or target name.

## Layout

```
src/flat_searcher/
  scraper/      SS.com parsers, throttled HTTP client, crawler
  library/      JSONL records, diff, district medians, local and git storage
  indexing/     the index run as a list of stages, each outcome recorded in meta.json
  judging/      Ollama runner and the verdict schema
  transit/      address register, GTFS, RAPTOR journeys, OSM walking
  shortlist/    criteria, extracted facts, gates and ordering, digest and page
  publishing.py GitHub Pages publishing
docs/
  pipeline.md         library format, promotion rules, transit, shortlist, operations
  ai-instructions.md  the judging contract, sent to the model as its system prompt
scripts/
  judge-when-free.ps1 runs judge once no other process is using Ollama
```

## Development

```powershell
python -m pytest -q
python -m ruff check src tests
python -m pyright
```

The page-script tests run in Node when it is installed and are skipped otherwise.
