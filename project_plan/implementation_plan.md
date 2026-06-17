# Flat Searcher: Detailed Implementation Plan

Source of truth: [original full Product Plan](source/flat_searcher_plan_v01.txt).

This file does not replace the original specification. The original Product Plan contains
2000+ lines of product requirements, rules, examples and edge cases. This document is the
implementation roadmap: it groups the source requirements into technical phases that can
be built, tested and reviewed step by step.

If this roadmap and the original Product Plan disagree, the original Product Plan wins.

The original document is stored inside the project at:

`project_plan/source/flat_searcher_plan_v01.txt`

## Traceability To The Source Specification

1. Product concept, core principle and UI language: original sections 1-3.
2. SS.com collection, caching and update logic: original sections 4-5.
3. Image display policy and no technical AI/debug UI: original sections 6-7.
4. Apartment naming and detail view: original sections 8-9.
5. Gemini two-pass pipeline and internal schemas: original sections 10-13.
6. Room classification and layout scoring: original sections 14-15.
7. Mortgage suitability and bankability: original section 16.
8. Price-value and market comparison: original sections 17-18.
9. Profiles, scoring blocks and score formula: original sections 19-21.
10. Filters, views, user workflow and listing history: original sections 22-25.
11. Geocoding, location scoring and map: original sections 26-28.
12. Main tabs, ranking rows and data model: original sections 29-31.
13. Layout prior database: original section 32.
14. MVP roadmap and technical decisions: original sections 33-34.
15. Final product summary: original section 35.

## Product Goal

Flat Searcher is a Python desktop application for analyzing apartments for sale in Riga.
The first source is the SS.com Riga apartment sale section:

`https://www.ss.com/lv/real-estate/flats/riga/all/sell/`

The application must not be just a scraper or map viewer. Its core value is turning messy
apartment listings into structured, explainable apartment candidates. The user should be
able to understand why an apartment ranks high or low, what is uncertain, what conflicts
with the seller's data, and which risks require manual checking.

The default search profile is:

`For living + mortgage`

The main target user scenario is finding a good apartment for living, with special attention
to mortgage suitability, reasonable price/value, usable private rooms, acceptable location
and nearby infrastructure.

## Core Product Rules

1. All user-facing UI text must be in English.
2. SS.com fields are useful signals, not trusted truth.
3. Deeper interpretation comes from listing text, images, AI, optional layout priors,
   geodata and market comparison.
4. The app must evaluate the real usefulness of the apartment, not only the seller's room
   count or marketing language.
5. Ordinary listing photos are used internally by AI and must not be shown in the apartment
   detail view.
6. The only image type that may be shown to the user is a floor plan image.
7. Raw AI JSON, prompts, full model output, debug logs and technical pipeline state must not
   be shown in the UI.
8. The app must expose uncertainty clearly: unclear layout, SS/AI room conflict, approximate
   address, suspicious low price and mortgage risk.
9. High mortgage risk does not automatically hide a listing. It lowers score and adds warnings.
10. Price history and unique visits are context only. They do not affect ranking.
11. Filters are separate from ranking. Filters decide what is visible; ranking sorts the visible
    set.
12. Location-sensitive scores are calculated only for exact high-confidence addresses.

## Recommended Architecture

The project should be built as a local analytical system with a desktop UI on top. The UI
should not own parsing, AI analysis, geocoding or scoring logic. Each subsystem should be
usable from CLI commands and testable without launching the GUI.

Recommended stack:

1. Desktop UI: `PySide6`.
2. Map: embedded `Leaflet` through Qt WebEngine.
3. Database: SQLite first.
4. Data validation: `Pydantic`.
5. Database access: initially direct SQLite helpers, later SQLAlchemy if the repository layer
   grows.
6. Migrations: a lightweight schema version table first, Alembic later if needed.
7. HTML fetching: `httpx`.
8. HTML parsing: `selectolax` or BeautifulSoup fallback.
9. CLI: standard `argparse` initially, Typer later if the command surface grows.
10. AI integration: isolated Gemini service layer with schema validation and retries.
11. Geodata: cached geocoder and cached OSM/Overpass data.
12. Tests: `pytest`, saved HTML fixtures and mocked AI/geocoder responses.

Recommended package layers:

1. `scraper`: SS.com list/detail fetching and parsing.
2. `db`: schema, bootstrap, repositories and history storage.
3. `models`: shared typed models and enums.
4. `ai`: Gemini prompts, schemas, image classification and layout/mortgage analysis.
5. `geo`: address parsing, geocoding, POI cache and distance scoring.
6. `scoring`: score blocks, profiles, penalties and explanations.
7. `ui`: PySide6 screens, table models, detail view and map bridge.
8. `cli`: sync, analyze, recalculate and diagnostics commands.

## Step 01. Project Foundation

### Goal

