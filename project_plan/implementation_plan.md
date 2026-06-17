# Flat Searcher: подробный план реализации

Источник: `C:/Users/Chtean45m/Downloads/flat_searcher_plan_v01.txt`.

Цель проекта: Python desktop-приложение для анализа квартир на продажу в Риге с SS.com. Продукт должен не просто собирать объявления, а превращать хаотичные данные в объяснимый рейтинг кандидатов: реальная полезность планировки, ипотечные риски, относительная цена, геоданные, история изменений, фильтры, карта и пользовательский workflow.

## Базовые продуктовые правила из ТЗ

1. UI-тексты только на английском.
2. SS.com fields считаются первичными сигналами, но не истиной.
3. Главные источники интерпретации: listing text, images, AI, optional layout priors, geodata, market comparison.
4. Обычные фотографии не показываются пользователю в detail view; можно показывать только floor plan image.
5. Raw AI JSON, prompts, debug logs и technical pipeline state не показываются в UI.
6. Location-sensitive scores считаются только для `exact_house + high confidence`.
7. Views / unique visits не влияют на рейтинг.
8. Filters separate from ranking: фильтр определяет видимость, score определяет порядок внутри видимого набора.
9. High mortgage risk не скрывает объявление автоматически, а снижает score и добавляет warning.
10. Price history является контекстом, но не влияет на rating.
11. Приложение должно показывать неопределенность, конфликты и риски, а не скрывать их.

## Архитектурное решение

Рекомендуемый стек:

- Desktop UI: `PySide6`.
- Map: embedded `Leaflet` through Qt WebEngine.
- Database: SQLite first.
- Migrations / ORM later: SQLAlchemy + Alembic.
- HTML parsing: `httpx` + `selectolax` or BeautifulSoup fallback.
- Data validation: Pydantic models.
- CLI: initially standard `argparse`, later Typer if useful.
- AI integration: separate Gemini service layer with schema validation and retries.
- Geodata: cached geocoder + cached OSM/Overpass data for groceries and transport stops.

Проект делится на независимые слои:

- `scraper`: SS.com list/detail parsing.
- `db`: storage, schema, migrations, repositories.
- `ai`: image classification and layout/mortgage reasoning.
- `geo`: address parsing, geocoding, OSM enrichment.
- `scoring`: all score blocks, profiles, explanations.
- `ui`: PySide6 screens and Leaflet bridge.
- `cli`: sync, analyze, recalculate, diagnostics.

## Step 01. Project foundation

### Цель

Создать Python-основу проекта, чтобы следующие MVP не смешивали код парсера, AI, UI и базы данных.

### Детали из ТЗ

ТЗ требует много независимых подсистем: SS parser, caching/update logic, two-pass Gemini pipeline, geodata, scoring blocks, ranking, map, user statuses, profile settings. Поэтому первый шаг должен дать модульную структуру и базовую БД, даже если парсер еще не реализован.

### Реализация

1. Создать `pyproject.toml`.
2. Создать пакет `src/flat_searcher`.
3. Создать подпакеты `scraper`, `db`, `ai`, `geo`, `scoring`, `ui`, `models`.
4. Добавить конфиг через environment variables.
5. Добавить CLI-команды:
   - `show-config`;
   - `init-db`.
6. Добавить базовую SQLite schema v001.
7. Добавить `.env.example`, `.gitignore`, root `README.md`.
8. Добавить lightweight verification через `compileall` и init-db в temporary path.

### Результат

Проект запускается как Python package, умеет показать конфиг и создать SQLite database со стартовой схемой.

### Acceptance criteria

1. `python -m flat_searcher show-config` работает при `PYTHONPATH=src`.
2. `python -m flat_searcher init-db --database <path>` создает SQLite file.
3. Код компилируется без syntax errors.
4. Структура проекта готова под следующие MVP.

## Step 02. SS.com parser and local database

### Цель

Реализовать сбор активных Riga apartment sale listings с SS.com и сохранение нормализованных данных.

### Детали из ТЗ

Стартовая страница:

`https://www.ss.com/lv/real-estate/flats/riga/all/sell/`

С listing page нужно собрать:

- listing URL;
- listing ID;
- district;
- street;
- declared room count;
- area;
- floor;
- building series/type if available;
- price;
- price per square meter if available;
- listing table metadata.

С detail page нужно собрать:

- full listing description;
- full address data available on SS;
- price;
- area;
- declared rooms;
- floor and total floors;
- series/type of building;
- heating or utility-related text if present;
- date of listing or update;
- unique visit count;
- image URLs;
- raw text snapshot;
- basic HTML snapshot/hash;
- original SS listing link.

