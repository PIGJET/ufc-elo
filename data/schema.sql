-- UFC Elo project — database schema (source of truth).
-- Portable SQL only: no SQLite-specific storage features, TEXT ISO-8601 dates,
-- so a future Postgres migration is dump/restore.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS fighters (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    nickname        TEXT,
    dob             TEXT,               -- ISO date
    height_in       REAL,
    reach_in        REAL,
    leg_reach_in    REAL,
    stance          TEXT,               -- Orthodox / Southpaw / Switch
    country         TEXT,
    division        TEXT,               -- current primary division
    wins            INTEGER,
    losses          INTEGER,
    draws           INTEGER,
    status          TEXT DEFAULT 'active',  -- active / retired / not_fighting
    ufcstats_id     TEXT UNIQUE,        -- ufcstats.com fighter id
    ufc_slug        TEXT,               -- ufc.com athlete page slug
    image_url       TEXT,               -- hotlinked cutout photo
    style_tag       TEXT,               -- striker / wrestler / grappler / balanced / insufficient_data
    pre_ufc_record  TEXT,               -- e.g. "15-0 (Bellator)" for seeding priors
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fighters_name ON fighters(name);
CREATE INDEX IF NOT EXISTS idx_fighters_division ON fighters(division);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    date            TEXT,               -- ISO date
    location        TEXT,
    venue           TEXT,
    status          TEXT DEFAULT 'completed',  -- upcoming / completed / cancelled
    ufcstats_id     TEXT UNIQUE,
    ufc_slug        TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);

CREATE TABLE IF NOT EXISTS fights (
    id               INTEGER PRIMARY KEY,
    event_id         INTEGER NOT NULL REFERENCES events(id),
    fighter_red_id   INTEGER NOT NULL REFERENCES fighters(id),
    fighter_blue_id  INTEGER NOT NULL REFERENCES fighters(id),
    winner_id        INTEGER REFERENCES fighters(id),   -- NULL for draw/NC/upcoming
    -- result_original: as announced in the cage. result_current: after any
    -- commission/USADA overturn. Ratings ALWAYS compute from result_current.
    result_original  TEXT,   -- win / draw / nc / dq / upcoming
    result_current   TEXT,
    method           TEXT,   -- KO/TKO, SUB, U-DEC, S-DEC, M-DEC, DQ, NC
    method_detail    TEXT,   -- e.g. "Punches", "Rear-naked choke"
    round            INTEGER,
    time_seconds     INTEGER,           -- elapsed in final round
    weight_class     TEXT,              -- division name or "Catchweight"
    catchweight_lbs  REAL,              -- actual contracted weight if catchweight
    scheduled_rounds INTEGER,
    is_title         INTEGER DEFAULT 0,
    is_interim_title INTEGER DEFAULT 0,
    is_main_event    INTEGER DEFAULT 0,
    card_position    INTEGER,           -- 1 = main event, ascending down the card
    judge_scores     TEXT,              -- JSON string, when available
    ufcstats_id      TEXT UNIQUE,
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fights_event ON fights(event_id);
CREATE INDEX IF NOT EXISTS idx_fights_red ON fights(fighter_red_id);
CREATE INDEX IF NOT EXISTS idx_fights_blue ON fights(fighter_blue_id);

-- One row per fighter per fight. Feeds style classification + UI stat tabs.
CREATE TABLE IF NOT EXISTS fight_stats (
    id                    INTEGER PRIMARY KEY,
    fight_id              INTEGER NOT NULL REFERENCES fights(id),
    fighter_id            INTEGER NOT NULL REFERENCES fighters(id),
    knockdowns            INTEGER,
    sig_strikes_landed    INTEGER,
    sig_strikes_attempted INTEGER,
    sig_head_landed       INTEGER,
    sig_body_landed       INTEGER,
    sig_leg_landed        INTEGER,
    sig_distance_landed   INTEGER,
    sig_clinch_landed     INTEGER,
    sig_ground_landed     INTEGER,
    total_strikes_landed  INTEGER,
    total_strikes_attempted INTEGER,
    takedowns_landed      INTEGER,
    takedowns_attempted   INTEGER,
    sub_attempts          INTEGER,
    reversals             INTEGER,
    control_time_seconds  INTEGER,
    UNIQUE (fight_id, fighter_id)
);

-- Append-only: line movement is preserved, latest fetched_at wins for display.
CREATE TABLE IF NOT EXISTS odds (
    id             INTEGER PRIMARY KEY,
    fight_id       INTEGER NOT NULL REFERENCES fights(id),
    source         TEXT NOT NULL,       -- e.g. "the-odds-api:draftkings"
    red_moneyline  INTEGER,             -- American odds
    blue_moneyline INTEGER,
    fetched_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_odds_fight ON odds(fight_id);

CREATE TABLE IF NOT EXISTS rankings_snapshot (
    id            INTEGER PRIMARY KEY,
    snapshot_date TEXT NOT NULL,
    division      TEXT NOT NULL,
    rank          INTEGER NOT NULL,     -- 0 = champion
    fighter_id    INTEGER REFERENCES fighters(id),
    fighter_name  TEXT,                 -- raw name as scraped (id may resolve later)
    movement      INTEGER DEFAULT 0,    -- +up / -down since previous snapshot
    UNIQUE (snapshot_date, division, rank)
);

-- Written by the rating engine (Phase 2). One row per fighter per rated fight.
CREATE TABLE IF NOT EXISTS elo_history (
    id           INTEGER PRIMARY KEY,
    fighter_id   INTEGER NOT NULL REFERENCES fighters(id),
    fight_id     INTEGER REFERENCES fights(id),
    date         TEXT NOT NULL,
    division     TEXT NOT NULL,
    mu_pre       REAL, rd_pre  REAL,
    mu_post      REAL, rd_post REAL, sigma_post REAL,
    pfp_mu_post  REAL,                   -- division mu + fitted offset D_w
    update_note  TEXT                    -- e.g. "MOV 1.31 x stakes 1.25, capped"
);
CREATE INDEX IF NOT EXISTS idx_elo_history_fighter ON elo_history(fighter_id, date);

-- Rebuilt in full by every rating recompute: the latest (mu, RD, sigma) per
-- fighter per division pool, with RD grown to as_of for inactive fighters.
CREATE TABLE IF NOT EXISTS ratings_current (
    fighter_id  INTEGER NOT NULL REFERENCES fighters(id),
    division    TEXT NOT NULL,
    mu          REAL NOT NULL,
    rd          REAL NOT NULL,
    sigma       REAL,
    pfp_mu      REAL,                   -- mu + fitted division offset D_w
    n_fights    INTEGER,                -- rated fights in this pool
    last_fight  TEXT,                   -- ISO date of last rated fight
    as_of       TEXT,
    PRIMARY KEY (fighter_id, division)
);

-- Field-level source audit trail. Never overwrite silently: on cross-source
-- disagreement keep current value, log both here, surface in discrepancy report.
CREATE TABLE IF NOT EXISTS provenance (
    id          INTEGER PRIMARY KEY,
    entity      TEXT NOT NULL,          -- table name
    entity_id   INTEGER NOT NULL,
    field       TEXT NOT NULL,
    source      TEXT NOT NULL,          -- kaggle / ufcstats / ufc.com / the-odds-api
    value_seen  TEXT,
    fetched_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_provenance_entity ON provenance(entity, entity_id);

-- Per-source incremental-sync high-water marks.
CREATE TABLE IF NOT EXISTS sync_state (
    source     TEXT PRIMARY KEY,
    watermark  TEXT,                    -- source-specific: last event date/id etc.
    last_run   TEXT,
    note       TEXT
);