Create the Python project foundation so later MVP work does not mix parser code, AI code,
database code and UI code.

### Source Requirements Covered

The source specification describes many independent subsystems: SS.com parsing, caching,
history, two-pass Gemini analysis, geocoding, market comparison, scoring profiles, ranking,
map, filters and user workflow. A modular foundation is required before implementing any
single feature deeply.

### Implementation Tasks

1. Create `pyproject.toml`.
2. Create the `src/flat_searcher` package.
3. Add subpackages:
   - `scraper`;
   - `db`;
   - `ai`;
   - `geo`;
   - `scoring`;
   - `ui`;
   - `models`.
4. Add runtime configuration based on environment variables.
5. Add basic logging.
6. Add a CLI with at least:
   - `show-config`;
   - `init-db`.
7. Add initial SQLite schema version `001`.
8. Add project documentation and `.env.example`.
9. Add ignored runtime folders for local database, image cache and temporary files.
10. Verify the package compiles and the database can be initialized.

### Deliverables

1. Package skeleton.
2. Runtime configuration.
3. SQLite schema bootstrap.
4. Basic CLI.
5. Project README.
6. Planning folder with original spec and implementation roadmap.

### Acceptance Criteria

1. `python -m flat_searcher show-config` works with `PYTHONPATH=src`.
2. `python -m flat_searcher init-db --database <path>` creates a SQLite database.
3. Source files compile without syntax errors.
4. The initial schema includes the core tables needed by future MVP steps.

## Step 02. SS.com Parser And Local Database

### Goal

Collect active apartment sale listings from SS.com and store normalized raw listing data in
the local database.

### Source Requirements Covered

The source specification requires the app to start from the Riga apartment sale page:

`https://www.ss.com/lv/real-estate/flats/riga/all/sell/`

From the list page, the app must collect:

1. Listing URL.
2. Listing ID.
3. District.
4. Street.
5. Declared room count.
6. Area.
7. Floor.
8. Building series/type if available.
9. Price.
10. Price per square meter if available.
11. Listing table metadata.

From the detail page, the app must collect:

1. Full listing description.
2. Full address data available on SS.
3. Price.
4. Area.
5. Declared rooms.
6. Floor and total floors.
7. Series/type of building.
8. Heating or utility-related text if present.
9. Listing date or update date.
10. Unique visit count.
11. Image URLs.
12. Raw text snapshot.
13. Basic HTML snapshot/hash.
14. Link to the original SS listing.

The original listing text must be preserved as a source block for the apartment detail view.

### Implementation Tasks

1. Implement an HTTP client with headers, timeout, retry and respectful request pacing.
2. Save example HTML fixtures for a listing page and several detail pages.
3. Implement `ListingListParser`.
4. Implement `ListingDetailParser`.
5. Extract `ss_id` from URLs.
6. Normalize prices, area, price per square meter, rooms and floors.
7. Preserve raw text snapshot.
8. Store description hash and raw HTML hash.
9. Store image URLs separately.
10. Add parser tests based on saved fixtures.
11. Add a CLI command such as `sync-listings` when parser and repository code are ready.

### Data Storage

Use the `listings` table for current normalized values and `listing_images` for image URL
metadata. Avoid storing ordinary image binaries at this stage.

### Risks And Notes

1. SS.com HTML may change, so parser tests with fixtures are essential.
2. The site may provide street without house number, which must be stored as approximate
   address data.
3. Text encoding should be handled carefully because Latvian characters and currency symbols
   matter in user-visible explanations.

### Acceptance Criteria

1. Parser works on local fixtures without network access.
2. Parser extracts stable `ss_id`.
3. Missing fields do not crash parsing.
4. Parsed data can be inserted or updated in SQLite.
5. Original listing URL and original listing text are preserved.

## Step 03. Caching, Change Detection And Listing History

### Goal

Avoid reprocessing everything on every launch and preserve listing history.

### Source Requirements Covered

On first launch, the application should collect active listings, open details, store raw data,
download images temporarily for AI analysis, run AI analysis, store structured output, remove
temporary ordinary images and preserve necessary metadata.

On later launches, the app should:

1. Recheck the SS.com listing page.
2. Detect new listings.
3. Detect removed or inactive listings.
4. Detect changed price.
5. Detect changed listing text.
6. Detect changed image count.
7. Detect changed unique visit count.
8. Run full AI analysis only for new or sufficiently changed listings.
9. Recalculate scores.
10. Preserve history.

Listing history should track:

1. Price history.
2. Unique visit history.
3. Description changes.
4. Image count changes.
5. First detected date.
6. Last checked date.
7. Last seen date.
8. Active/inactive status.
9. Whether a listing disappeared.
10. Whether it returned.

### Implementation Tasks