### Реализация

1. Создать HTTP client with rate limit and retry.
2. Создать saved HTML fixtures для list page и detail page.
3. Реализовать `ListingListParser`.
4. Реализовать `ListingDetailParser`.
5. Нормализовать числа: price EUR, area m2, price per m2, floors, rooms.
6. Извлекать `ss_id` из URL.
7. Сохранять raw snapshot hashes.
8. Сохранять original listing text as source block.
9. Покрыть parser unit tests на fixtures.

### Результат

CLI-команда sync собирает объявления, открывает detail pages и сохраняет raw/current data в SQLite.

### Acceptance criteria

1. Парсер работает на сохраненных fixtures без сети.
2. Парсер умеет обработать missing house number и missing fields.
3. Один запуск создает/обновляет listings.
4. `ss_id` уникален.

## Step 03. Caching, update logic and listing history

### Цель

Не переанализировать все объявления на каждом запуске и сохранять историю изменений.

### Детали из ТЗ

На следующих запусках нужно:

- recheck listing page;
- detect new listings;
- detect removed/inactive listings;
- detect changed price;
- detect changed listing text;
- detect changed image count;
- detect changed unique visit count;
- run full AI analysis only for new or sufficiently changed listings;
- recalculate scores;
- preserve history.

История должна включать:

- price history;
- unique visit history;
- text changes;
- image count changes;
- first detected date;
- last checked date;
- disappeared/returned state.

### Реализация

1. Добавить `app_runs`.
2. Каждый sync создает snapshot для каждого listing.
3. Сравнивать current snapshot with previous snapshot.
4. Создавать `listing_change_events`.
5. Помечать missing listings as inactive.
6. Если listing returns with same SS ID, mark active again.
7. Подготовить field `needs_ai_analysis`.

### Результат

Повторный запуск обновляет только измененные сущности и сохраняет историю.

### Acceptance criteria

1. Price change создает event.
2. Image count change создает event.
3. Removed listing не удаляется.
4. Returned listing снова становится active.
5. History view сможет быть построен из таблиц.

## Step 04. Temporary image downloading and floor plan cache

### Цель

Подготовить изображения для AI без постоянного хранения обычных фото.

### Детали из ТЗ

Обычные фото используются AI internally и затем удаляются. Исключение: floor plan image может сохраняться, потому что объясняет layout conclusion в UI.

### Реализация

1. Скачать images во временную папку per listing/per run.
2. Хранить metadata: image URL, content hash, size, mime.
3. После AI удалить ordinary images.
4. Сохранять floor plan image asset if detected.
5. Обновлять image count and hashes for change detection.

### Результат

AI получает локальные image files, но приложение не превращается в архив фото.

### Acceptance criteria

1. Temporary image directory очищается после анализа.
2. Floor plans сохраняются отдельно.
3. Listing detail view не имеет ordinary photo assets.

## Step 05. Two-pass Gemini AI pipeline

### Цель

Получить структурированную AI-интерпретацию планировки, рисков здания и ипотечной пригодности.

### Детали из ТЗ

Pass 1:

- classify all images;
- find floor plan images;
- mark useless images;
- detect near-duplicates;
- group interior photos by likely room;
- detect corridor/hallway/entrance photos;
- detect kitchen, bathroom, living room photos;
- mark exterior/building photos;
- identify photos useful for layout reasoning;
- identify photos useful for building/mortgage analysis.

Categories:

- `floor_plan`;
- `interior_room`;
- `kitchen`;
- `bathroom`;
- `corridor_or_hallway`;
- `exterior_building`;
- `entrance_staircase`;
- `yard_or_street_view`;
- `duplicate_or_near_duplicate`;
- `irrelevant_or_decorative`;
- `agency_collage`.

Pass 2 receives listing text, SS fields, useful photos, floor plan, Pass 1 classification, image groups, exterior photos and optional layout priors.

Pass 2 determines:

- real layout;
- effective private rooms;
- walkthrough rooms;
- kitchen-living cases;
- SS vs AI room conflict;
- building type guess;
- series guess;
- wooden building risk;
- stove heating risk;
- mortgage risk level;
- human-readable explanations.

### Реализация

1. Создать Pydantic schemas for Pass 1 and Pass 2.
2. Сделать Gemini client abstraction.
3. Сделать prompt templates.
4. Добавить strict JSON parsing.
5. Добавить retry on invalid JSON.
6. Сохранять raw technical output only internally.
7. Сохранять user-facing explanations separately.
8. Добавить analysis versioning.

### Результат

Для listing появляется validated AI analysis, готовый для scoring and UI.

### Acceptance criteria

