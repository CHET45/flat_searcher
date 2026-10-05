# Pipeline

Flat Searcher runs once a day. It crawls SS.com, indexes every field of every Riga apartment
listing, works out what changed since the last run and writes the result to a **library**: a
directory of JSONL files, or a private git repository holding one. Listings that are new or
changed in a way worth a second look land in a daily queue; a model judges the queue and its
verdicts are merged into the library on the next run. Everything after that — locating listings,
journeys to your targets, the shortlist and its page — is deterministic code.

```
SS.com ──index──► library ──judge──► verdicts/<day>      local Ollama model, optional
                     │
                     ├──transit──► transit.jsonl        address register, timetable, OSM
                     │
                     └──digest───► digest/<day>/ ──publish──► GitHub Pages
```

## What is secret and what is not

The crawl target is public: the parser is openly SS.com, and the listings it reads are public
pages. What stays private is **where the library lives and how to reach it** — its path or
repository URL and the token — and the criteria inside it, which name personal targets.

The CLI prints counts and stage outcomes only: never a URL, a listing id, a target name or a path
inside the library. Errors from git are redacted and the indexer logs exception types only, so the
same commands are safe to run in a public CI log.

## Library layout

```
library/
  listings.jsonl        one JSON object per line, sorted by ss_id — current state of every listing
  events.jsonl          append-only change log
  queue/<YYYY-MM-DD>.jsonl     listings needing judgment that day
  verdicts/<YYYY-MM-DD>.jsonl  the AI's answers, merged on the next run
  meta.json             last run: status, per-stage outcomes and counts
  criteria.toml         shortlist criteria (written by hand; absent = no digest)
  transit.jsonl         per active listing: register match, coordinates, journeys per target
  transit-targets.json  resolved target points and how each target was defined
  transit-map.json      route shapes, stops and Riga's streets for the map page
  digest/<YYYY-MM-DD>.md       the ordered shortlist for that day
  digest/<YYYY-MM-DD>/         the same shortlist as a page: index.html, map.js, photos/
```

`listings.jsonl` is sorted so that a git diff of the library shows exactly which listings changed.
Writes are atomic (temp file + rename): an interrupted run cannot truncate the library.

A listing record:

| key | contents |
| --- | --- |
| `ss_id`, `url`, `status` | identity; `status` is `active` or `removed` |
| `first_seen`, `last_seen`, `revision`, `content_hash` | bookkeeping |
| `core` | price, area, rooms, floor, district, street, house number, series, building type |
| `fields` | **every** row of the SS.com detail table, verbatim, under the site's own labels |
| `text` | title and description |
| `images` | `[{url, is_floor_plan}]` |
| `stats` | `unique_visits` |
| `market` | `price_per_m2`, `district_median_price_per_m2`, `district_sample_size` |
| `judgment` | the latest verdict, once one exists |

`market` is the only computed enrichment. The model sees one listing at a time and cannot know
the corpus, so the district median is handed to it; with fewer than five comparables the median
is the string `unknown` rather than a misleading number.

## Promotion rules

| What the crawl found | Library | Queue |
| --- | --- | --- |
| identical content | untouched | no |
| only `unique_visits` changed | updated, event logged | no — a model call is the scarce resource |
| price, description or images changed | updated, event logged | yes, `reason: changed` |
| never seen before | added | yes, `reason: new` |
| seen before, never judged, anything changed | updated | yes, `reason: never_judged` |
| absent from a **complete** crawl | `status: removed` | no |
| absent from an **incomplete** crawl | untouched | no |

The last row is deliberate. A crawl with a failed list page knows nothing about what is missing;
treating it as authoritative once deactivated thirty live listings. `meta.json` records
`crawl_complete` and `deactivation_skipped` so a run that could not judge absence says so.

Verdicts merge in date order, later days winning. A verdict naming an unknown `ss_id` is counted
in `meta.json` and ignored.

## Running it

Against a directory:

```powershell
$env:FLAT_SEARCHER_LIBRARY_PATH = "C:\data\flat-library"
python -m flat_searcher index --limit 20 --request-delay 1.0
```

Against the private repository directly (pull, index, push):