1. Create an `app_runs` record for every sync/analyze run.
2. Create a `listing_snapshots` row for each checked listing.
3. Compare the current snapshot with the previous snapshot.
4. Create `listing_change_events` for:
   - `price_changed`;
   - `description_changed`;
   - `image_count_changed`;
   - `unique_visits_changed`;
   - `listing_became_inactive`;
   - `listing_reactivated`.
5. Mark listings missing from the current SS.com list as inactive.
6. Keep inactive listings in the database.
7. Preserve favorites even if a listing becomes inactive.
8. Add a future-ready `needs_ai_analysis` decision function.

### Acceptance Criteria

1. A repeated sync does not duplicate listings.
2. Price changes create history events.
3. Removed listings are not deleted.
4. Returned listings become active again.
5. History can support the detail view required by the spec.

## Step 04. Image Downloading And Storage Policy

### Goal

Download images temporarily for AI analysis while respecting the product rule that ordinary
listing photos are not permanently stored or shown to the user.

### Source Requirements Covered

The source specification says images should generally not be stored permanently. Ordinary
photos are used internally by AI and then removed. The exception is a floor plan image, which
may be cached because it directly explains the layout conclusion in the UI.

The apartment detail view must not show:

1. Room photos.
2. Kitchen photos.
3. Bathroom photos.
4. Facade photos.
5. Staircase photos.
6. Yard photos.
7. Street view photos.
8. Agency collages.

Only floor plan images may be shown.

### Implementation Tasks

1. Create a per-run temporary image directory.
2. Download images for a listing before AI analysis.
3. Store image metadata: URL, content hash, mime type, width and height if available.
4. Pass local files to the AI pipeline.
5. Delete ordinary temporary images after analysis.
6. Preserve floor plan images identified by the AI pipeline.
7. Store cached floor plan paths in the database.
8. Add cleanup logic for failed or interrupted runs.

### Acceptance Criteria

1. Ordinary photos are removed after analysis.
2. Floor plans can be displayed in detail view.
3. Listing image metadata remains available for change detection.
4. UI cannot accidentally render ordinary listing photos.

## Step 05. Gemini Pass 1: Image Classification And Grouping

### Goal

Classify all listing images without discarding useful layout evidence too aggressively.

### Source Requirements Covered

The first AI pass must:

1. Classify all images.
2. Find floor plan images.
3. Mark useless images.
4. Detect near-duplicates.
5. Group interior photos by likely room.
6. Detect corridor/hallway/entrance photos.
7. Detect kitchen, bathroom and living room photos.
8. Mark exterior/building photos.
9. Identify photos useful for layout reasoning.
10. Identify photos useful for building/mortgage analysis.

Image categories:

1. `floor_plan`.
2. `interior_room`.
3. `kitchen`.
4. `bathroom`.
5. `corridor_or_hallway`.
6. `exterior_building`.
7. `entrance_staircase`.
8. `yard_or_street_view`.
9. `duplicate_or_near_duplicate`.
10. `irrelevant_or_decorative`.
11. `agency_collage`.

### Implementation Tasks

1. Define a Pydantic schema for Pass 1 output.
2. Build a prompt template for image classification.
3. Include all image IDs and metadata in the prompt.
4. Validate Gemini output strictly.
5. Mark useful interior photos for Pass 2.
6. Mark exterior/building images for building risk analysis.
7. Store technical output internally only.
8. Persist floor plan image IDs.

### Acceptance Criteria

1. Pass 1 output validates against schema.
2. Floor plan candidates are identified.
3. Interior images useful for layout are not over-filtered.
4. Duplicate and irrelevant images are marked.
5. Pass 1 data is not shown in the user-facing UI.

## Step 06. Gemini Pass 2: Layout, Mortgage And Explanation Reasoning

### Goal

Determine the real apartment layout, effective private rooms, mortgage risk and user-facing
explanations.

### Source Requirements Covered

Pass 2 receives:

1. Full listing text.
2. SS table fields.
3. Useful interior photos.
4. Floor plan image if found.
5. Pass 1 image classification.
6. Image groups by likely room.
7. Exterior/building photos for building risk analysis.
8. Optional layout priors if available.

Pass 2 determines:

1. Real layout.
2. Effective private rooms.
3. Walkthrough rooms.
4. Kitchen-living cases.
5. SS vs AI room conflict.
6. Building type guess.
7. Series guess.
8. Wooden building risk.
9. Stove heating risk.
10. Mortgage risk level.
11. Human-readable explanations.

### Required Output Fields

Layout fields:

1. `ai_detected_living_rooms`.
2. `effective_private_rooms`.
3. `walkthrough_rooms`.
4. `kitchen_living_detected`.
5. `separate_kitchen_detected`.
6. `layout_class`.
7. `layout_confidence_label`.
8. `ss_vs_ai_room_conflict`.
9. `layout_explanation_user`.
10. `floor_plan_image_ids`.