1. AI output валидируется.
2. Invalid output не ломает sync.
3. Layout confidence is one of `Confirmed`, `Likely`, `Unclear`, `Conflict`.
4. Mortgage risk is one of `Low`, `Medium`, `High`, `Critical`, `Unknown`.
5. UI-ready explanation не содержит raw JSON.

## Step 06. Product layout rules and confidence model

### Цель

Закрепить продуктовые правила комнат как детерминированный слой поверх AI-output.

### Детали из ТЗ

Private room: living room accessible from neutral area, without passing through another private room.

Walkthrough room: living room that must be crossed to enter another living room; not counted as private room.

Kitchen is not counted as living room.

Kitchen-living is not counted as full private room.

Product rule:

`1 bedroom + kitchen-living != good 2-room apartment`

Evidence priority:

1. Floor plan image.
2. Interior photos.
3. Listing text.
4. SS-declared room count.
5. Typical layout prior.

### Реализация

1. Создать deterministic post-processor for AI layout.
2. Создать flag generation:
   - `Room conflict`;
   - `Layout unclear`;
   - `Layout confirmed by floor plan`;
   - `AI: 2 private / SS: 3`;
   - `Kitchen-living is not counted as private room`.
3. Создать final normalized fields:
   - `effective_private_rooms`;
   - `walkthrough_rooms`;
   - `kitchen_living_detected`;
   - `ss_vs_ai_room_conflict`.

### Результат

AI conclusion превращается в стабильные product fields and flags.

### Acceptance criteria

1. Kitchen-living не увеличивает private room score.
2. SS/AI conflict always generates a visible flag.
3. Floor plan presence influences confidence, not a separate user-facing debug flag.

## Step 07. Mortgage suitability and bankability

### Цель

Рассчитывать ипотечную пригодность отдельно от общей привлекательности квартиры.

### Детали из ТЗ

Risk factors:

- stove heating;
- wooden building;
- wooden building plus stove heating;
- legal risks;
- land lease;
- shared ownership;
- encumbrances;
- auction;
- building not commissioned;
- poor building condition;
- missing utilities;
- suspicious incomplete listing.

Risk severity:

- stove heating: High or Critical;
- wooden building: High;
- wooden building plus stove heating: Critical;
- legal/ownership complications: High or Critical;
- building not commissioned: Critical.

### Реализация

1. Создать mortgage risk rules.
2. Объединять AI evidence and listing text keywords.
3. Хранить `mortgage_bankability_score`.
4. Хранить `mortgage_risk_level`.
5. Хранить `mortgage_risk_reasons`.
6. Генерировать human-readable evidence.

### Результат

Квартира может быть хорошей, но risky for mortgage, и это видно в scoring and flags.

### Acceptance criteria

1. High risk listing не скрывается automatically.
2. Critical risk strongly lowers mortgage block.
3. Reasons visible in detail view.

## Step 08. Geocoding and address precision

### Цель

Получить координаты и четко отделить точные адреса от приблизительных.

### Детали из ТЗ

Address precision levels:

- `exact_house`;
- `street_approx`;
- `district_approx`;
- `unknown`.

Only `exact_house + high confidence` enables location-sensitive scores.

For `street_approx`:

- show approximate map marker;
- do not calculate location scores;
- show flag `Approximate address - location scores not calculated`.

### Реализация

1. Address parser extracts street and house number.
2. Geocoder cache by normalized address.
3. Confidence classifier.
4. Store `geocode_precision`, `geocode_confidence`, source and explanation.
5. Set `geo_scores_enabled`.
6. Set disabled reason.

### Результат

Плохой геокодинг не дает ложных location bonuses.

### Acceptance criteria

1. Missing house number disables location scores.
2. Approximate marker can still be shown on map.
3. Unknown address remains in ranking if filters allow it.

## Step 09. Location scoring blocks

### Цель

Посчитать простые distance-based location scores.

### Детали из ТЗ

MVP does not calculate:

- walking time;
- public transport travel time;
- route planning;
- transfers;
- frequency;
- schedules.

Scores:

- RTU score by distance to one main RTU point.
- Station score by distance to Riga Central / Origo area.
- Shop score by grocery stores within 300m, 700m, 1200m.
- Transport score by nearby stop distance and count if available.

### Реализация

1. Define RTU destination coordinate.
2. Define Central Station / Origo coordinate.
3. Cache grocery POIs from OSM/Overpass.
4. Cache transport stop POIs if available.
5. Haversine distance calculations.
6. Smooth scoring curves.
7. Disable all blocks for non-exact geocodes.

### Результат

Location block in detail view and scoring blocks get stable distance-based values.