```powershell
$env:FLAT_SEARCHER_LIBRARY_REPO = "https://github.com/<owner>/<name>.git"
$env:FLAT_SEARCHER_LIBRARY_TOKEN = "<token>"
python -m flat_searcher index
```

`--dry-run` crawls and diffs but writes nothing. `--env-file` loads variables from a file first
(`.env` in the working directory is read by default). The crawl start URL comes from
`FLAT_SEARCHER_SS_START_URL`; narrow it to a district or price band to keep daily runs short.
`FLAT_SEARCHER_HOME` (default `~/.flat_searcher`) holds caches and the log file;
`python -m flat_searcher show-config` prints the resolved paths.

### The daily loop

`index → transit → digest → publish`, and `judge` on the newest queue when the model step is
wanted. A full crawl takes ~45 minutes and a queue of 170 listings ~1 hour, so detach long runs —
the shell that started them is otherwise the thing that stops them:

```powershell
$env:FLAT_SEARCHER_LIBRARY_PATH = "data\library"
Start-Process .\.venv\Scripts\python.exe -ArgumentList "-m","flat_searcher","index" `
  -RedirectStandardOutput data\index-run.log -RedirectStandardError data\index-run.err -WindowStyle Hidden
```

Everything under `data/` is gitignored. On Windows the venv `python.exe` is a launcher
stub: the PID `Start-Process` returns lives as long as the run does, but CPU time and network
activity belong to its child `python.exe` (filter children by name — the first one may be
`conhost.exe`). `judge` is resumable: interrupting it loses nothing, a rerun skips judged
listings and sweeps earlier failures.

`scripts/judge-when-free.ps1` waits until no other process has held a connection to Ollama for
three minutes, then runs `judge` (`-Day` optional) and appends to `data/judge-run.log`. Use it
whenever another job on the machine may be using the model — see the shared-runner rule below.

## Judging locally

`docs/ai-instructions.md` is written for any model, but the repository also carries a runner for a
local Ollama model, so the queue can be judged locally with no API cost:

```powershell
ollama pull qwen3.5:9b
$env:FLAT_SEARCHER_LIBRARY_PATH = "C:\data\flat-library"
python -m flat_searcher judge --limit 20                      # newest queue day
python -m flat_searcher judge --day 2026-09-14 --max-price 120000 --min-rooms 2 --districts "Centrs,Teika"
```

The runner sends the instructions as the system prompt, the queue entry as the user message with
up to eight gallery images attached, and Ollama's structured-output schema so the verdict keys are
guaranteed. Thinking is off and the context is 12k tokens — that keeps a 9B model on an 8 GB GPU
at roughly 20–30 seconds per listing. Verdicts are appended one at a time, so an interrupted run
loses nothing and a rerun skips what is already judged; three consecutive failures abort the run.

The price fields (`price_ratio`, `price_verdict`, `price_basis`, `suspicious_price`) are computed
in code from `record.market` and handed to the model, then written over whatever it returned:
arithmetic is not something to ask a language model to do. Two more deterministic helpers sit
upstream of the model: sentences about land tenure (`zemes noma`, `denacionalizēts` …) are
extracted from the full text and passed as `hints`, because a 9B model skims a 20k-character
agency description; and the prompt is budgeted — the description is cut to 3,000 characters,
fields to 160, and when a gallery is larger than the image cap the runner keeps the first three
photos plus the tail (SS.com puts the floor plan last). If Ollama still rejects the prompt as
too large, the runner retries with half the images.

Useful switches: `--ids 123,456 --force` re-judges specific listings (a verdict appended later
wins on merge); `--shard 0/2` and `--shard 1/2` let two processes split one queue. Sharding only
pays when the whole model sits in VRAM — with any part on the CPU the two requests serialise and
the pace is unchanged (measured: 22.7 s vs 23 s per listing). Ollama keeps one loaded copy per
`(model, num_ctx)`; another local client using the same model with a different context size
forces a reload on every alternation (measured 23 → 40 s per listing), so run such jobs
sequentially or give them the same `num_ctx`.

## Shortlist: transit and digest

Two deterministic commands turn the library into a daily shortlist. Neither calls a model.

```powershell
python -m flat_searcher transit [--refresh]   # locate listings, journeys to each target
python -m flat_searcher digest [--day D]      # gates + ordering -> digest/<day>.md + <day>/
python -m flat_searcher publish [--day D]     # push the page to the GitHub Pages site (optional)
```

They run after `index` as separate commands, so a timetable outage never costs the day's index.

**`criteria.toml`** lives in the library, because targets and budgets are personal; nothing in
this repository carries defaults that describe a buyer. Unknown keys are errors.

```toml
[price]
max_eur = 60000
bands = [[30000, 40000], [0, 30000], [40000, 50000], [50000, 60000]]  # preference order