Building and mortgage fields:

1. `building_type_guess`.
2. `series_guess`.
3. `wooden_building_risk`.
4. `stove_heating_risk`.
5. `mortgage_risk_level`.
6. `mortgage_risk_reasons`.

### Implementation Tasks

1. Define Pydantic schemas for Pass 2 output.
2. Define allowed enum values for confidence and risk levels.
3. Build a Pass 2 prompt template.
4. Add strict JSON parsing and retry on invalid output.
5. Store raw model output internally.
6. Store user-facing explanations separately.
7. Add analysis versioning.
8. Add failure handling so one bad listing does not break a full run.

### Acceptance Criteria

1. `layout_confidence_label` is one of `Confirmed`, `Likely`, `Unclear`, `Conflict`.
2. `mortgage_risk_level` is one of `Low`, `Medium`, `High`, `Critical`, `Unknown`.
3. User-facing explanations are plain English.
4. Raw JSON and prompts are never rendered in UI.

## Step 07. Product Rules For Room Classification

### Goal

Convert AI conclusions into stable product behavior for effective private rooms, walkthrough
rooms and kitchen-living cases.

### Source Requirements Covered

A private room is a living room that can be entered from a neutral area such as corridor,
hallway or entrance hall. A person should not need to pass through another person's private
room to access their own room.

A walkthrough room is a living room that must be crossed to enter another living room. For
rating, walkthrough rooms are not counted as private rooms.

Kitchen is not counted as a living room. Kitchen quality is not scored as a separate factor.

Kitchen-living is not counted as a full private room.

Product rule:

`1 bedroom + kitchen-living != good 2-room apartment`

Evidence priority:

1. Floor plan image.
2. Interior photos as cross-check.
3. Listing text.
4. SS-declared room count.
5. Typical layout prior.

### Implementation Tasks

1. Add deterministic post-processing after AI analysis.
2. Generate room-related flags:
   - `Room conflict`;
   - `Layout unclear`;
   - `Layout confirmed by floor plan`;
   - `AI: 2 private / SS: 3`;
   - `Kitchen-living is not counted as private room`.
3. Normalize effective private room count.
4. Normalize walkthrough room count.
5. Normalize kitchen-living detection.
6. Detect conflict between SS rooms and AI-effective rooms.

### Acceptance Criteria

1. Kitchen-living does not increase private room score.
2. Walkthrough rooms are not counted as private rooms.
3. SS/AI conflict produces a visible warning.
4. A floor plan affects explanation and confidence.

## Step 08. Mortgage Suitability And Bankability

### Goal

Calculate mortgage suitability separately from overall apartment attractiveness.

### Source Requirements Covered

Mortgage suitability should be represented by:

1. `mortgage_bankability_score`.
2. `mortgage_risk_level`.
3. `mortgage_risk_reasons`.

Risk factors:

1. Stove heating.
2. Wooden building.
3. Wooden building plus stove heating.
4. Legal risks.
5. Land lease.
6. Shared ownership.
7. Encumbrances.
8. Auction.
9. Building not commissioned.
10. Poor building condition.
11. Poor facade condition.
12. Low liquidity.
13. Missing utilities.
14. No normal bathroom/water/sewer infrastructure.
15. Very incomplete or suspicious listing.

Risk severity:

1. Stove heating: `High` or `Critical`.
2. Wooden building: `High`.
3. Wooden building plus stove heating: `Critical`.
4. Legal/ownership complications: `High` or `Critical`.
5. Building not commissioned: `Critical`.

### Implementation Tasks

1. Implement mortgage risk rules.
2. Combine AI evidence with listing text keywords.
3. Store risk level, score and reasons.
4. Generate user-facing evidence text.
5. Add warning flags:
   - `High mortgage risk`;
   - `Stove heating risk`;
   - `Wooden building risk`.
6. Make mortgage impact profile-dependent.

### Acceptance Criteria

1. High-risk listings remain visible by default.
2. Mortgage risk lowers ranking through the mortgage block.
3. Reasons are visible in the apartment detail view.
4. `Mortgage first` can make mortgage risk much more important later.

## Step 09. Geocoding And Address Precision

### Goal

Determine coordinates and ensure location scoring is calculated only when the address is
precise enough.

### Source Requirements Covered

Exact address means:

`street + house number`

Address precision levels:

1. `exact_house`.
2. `street_approx`.
3. `district_approx`.
4. `unknown`.

Geocode confidence levels:

1. `high`.
2. `medium`.
3. `low`.

For MVP scoring, only `exact_house + high confidence` enables location scores.

For `street_approx`, the app may show an approximate marker but must not calculate location
scores. The UI should show:

`Approximate address - location scores not calculated`

### Implementation Tasks