### Acceptance criteria

1. Scores are null/disabled for approximate address.
2. Distance explanations are user-facing.
3. No route/travel-time claims are shown.

## Step 10. Price-value score and market baselines

### Цель

Рассчитать value не по абсолютной дешевизне, а относительно рынка и полезности.

### Детали из ТЗ

Components:

- `price_per_m2_score`;
- `relative_market_score`;
- `price_per_effective_private_room_score`;
- `absolute_price_score`;
- `suspicious_low_price_flag`.

Baseline levels:

1. Riga baseline.
2. District baseline.
3. AI-adjusted baseline.
4. Series/building baseline.

Exclude from normal baselines:

- critical mortgage risk;
- wooden + stove heating;
- severe legal risk;
- inactive listings;
- incomplete listings;
- extreme outliers;
- data errors.

Use median, percentiles or trimmed mean.

### Реализация

1. Build comparable listing selector.
2. Add outlier filtering.
3. Compute median price/m2 at multiple levels.
4. Pick strongest reliable baseline by sample size.
5. Calculate price per effective private room only when AI confidence allows it.
6. Flag suspiciously low prices without killing score.
7. Store baseline explanation.

### Результат

Cheap listings can rank well but receive warning if abnormal.

### Acceptance criteria

1. No default budget threshold like 100k/120k.
2. Suspicious low price gives both positive value and warning.
3. Price history does not affect rating.

## Step 11. Scoring blocks and profiles

### Цель

Создать общий weighted scoring engine.

### Детали из ТЗ

Each block returns 0-100.

Profile importance:

- Ignore = 0;
- Weak factor = 1;
- Medium factor = 2;
- Strong factor = 3;
- Critical factor = 5.

Formula:

`overall_score = weighted_average(block_scores) - penalties`

Default profile: `For living + mortgage`.

Default importance order:

1. Price value.
2. Room privacy.
3. Mortgage suitability.
4. RTU accessibility.
5. Transport connectivity.
6. Central station accessibility.
7. Shops / infrastructure.
8. Useful area.
9. Building / series.
10. AI confidence.

Disabled by default:

- Floor;
- Condition / renovation;
- Views.

### Реализация

1. Implement block interface.
2. Implement default profile.
3. Implement weighted average.
4. Implement penalties.
5. Implement tie-breakers.
6. Store score breakdown and explanation.

### Результат

Ranking can sort by selected profile with explainable breakdown.

### Acceptance criteria

1. Views cannot be scoring block.
2. Filters do not change score.
3. Close scores use tie-breaker explanation.

## Step 12. Apartment detail view

### Цель

Создать readable apartment card without debug/technical noise.

### Детали из ТЗ

Blocks:

- top block;
- flags;
- rating block;
- layout block;
- mortgage risk block;
- location block;
- listing history block;
- original listing text.

Top block shows:

- generated title;
- price;
- area;
- price per m2;
- district;
- street;
- floor;
- building series/type;
- listing date/update date;
- original SS link.

Do not show ordinary listing photos. Show only floor plan if found.

### Реализация

1. Build PySide6 detail screen.
2. Map database fields to English labels.
3. Render flags.
4. Render score breakdown.
5. Render floor plan image if cached.
6. Render source listing text.
7. Link opens original SS listing.

### Результат

User can inspect why apartment is ranked and what uncertainty exists.

### Acceptance criteria

1. No raw AI JSON.
2. No prompt text.
3. No ordinary photos.
4. English labels only.

## Step 13. Ranking list and filters

### Цель

Создать основной рабочий список квартир.

### Детали из ТЗ

Example row:

`#12 · Purvciems · AI: 2 private / SS: 3 · 48 m2 · 92 000 EUR · Score 78`

Show:

- position;
- generated apartment title;
- score;
- key flags;
- price;
- EUR/m2;
- area;
- effective private rooms;
- layout confidence;
- mortgage risk;
- RTU indicator;
- transport indicator;
- shop indicator;
- view activity indicator.

Filters:

- price;
- area;
- district;
- SS rooms;
- effective private rooms;
- room conflict;
- confirmed layout;
- mortgage risk;
- stove heating;
- wooden buildings;
- floor plan;
- transport;
- RTU;
- central station;
- new today/week/since launch;
- active/inactive;
- viewed/rejected/favorites.

### Реализация

1. Create query builder for filtered dataset.
2. Ranking and map consume same filtered dataset.
3. Add row renderer.
4. Add filter panel.
5. Hide rejected by default.
6. Active-only by default.

### Результат

User can narrow candidates without corrupting score semantics.

### Acceptance criteria