[rooms]
order = [2, 1]                 # allowed counts, most preferred first

[building]
excluded_types = ["Koka"]

[heating]
exclude_stove = true

[floor]
exclude = [1]                  # floors that never pass; default: none

[transit]
walk_m = 500                   # stops this close to the flat and to a target count
transfer_walk_m = 300          # default; longest walk between two stops at a transfer
window_start = "07:00"         # default; departures from the first stop in this window
window_end = "10:00"
max_transfers = 2              # default
journeys_max = 4               # default; options kept per target

[[transit.targets]]
name = "work"
address = "Brīvības iela 1"    # resolved through the address register
# lat = 56.95, lon = 24.11     # or explicit coordinates when the register lacks the building

[today]                        # the page's short daily view; every key optional
size = 20                      # default
max_minutes = 120              # sum over targets of minutes + every/2; default: no cap
exclude_walkthrough = true     # default
exclude_leased_land = true     # default
```

`transit` records how each target was defined. `digest` refuses to run when a target was added,
renamed or moved since the last `transit` (`targets_changed=N`): its journeys would be missing or
stale. Run `transit`, then `digest`.

**Transit sources** are public open data cached under `$FLAT_SEARCHER_HOME/cache/transit/`:
the Rīgas Satiksme GTFS feed (newest monthly archive of the data.gov.lv timetable dataset) and
the VZD state address register (`aw_eka.csv`, ~140 MB; only existing Riga buildings are kept,
~49k), both for seven days, and Riga's streets from the Overpass API (roads, railways, water;
reduced to ~0.5 MB of encoded polylines) for ninety days, with two mirrors and the stale copy
as the last resort. SS.com addresses are matched against the register: first names dropped by
SS.com, abbreviated street types, block numbers (`k-1`) and `59/61` numbers are handled. A
missing house number falls back to the nearest number on the same street (`approx`, shown as ≈).

**Walking** follows OpenStreetMap: every `highway` way except motorways, trunks and the
like, and nothing marked `foot=no` or private without foot access, forms a graph cached as
`riga-walk.json` from the same download as the streets. A walk snaps its ends to the nearest
node within 150 m and takes the shortest path at 5 km/h; where no way is near or no path is
found, the straight line × 1.3 stands in. Stops still count as "near" by straight line
(`walk_m`, `transfer_walk_m`); only the time and the drawn line use the path.

**Journeys.** The timetable is reduced to one reference day, the next Monday–Friday without a
`calendar_dates` exception. RAPTOR runs backwards from each target's stops once per five-minute
arrival deadline, which gives the latest departure from every stop in the city, so three
targets cover all located listings in about a minute. Every journey found for a flat's stops
is a template; templates using the same stops merge into one option whose legs list every
route between those stops (bus 4/38/39 → tram 1). Each option is measured on the timetable:
**minutes** is the median door-to-door time over the window's departures (walks along the
streets, rides, transfer walks and waits; the wait for the first vehicle is not included), and **every N min** is the window length divided by departures, taken per leg
and reported as the rarest leg. Options sort by minutes plus half the headway, the expected
time when leaving at a random moment. A target is reached *directly* when an option has no
transfer; that count still orders the digest.

**Gates**, in order: price above `max_eur`, rooms outside `order` (unknown rooms pass), an
excluded building type, stove heating stated in the text, a sale as a share of property
("pārdod kā domājamā daļa"; the ordinary land share of a flat does not count).

**Ordering**, no score: price band (list order) → rooms (list order, unknown last) → layout
(isolated from the text › isolated by series prior › unknown › walk-through by series ›
walk-through from the text) → number of targets reached directly → €/m² relative to the
district median → price. Every column of that key is shown in the digest row.

**Digest marks**: ★ first seen after the previous digest, ↓ price dropped after the previous
digest. Removed candidates are counted in the header. **Flags**: stove mentioned, land leased
(a lease that is not negated, land not or only partly owned, a denationalised house), price
below 0.4× the district's €/m², under 20 m².

**Digest page**: `digest` also writes the folder `digest/<day>/`: `index.html` (the cards'
data inline, ~0.15 MB gzipped), `map.js` (every journey option with its walks, route shapes,
stops and streets; loaded with Leaflet from cdnjs only when the Map tab opens, so the list works
without either) and `photos/<ss_id>.jpg` (thumbnails, `.t.jpg`, ~5 KB, cached under
`$FLAT_SEARCHER_HOME/cache/thumbnails/`, loaded lazily).

- One card per flat: ads with the same building, floor, area and rooms and prices within 10 %
  share a card that lists the other ads and their prices.
- **Today** (the default view): candidates that pass the soft rules of `[today]`, a way to every
  target and no implausible-price or room-size flag; among them the flats no other beats at once
  on price, the summed expected journey time and floor area, then flats beaten only by those,
  up to `size`. **All** shows every candidate in the digest's order.
- A card shows, per target, the option with the shortest expected time (minutes + every/2) on
  one line: route chips, minutes, every N, changes, walk. It adds price history, company or
  private ad, land owned or leased, the local model's condition and mortgage risk when a
  verdict exists, and the listing's own words behind the layout and land facts.
- ★ favourite, ✕ hide and "opened" (set on opening the ad or its map) are stored in the
  browser, per ad, for every ad of the flat, and survive daily updates; so does the day of the
  previous visit for "new or cheaper since my last visit".
- The map colours flats by expected time to one target or to all (five quantile steps of one
  blue ramp, validated for light and dark) and stacks flats in one house into a numbered
  marker. **On the map** on a card opens the focus view: the flat, the targets and the chosen
  option per target along the route shape, with every option listed under the map.

To preview locally, serve the digest folder and open `http://127.0.0.1:8765/<day>/`:

```powershell
python -m http.server 8765 --bind 127.0.0.1 --directory data\library\digest
```

Output is counts only; target names are never printed.

## Sharing the page

`python -m flat_searcher publish [--day D]` pushes the folder `digest/<day>/` to the
GitHub Pages repository named by `FLAT_SEARCHER_PAGES_REPO`: one fresh commit each time,
force-pushed to `main`, so that repository never accumulates history. GitHub serves it at
`https://<owner>.github.io/<repository>/` on any phone, with the OSM tiles loading. `publish`
runs last in the daily loop.

Setup, once: create an empty public repository, run `publish`, then enable Pages for it
(Settings → Pages → branch `main`, root). git's own credentials authenticate (`gh auth
setup-git`); where there are none, set `FLAT_SEARCHER_PAGES_TOKEN`, a fine-grained token with
*Contents: write* on that repository only. The page carries `noindex`, but a public repository
is public and the page names your targets: share the link the way you would share an address.
The CLI prints `published day= files= bytes=` and never the repository or the token; a failure
prints the exception type only.

## First run and recovery

- **First backfill** indexes everything and queues everything. Expect ~40 minutes at one request
  per second for the whole of Riga; the queue that day is the entire corpus.
- **Interrupted run**: the library is written only by the last stage, and a git library is pushed
  only after that. Run it again.
- **Failed stage**: `meta.json` says which stage and why (exception type only); later stages are
  skipped. The job exits non-zero. Fix and re-run — every run starts from the library as it is.
- **Suspicious deactivations**: check `crawl_complete` in `meta.json`. Removal only happens after
  a complete crawl, and a removed listing that reappears is reactivated, never re-added.
- **Re-judging**: delete the relevant verdict lines and the listing's `judgment` key, or just
  write a newer verdict — later days win.