1. Parse street and house number from SS address data.
2. Normalize address strings.
3. Query a geocoding provider.
4. Cache geocoding responses.
5. Classify precision and confidence.
6. Store latitude, longitude, source and explanation.
7. Set `geo_scores_enabled`.
8. Set `geo_scores_disabled_reason`.

### Acceptance Criteria

1. Missing house number disables location scores.
2. Approximate listings can still appear on the map.
3. Unknown coordinate listings remain in ranking if filters allow them.
4. The app does not give false location bonuses.

## Step 10. Location Score Blocks

### Goal

Calculate simple distance-based location scores for exact high-confidence addresses.

### Source Requirements Covered

For MVP, the app must calculate distances, not routes or travel time.

Do not calculate:

1. Walking time.
2. Public transport travel time.
3. Route planning.
4. Transfers.
5. Frequency.
6. Schedules.
7. Claims such as "can reach in 10 minutes".

Location blocks:

1. RTU score using one main RTU point.
2. Central station score using Riga Central / Origo as one destination zone.
3. Shop score using grocery stores.
4. Simple transport score using nearby stops.

### Implementation Tasks

1. Define main RTU coordinate.
2. Define central station / Origo coordinate.
3. Build Haversine distance utilities.
4. Cache grocery stores from OSM/Overpass.
5. Cache transport stops from OSM/Overpass if practical.
6. Count shops within 300m, 700m and 1200m.
7. Count nearby transport stops if data is available.
8. Calculate smooth scores instead of hard filters.
9. Generate user-facing explanations.
10. Disable scoring for non-exact addresses.

### Acceptance Criteria

1. Approximate addresses show disabled location scores.
2. RTU, station, shop and transport blocks explain distance-based values.
3. No route-time or schedule claims appear in UI.

## Step 11. Price-Value Score And Market Baselines

### Goal

Score value relative to the market and apartment usefulness, not just absolute cheapness.

### Source Requirements Covered

There is no default budget. Do not define soft limits such as up to 100k, up to 120k or
above 130k is bad.

Default principle:

`Price/value is more important than absolute cheapness.`

Components:

1. `price_per_m2_score`.
2. `relative_market_score`.
3. `price_per_effective_private_room_score`.
4. `absolute_price_score`.
5. `suspicious_low_price_flag`.

Baseline levels:

1. Riga baseline.
2. District baseline.
3. AI-adjusted baseline.
4. Series/building baseline.

Exclude from normal market baselines:

1. Critical mortgage risk.
2. Obvious stove heating plus wooden building.
3. Severe legal risk.
4. Inactive listings.
5. Very incomplete listings.
6. Extreme price/area outliers.
7. Obvious data errors.

Use median, percentiles or trimmed mean rather than simple average.

### Implementation Tasks

1. Build comparable listing selectors for each baseline level.
2. Implement outlier detection.
3. Compute median price per square meter.
4. Choose the strongest reliable baseline by sample size.
5. Calculate price per effective private room only when room confidence is sufficient.
6. Calculate suspiciously low price warning.
7. Store baseline level, sample size, median and explanation.
8. Ensure price history is not used in rating.

### Acceptance Criteria

1. Cheap listings can receive positive value score.
2. Abnormally cheap listings also receive warning.
3. Absolute price is a weak default factor.
4. Price history remains context only.

## Step 12. Scoring Profiles And Weighted Score Engine

### Goal

Create a profile-based score engine where each block returns a score and the profile controls
importance.

### Source Requirements Covered

The user changes only the importance of scoring blocks, not their direction.

Importance levels:

1. `Ignore` = 0.
2. `Weak factor` = 1.
3. `Medium factor` = 2.
4. `Strong factor` = 3.
5. `Critical factor` = 5.

Formula concept:

`overall_score = weighted_average(block_scores) - penalties`

Default profile:

`For living + mortgage`

Default importance order:

1. Price value.
2. Room privacy / effective private rooms.
3. Mortgage suitability.
4. RTU accessibility.
5. Transport connectivity.
6. Central station accessibility.
7. Shops / infrastructure.
8. Useful area.
9. Building / series.
10. AI confidence.

Disabled by default:

1. Floor.
2. Condition / renovation.
3. Views.

Views are not profile blocks at all.

### Implementation Tasks

1. Define a scoring block interface.
2. Implement core blocks:
   - price value;
   - useful area;
   - room privacy;
   - layout confidence;
   - mortgage suitability;
   - RTU accessibility;
   - transport connectivity;
   - central station accessibility;
   - shops / infrastructure;
   - building / series;
   - floor;
   - condition / renovation.
3. Implement default built-in profile.
4. Implement weighted average.
5. Implement penalties.
6. Implement tie-breakers.
7. Store score breakdown and explanation.

### Acceptance Criteria