1. Filter state affects visible rows only.
2. Position recalculates inside current visible set.
3. Hidden listings disappear from map too.

## Step 14. User statuses and workflow

### Цель

Сделать приложение candidate management tool, not only analyzer.

### Детали из ТЗ

Required MVP statuses:

- `new`;
- `unseen`;
- `viewed`;
- `favorite`;
- `rejected`;
- `inactive`.

Technical listing status and user status are separate.

Rejected listings:

- not deleted;
- hidden from main ranking by default;
- visible through filter or tab;
- preserve history.

Favorites:

- separate tab;
- filter;
- special map marker;
- later comparison view.

### Реализация

1. Add user state table.
2. Add actions: favorite, reject, mark viewed.
3. Add tabs New, Favorites, Rejected, Inactive.
4. Opening new listing marks viewed unless favorite/rejected.
5. Preserve favorites even if inactive.

### Результат

User can manage a real apartment search workflow.

### Acceptance criteria

1. Inactive favorite remains visible in Favorites.
2. Rejected listing remains in DB.
3. New since last launch is calculated from app runs.

## Step 15. Map

### Цель

Показать synchronized map of currently filtered apartments.

### Детали из ТЗ

Marker types:

- normal exact address;
- approximate marker with uncertainty style;
- district marker;
- favorite marker;
- rejected muted marker;
- inactive muted marker.

Marker color reflects score under current profile.

Clusters:

- show apartment count;
- click zooms in;
- no cluster popup/list for MVP.

Map points:

- apartments;
- clusters;
- RTU point;
- Riga Central / Origo;
- grocery stores used in shop scoring.

### Реализация

1. Embed Leaflet in PySide6 WebEngine.
2. Send filtered listings as JSON to map.
3. Implement marker styles.
4. Implement marker clustering.
5. Add click-to-select listing.
6. Add `Showing X of Y apartments`.

### Результат

Ranking and map are two views of the same filtered candidate set.

### Acceptance criteria

1. Hidden by filter means hidden on map.
2. Approximate markers are visually different.
3. Cluster click zooms in.

## Step 16. Preset and custom profiles

### Цель

Дать пользователю менять importance, not raw scoring direction.

### Детали из ТЗ

Preset profiles:

- `For living + mortgage`;
- `Mortgage first`;
- `Maximum opportunity`;
- `Only 2 private rooms`;
- `Best price`;
- `Best transport`;
- `Closer to RTU`;
- `Cash purchase`;
- `Investment option`.

Profile editor scale:

`Ignore | Weak factor | Medium factor | Strong factor | Critical factor`

### Реализация

1. Store profile definitions in DB.
2. Add editor UI.
3. Drag blocks between importance levels.
4. Disable/enable blocks.
5. Save/rename custom profile.
6. Recalculate ranking on profile change.

### Результат

Different search strategies can reuse same analyzed data.

### Acceptance criteria

1. User cannot reverse block meaning.
2. Custom profile persists.
3. Profile change updates ranking and marker colors.

## Step 17. Notes, comparison and sessions

### Цель

Добавить удобства для длительного поиска.

### Детали из ТЗ

Notes examples:

- `Call seller`;
- `Check heating`;
- `Ask about land`;
- `Bad layout but good price`.

Comparison view compares 2-5 apartments by:

- price;
- EUR/m2;
- area;
- effective private rooms;
- layout confidence;
- mortgage risk;
- RTU distance;
- transport;
- central station;
- shops;
- building/series;
- AI summary;
- flags;
- pros/cons.

Search sessions store:

- session name;
- selected scoring profile;
- filters;
- sort mode;
- hidden statuses;
- created/updated date.

### Реализация

1. Add user notes table and UI.
2. Add comparison basket.
3. Add comparison tab.
4. Add saved sessions.
5. Restore filters/profile from session.

### Результат

Приложение становится полноценным рабочим инструментом для выбора квартиры.

### Acceptance criteria

1. Notes visible in detail and list indicator.
2. Comparison supports 2-5 apartments.
3. Session restores filters and profile.

## Step 18. Testing, reliability and packaging

### Цель

Сделать проект устойчивым к изменениям SS.com, сетевым ошибкам, AI failures and user data loss.

### Реализация

1. Parser tests on fixtures.
2. DB migration tests.
3. Scoring unit tests.
4. AI schema validation tests with mocked Gemini.
5. Geocoding cache tests.
6. UI smoke tests where practical.
7. Backup/export command for SQLite DB.
8. Packaging strategy for Windows.

### Acceptance criteria

1. Existing analyzed listings survive app updates.
2. Invalid AI response does not break full run.
3. SS layout change is detected by parser test failures.
4. User can backup/export local database.
