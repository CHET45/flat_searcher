CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    message TEXT
);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ss_id TEXT NOT NULL UNIQUE,
    ss_url TEXT NOT NULL,
    listing_status TEXT NOT NULL DEFAULT 'active',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT,
    last_checked_at TEXT,
    became_inactive_at TEXT,
    reactivated_at TEXT,
    is_new_since_last_run INTEGER NOT NULL DEFAULT 0,
    needs_ai_analysis INTEGER NOT NULL DEFAULT 0,

    listing_title TEXT,
    listing_summary_text TEXT,
    listing_table_metadata_json TEXT,
    detail_fields_json TEXT,

    address_raw TEXT,
    district TEXT,
    street TEXT,
    house_number TEXT,

    price_eur INTEGER,
    price_per_m2 REAL,
    area_m2 REAL,
    declared_rooms_ss INTEGER,
    floor INTEGER,
    total_floors INTEGER,
    building_series TEXT,
    building_type TEXT,

    listing_date_text TEXT,
    unique_visits INTEGER,
    description_text TEXT,
    description_hash TEXT,
    images_count INTEGER,
    raw_snapshot_hash TEXT,
    raw_text_snapshot TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_listings_status ON listings (listing_status);
CREATE INDEX IF NOT EXISTS idx_listings_district ON listings (district);
CREATE INDEX IF NOT EXISTS idx_listings_price ON listings (price_eur);

CREATE TABLE IF NOT EXISTS listing_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    content_hash TEXT,
    image_category TEXT,
    is_floor_plan INTEGER NOT NULL DEFAULT 0,
    local_floor_plan_path TEXT,
    width INTEGER,
    height INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(listing_id, source_url)
);

CREATE TABLE IF NOT EXISTS listing_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    app_run_id INTEGER REFERENCES app_runs(id) ON DELETE SET NULL,
    checked_at TEXT NOT NULL,
    price_eur INTEGER,
    unique_visits INTEGER,
    description_hash TEXT,
    images_count INTEGER,
    is_active INTEGER NOT NULL,
    raw_snapshot_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_listing_snapshots_listing_checked
ON listing_snapshots (listing_id, checked_at);

CREATE TABLE IF NOT EXISTS listing_change_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    detected_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    delta_value TEXT,
    explanation TEXT
);

CREATE INDEX IF NOT EXISTS idx_listing_change_events_listing_detected
ON listing_change_events (listing_id, detected_at);

CREATE TABLE IF NOT EXISTS ai_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    analysis_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    analyzed_at TEXT,

    ai_detected_living_rooms INTEGER,
    effective_private_rooms INTEGER,
    walkthrough_rooms INTEGER,
    kitchen_living_detected INTEGER,
    separate_kitchen_detected INTEGER,
    layout_class TEXT,
    layout_confidence_label TEXT,
    ss_vs_ai_room_conflict INTEGER,
    layout_explanation_user TEXT,
    floor_plan_image_ids TEXT,

    building_type_guess TEXT,
    series_guess TEXT,
    building_material_detected TEXT,
    wooden_building_risk INTEGER,
    stove_heating_risk INTEGER,
    heating_type TEXT,
    facade_condition_score REAL,
    entrance_condition_score REAL,
    house_condition_score REAL,
    legal_risk_flags TEXT,
    mortgage_bankability_score REAL,
    mortgage_risk_level TEXT,
    mortgage_risk_reasons TEXT,
    mortgage_explanation_user TEXT,

    pass1_output_json TEXT,
    pass2_output_json TEXT,
    error_message TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ai_analyses_listing_version
ON ai_analyses (listing_id, analysis_version);

CREATE VIEW IF NOT EXISTS latest_ai_analyses AS
SELECT ai.*
FROM ai_analyses ai
JOIN (
    SELECT listing_id, MAX(id) AS latest_id
    FROM ai_analyses
    WHERE status = 'finished'
    GROUP BY listing_id
) latest ON latest.latest_id = ai.id;

CREATE TABLE IF NOT EXISTS geocoding_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    normalized_address TEXT,
    latitude REAL,
    longitude REAL,
    geocode_precision TEXT,
    geocode_confidence TEXT,
    geocode_source TEXT,
    geocode_explanation TEXT,
    geo_scores_enabled INTEGER NOT NULL DEFAULT 0,
    geo_scores_disabled_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_geocoding_results_listing
ON geocoding_results (listing_id);

CREATE TABLE IF NOT EXISTS location_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    distance_to_rtu_m REAL,
    rtu_score REAL,
    distance_to_central_station_m REAL,
    station_score REAL,
    nearest_shop_distance_m REAL,
    shops_within_300m INTEGER,
    shops_within_700m INTEGER,
    shops_within_1200m INTEGER,
    shop_score REAL,
    nearest_transport_stop_distance_m REAL,
    transport_stops_nearby_count INTEGER,
    transport_score REAL,
    calculated_at TEXT NOT NULL,
    explanation TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_location_scores_listing
ON location_scores (listing_id);

CREATE TABLE IF NOT EXISTS price_value_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price_per_effective_private_room REAL,
    price_value_score REAL,
    price_per_m2_score REAL,
    relative_market_score REAL,
    absolute_price_score REAL,
    suspicious_low_price_flag INTEGER NOT NULL DEFAULT 0,
    market_baseline_level_used TEXT,
    market_baseline_sample_size INTEGER,
    market_baseline_median_price_per_m2 REAL,
    market_baseline_explanation TEXT,
    calculated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_price_value_listing
ON price_value_analyses (listing_id);

CREATE TABLE IF NOT EXISTS scoring_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_key TEXT NOT NULL UNIQUE,
    profile_name TEXT NOT NULL,
    base_profile_key TEXT,
    enabled_blocks_json TEXT NOT NULL,
    block_weights_json TEXT NOT NULL,
    block_settings_json TEXT,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS score_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    profile_key TEXT NOT NULL,
    overall_score REAL,
    score_breakdown_json TEXT NOT NULL,
    score_explanation TEXT,
    tie_breaker_explanation TEXT,
    calculated_at TEXT NOT NULL,
    UNIQUE(listing_id, profile_key)
);

CREATE TABLE IF NOT EXISTS user_listing_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    user_status TEXT NOT NULL DEFAULT 'unseen',
    is_favorite INTEGER NOT NULL DEFAULT 0,
    is_rejected INTEGER NOT NULL DEFAULT 0,
    is_viewed INTEGER NOT NULL DEFAULT 0,
    user_notes TEXT,
    user_tags TEXT,
    last_user_opened_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(listing_id)
);

CREATE TABLE IF NOT EXISTS search_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT NOT NULL,
    selected_profile_key TEXT,
    filters_json TEXT NOT NULL,
    sort_mode TEXT,
    hidden_statuses_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