1. Every active block returns `0..100`.
2. Views cannot affect score.
3. Filters do not alter score.
4. Tie-breakers are applied only when scores are close.
5. Score breakdown is available for the detail view.

## Step 13. Apartment Detail View

### Goal

Create a clean detail view that explains the apartment without exposing technical internals.

### Source Requirements Covered

The apartment detail card must include:

1. Top block.
2. Flags.
3. Rating block.
4. Layout block.
5. Mortgage risk block.
6. Location block.
7. Listing history block.
8. Original listing text.

Top block shows:

1. Generated apartment title.
2. Price.
3. Area.
4. Price per square meter.
5. District.
6. Street.
7. Floor.
8. Building series/type.
9. Listing date or update date.
10. Link to original SS listing.

The layout block shows:

1. AI-effective private rooms.
2. SS-declared rooms.
3. Walkthrough room status.
4. Kitchen-living status.
5. Confidence label.
6. Human-readable explanation.
7. Floor plan image if found.

The UI must not show raw AI JSON, prompt text, full model output, debug logs, internal image
classification, confidence tables or technical pipeline state.

### Implementation Tasks

1. Build the PySide6 detail screen.
2. Define English labels for every field.
3. Render generated title.
4. Render flags.
5. Render score breakdown.
6. Render layout explanation.
7. Render floor plan image only when available.
8. Render mortgage risk reasons.
9. Render disabled location score explanations.
10. Render listing history.
11. Render original listing text.
12. Add original SS.com link.

### Acceptance Criteria

1. All UI labels are English.
2. Ordinary listing photos are never shown.
3. Floor plan image can be shown.
4. Raw AI/debug information is hidden.
5. The original listing text is preserved and visible.

## Step 14. Ranking List And Filters

### Goal

Create the main ranked apartment list and filter controls.

### Source Requirements Covered

Example row:

`#12 - Purvciems - AI: 2 private / SS: 3 - 48 m2 - 92 000 EUR - Score 78`

The row should show:

1. Position.
2. Generated apartment title.
3. Score for selected profile.
4. Key flags.
5. Price.
6. EUR/m2.
7. Area.
8. Effective private rooms.
9. Layout confidence.
10. Mortgage risk.
11. RTU indicator.
12. Transport indicator.
13. Shop indicator.
14. View activity indicator.

Possible filters:

1. Price from/to.
2. Area from/to.
3. District.
4. SS-declared rooms.
5. Effective private rooms.
6. Only without room conflict.
7. Only confirmed layout.
8. Hide high mortgage risk.
9. Hide stove heating.
10. Hide wooden buildings.
11. Only with floor plan image.
12. Only good transport.
13. Only near RTU.
14. Only near central station.
15. New today.
16. New this week.
17. New since last launch.
18. Active only.
19. Show inactive.
20. Hide viewed.
21. Hide rejected.
22. Favorites only.

### Implementation Tasks

1. Build a query service for the currently filtered dataset.
2. Build the ranking table model.
3. Add position calculation within the filtered set.
4. Add filter state model.
5. Add filter UI.
6. Hide rejected by default.
7. Show active listings by default.
8. Ensure map and ranking consume the same filtered dataset.

### Acceptance Criteria

1. Filters decide visibility only.
2. Ranking answers "which visible apartment is better?"
3. Map and ranking are synchronized.
4. Rejected listings are hidden by default but not deleted.

## Step 15. User Statuses And Workflow

### Goal

Turn the app into a candidate management tool for an apartment search.

### Source Requirements Covered

Required MVP statuses:

1. `new`.
2. `unseen`.
3. `viewed`.
4. `favorite`.
5. `rejected`.
6. `inactive`.

Technical listing status and user status are separate.

Rejected listings:

1. Are hidden from the main ranking by default.
2. Stay in the database.
3. Can be viewed through a filter or separate tab.
4. Preserve history.

Favorites:

1. Appear in a separate tab.
2. Are available through a filter.
3. Have a special map marker style.
4. Remain preserved even when inactive.

### Implementation Tasks

1. Create user state repository.
2. Add actions:
   - mark viewed;
   - favorite/unfavorite;
   - reject/unreject.
3. Add New tab.
4. Add Favorites tab.
5. Add Rejected tab.
6. Add Inactive tab.
7. Mark opened new listings as viewed unless favorite/rejected.
8. Preserve favorite state independently from listing active status.

### Acceptance Criteria

1. Inactive favorites remain accessible.
2. Rejected listings remain in the database.
3. New since last launch can be displayed.
4. User workflow fields do not affect score.

## Step 16. Geodata-Aware Map

### Goal

Show apartments on a synchronized Riga map with markers, clusters and uncertainty styles.

### Source Requirements Covered

The map shows only the currently filtered set of apartments. If a listing is hidden by filters,
it disappears from the map.

Marker types:

1. Normal marker for exact address.
2. Approximate marker for street without house number.
3. District marker for district-only precision.
4. Favorite marker.
5. Rejected marker, muted if shown.
6. Inactive marker, grey/muted if shown.

Marker color should reflect score under the current profile. Approximate markers should have
reduced visual confidence.

Clusters:

1. Show apartment count.
2. Click zooms in.
3. Cluster click does not open a list or popup in MVP.

Map should show:

1. Apartments.
2. Clusters.
3. Main RTU point.
4. Riga Central / Origo.
5. Grocery stores used in scoring.

### Implementation Tasks

1. Embed Leaflet in a PySide6 WebEngine view.
2. Build a Python-to-JavaScript bridge or regenerate map JSON.
3. Send filtered listing data to the map.
4. Implement marker styles.
5. Implement marker clustering.
6. Implement cluster zoom behavior.
7. Add special markers for RTU, station and grocery stores.
8. Add indicator such as `Showing 143 of 618 apartments`.

### Acceptance Criteria

1. Map and ranking use the same filtered dataset.
2. Approximate markers are visually distinct.
3. Cluster click zooms in.
4. Rejected/inactive markers are muted when shown.

## Step 17. Preset And Custom Profiles

### Goal

Allow different search strategies while keeping scoring directions controlled by the app.

### Source Requirements Covered

Preset profiles:

1. `For living + mortgage`.
2. `Mortgage first`.
3. `Maximum opportunity`.
4. `Only 2 private rooms`.
5. `Best price`.
6. `Best transport`.
7. `Closer to RTU`.
8. `Cash purchase`.
9. `Investment option`.

The profile editor uses:

`Ignore | Weak factor | Medium factor | Strong factor | Critical factor`

The user can:

1. Drag blocks between importance levels.
2. Place blocks next to each other to give equal importance.
3. Disable blocks.
4. Re-enable blocks.
5. Save as a new profile.
6. Rename profile.

The user does not configure raw mathematical direction.

### Implementation Tasks

1. Store built-in profiles.
2. Store custom profiles.
3. Build a profile selector.
4. Build profile editor UI.
5. Add block enable/disable support.
6. Add profile save and rename.
7. Recalculate ranking when the selected profile changes.
8. Update marker colors when the profile changes.

### Acceptance Criteria

1. Custom profiles persist.
2. Profile changes update ranking and map colors.
3. User cannot invert block direction.
4. Views remain unavailable as a scoring block.

## Step 18. Notes, Comparison And Search Sessions

### Goal

Support longer apartment search workflows after the core MVP is usable.

### Source Requirements Covered

Notes examples:

1. `Call seller`.
2. `Check heating`.
3. `Looks like agency`.
4. `Ask about land`.
5. `Bad layout but good price`.

Comparison view should compare 2-5 apartments by:

1. Price.
2. EUR/m2.
3. Area.
4. Effective private rooms.
5. Layout confidence.
6. Mortgage risk.
7. RTU distance.
8. Transport.
9. Central station.
10. Shops.
11. Building/series.
12. AI summary.
13. Flags.
14. Pros/cons.

Search sessions store:

1. Session name.
2. Selected scoring profile.
3. Filters.
4. Sort mode.
5. Hidden statuses.
6. Created date.
7. Updated date.

### Implementation Tasks

1. Add user notes UI.
2. Show note indicator in ranking rows.
3. Add comparison basket.
4. Add comparison tab for 2-5 apartments.
5. Add saved search sessions.
6. Restore profile, filters and sort mode from a session.

### Acceptance Criteria

1. Notes are visible in detail view.
2. Notes can be indicated in the ranking list.
3. Comparison supports 2-5 apartments.
4. Search sessions restore filters and profile.

## Step 19. Layout Prior Database

### Goal

Add optional local knowledge about typical building layouts without allowing it to override
real listing evidence.

### Source Requirements Covered

The application may use a local database of typical building layouts. Gemini does not query
this database directly. The application retrieves relevant prior layouts and includes a small
number of candidates in the prompt.

Pipeline:

1. Extract listing features.
2. Search local layout prior database.
3. Retrieve 3-10 relevant candidates.
4. Pass them to Gemini as hypotheses.
5. Gemini uses them as supporting context, not truth.

Important rule:

Typical layout prior cannot override real evidence.

### Implementation Tasks

1. Create `layout_priors` table.
2. Store fields:
   - series name;
   - building type;
   - construction period;
   - typical area range;
   - typical room count;
   - typical layout variants;
   - walkthrough probability;
   - isolated rooms probability;
   - source note;
   - confidence;
   - verified flag.
3. Build lookup by series/building type/area/room count.
4. Add a compact prior summary to the Pass 2 prompt.
5. Add tests that floor plan evidence wins over priors.

### Acceptance Criteria

1. Layout priors are optional.
2. Gemini receives priors only as hypotheses.
3. Floor plan and real images override priors.

## Step 20. Testing, Reliability And Packaging

### Goal

Make the project reliable enough for repeated local use.

### Implementation Tasks

1. Parser tests using saved SS.com HTML fixtures.
2. Database bootstrap tests.
3. Change detection tests.
4. AI schema validation tests with mocked Gemini output.
5. Scoring unit tests.
6. Geocoding cache tests.
7. Map data serialization tests.
8. Basic UI smoke tests where practical.
9. Backup/export command for SQLite database.
10. Windows packaging strategy after the MVP stabilizes.

### Acceptance Criteria

1. Existing analyzed listings survive app updates.
2. Invalid AI response does not break a full run.
3. SS.com HTML changes are detected by parser test failures.
4. User can back up/export the local database.
5. Runtime data is not accidentally committed.

## Suggested Build Order

1. Finish Step 01 foundation.
2. Build Step 02 parser with local fixtures before heavy UI work.
3. Add Step 03 history and change detection.
4. Add Step 04 image downloading.
5. Add Steps 05-08 AI layout and mortgage analysis.
6. Add Steps 11-12 price-value and scoring.
7. Build Step 13 detail view and Step 14 ranking list.
8. Add Steps 09-10 geocoding and location scores.
9. Add Step 16 map.
10. Add Step 15 workflow statuses.
11. Add profiles, notes, comparison and sessions after the core loop works.

## Current Implemented Foundation

The repository currently contains the first foundation and parser/storage layers:

1. Python package skeleton.
2. Basic runtime configuration.
3. CLI entry point.
4. SQLite schema bootstrap and idempotent additive migrations.
5. Initial database tables for listings, images, snapshots, change events, AI analysis,
   geocoding, location scores, price-value analysis, scoring profiles, score results,
   user listing states and search sessions.
6. Original full Product Plan copied into `project_plan/source`.
7. SS.com list parser based on saved HTML fixtures.
8. SS.com detail parser for description, fields, listing date, unique visits and image URLs.
9. Listing repository with upsert, snapshots, image URL metadata, user state creation and
   change events.
10. `sync-listings` CLI command for limited or full SS.com sync runs.
11. Temporary image downloader with run cleanup and floor plan cache support.
12. Internal AI schemas for Pass 1 image analysis and Pass 2 layout/mortgage analysis.
13. Prompt builders for the two-pass Gemini pipeline.
14. AI pipeline service over an abstract model client.
15. Deterministic product rules for layout and mortgage flags.
16. Address precision, distance and location score helpers.
17. Default scoring profile, weighted score engine and core deterministic scoring blocks.
18. Price-value market baseline foundation.
19. User workflow state repository.
20. Generated apartment title formatting.
21. Filter and ranking services for synchronized ranking/map candidate sets.
22. Serializable map marker payloads for future Leaflet integration.
23. Persistent read repository for ranking, detail and map surfaces.
24. UI-facing view models with English display text.
25. CLI diagnostics for ranking, listing detail and map markers.
26. Optional PySide6 desktop shell for ranking/detail inspection.
27. Geocoding provider abstraction, Nominatim implementation and persistence service.
28. Fixture-based parser, repository, image, AI, geo, scoring, filtering, ranking, map,
    read-model and geocoding tests.

## Implementation Decisions Added During Build

1. The current parser layer uses only the Python standard library. External HTML dependencies
   can be introduced later if SS.com parsing becomes too brittle, but the current tree helper
   keeps the first parser testable and dependency-light.
2. Schema version `002` adds parser/storage fields that were missing from the initial
   foundation: listing title, listing summary text, table metadata, detail fields and
   `needs_ai_analysis`.
3. If a detail page fetch fails during sync, existing detailed data is preserved instead of
   being overwritten by empty list-page data.
4. `sync-listings --limit N` never marks missing listings inactive. Inactive marking is only
   safe for full syncs.
5. Parser storage preserves raw SS.com numeric values. Impossible-looking values, such as an
   apartment area that appears as `503 m2`, are not corrected in the parser. They should be
   handled later by data quality, outlier and price-value scoring logic.
6. The AI layer starts with strict internal contracts before API wiring. This keeps Gemini
   integration replaceable and makes malformed model output testable.
7. Ranking and map foundations both consume filtered candidates. This keeps the product rule
   that filters decide visibility and ranking decides order.
8. Presentation helpers intentionally produce English strings only.
9. PySide6 is an optional UI dependency. Core parsing, storage, scoring, geocoding and tests
   do not import PySide6.
10. Geocoding is provider-based. Tests use fake providers; live Nominatim calls are behind the
    `geocode-listings` CLI command.

Next recommended implementation task:

Add persistent AI-analysis execution/storage commands, then connect score recalculation to
the existing score repository tables.
