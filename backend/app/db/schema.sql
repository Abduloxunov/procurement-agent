-- Sourcing Desk schema.
--
-- Written to be read by a text-to-SQL agent, so: flat tables, spelled-out
-- column names, no abbreviations, no clever joins required for common
-- questions. A vague schema is where text-to-SQL produces valid-looking
-- wrong answers.

PRAGMA foreign_keys = ON;


-- ---------------------------------------------------------------- parts ----

CREATE TABLE IF NOT EXISTS parts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    mpn             TEXT    NOT NULL UNIQUE,   -- manufacturer part number, e.g. FST100-2006A
    manufacturer    TEXT,
    description     TEXT,
    category        TEXT,
    hs_code         TEXT,                      -- drives the duty rate; human-confirmed
    hs_code_source  TEXT,                      -- 'proposed' | 'confirmed'
    weight_kg       REAL,                      -- shipping weight, asked in the RFQ
    weight_source   TEXT,                      -- 'estimated' | 'datasheet' | 'supplier'
    datasheet_url   TEXT,
    datasheet_path  TEXT,
    lifecycle       TEXT,                      -- 'active' | 'nrnd' | 'obsolete' | 'unknown'
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);


-- Flexible key/value so "anything technical" needs no schema migration.
CREATE TABLE IF NOT EXISTS part_specs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id     INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
    spec_key    TEXT    NOT NULL,              -- 'output_signal', 'supply_voltage'
    spec_value  TEXT    NOT NULL,              -- 'RS485', '12-24'
    unit        TEXT,                          -- 'V', 'mm', '%'
    is_mandatory INTEGER NOT NULL DEFAULT 0,   -- 1 = a mismatch disqualifies
    source      TEXT,                          -- 'datasheet' | 'user' | 'listing'
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_part_specs_part ON part_specs(part_id);


-- -------------------------------------------------------------- sources ----

-- A weight, not a filter. Nothing is ever excluded from search (design S04).
CREATE TABLE IF NOT EXISTS sources (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL UNIQUE,
    domain              TEXT,
    kind                TEXT    NOT NULL,      -- 'engine' | 'marketplace' | 'manufacturer'
                                               -- | 'distributor' | 'local_rep'
    access              TEXT    NOT NULL,      -- 'api' | 'fetch' | 'browser' | 'manual'
    region              TEXT,
    currency            TEXT    DEFAULT 'USD',
    ships_to_uz         INTEGER DEFAULT 1,
    weight              REAL    NOT NULL DEFAULT 1.0,   -- ranking multiplier
    typical_lead_days   INTEGER,
    times_used          INTEGER NOT NULL DEFAULT 0,
    last_used_at        TEXT,
    notes               TEXT
);


CREATE TABLE IF NOT EXISTS suppliers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    source_id       INTEGER REFERENCES sources(id),
    country         TEXT,
    contact_email   TEXT,
    profile_url     TEXT,
    trust_score     REAL    NOT NULL DEFAULT 0.5,   -- 0..1, moves with purchase outcomes
    times_used      INTEGER NOT NULL DEFAULT 0,
    on_time_rate    REAL,                            -- computed from purchases
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (name, source_id)
);


-- ------------------------------------------------------------- requests ----

CREATE TABLE IF NOT EXISTS requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    part_id         INTEGER REFERENCES parts(id),
    raw_request     TEXT,                      -- what he actually typed
    quantity        INTEGER,
    quantity_tiers  TEXT,                      -- JSON list, e.g. [10, 50, 100]
    need_by         TEXT,
    budget          REAL,                      -- total, USD; NULL if not given
    destination     TEXT    DEFAULT 'Tashkent, UZ',
    incoterm_pref   TEXT,                      -- 'FOB' | 'EXW' | 'DDP' | null
    status          TEXT    NOT NULL DEFAULT 'draft',
                    -- draft | clarifying | searching | extracting | costed | done
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);


-- --------------------------------------------------------------- offers ----

-- One row per (supplier, part, quantity) price point we found or were quoted.
CREATE TABLE IF NOT EXISTS offers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      INTEGER REFERENCES requests(id) ON DELETE CASCADE,
    supplier_id     INTEGER REFERENCES suppliers(id),
    part_id         INTEGER REFERENCES parts(id),

    quantity        INTEGER,
    unit_price      REAL,                      -- NULL when the page says "contact supplier"
    currency        TEXT,                      -- NULL iff unit_price is NULL
    unit_price_original     REAL,              -- kept so a bad conversion is traceable
    currency_original       TEXT,              -- e.g. 'CNY' from a Baidu result

    incoterm        TEXT,                      -- 'FOB' | 'EXW' | 'DDP' | 'CIF'
    moq             INTEGER,
    tier_breaks     TEXT,                      -- JSON: [{"qty":50,"price":38.5}, ...]
    lead_days       INTEGER,
    warranty_months INTEGER,

    url             TEXT,                      -- the page or thread this came from
    page_title      TEXT,                      -- original title, untranslated
    source_kind     TEXT,                      -- 'listing' | 'email' | 'manual'
    rung            TEXT,                      -- 'fetch' | 'browser' | 'manual' | 'rfq'
    confidence      REAL,                      -- 0..1 from extraction
    state           TEXT    NOT NULL DEFAULT 'listed',
                    -- listed      price scraped from a public page
                    -- needs_rfq   real supplier, no published price
                    -- rfq_sent    we asked, awaiting reply
                    -- verified    quoted to us, at our quantity
                    -- disqualified fails a mandatory spec
                    -- stale       price too old to trust
    disqualified_reason TEXT,                  -- which mandatory spec failed

    captured_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_offers_request ON offers(request_id);
CREATE INDEX IF NOT EXISTS idx_offers_part    ON offers(part_id);


-- --------------------------------------------------------- landed costs ----

CREATE TABLE IF NOT EXISTS landed_costs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id        INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    quantity        INTEGER NOT NULL,

    goods           REAL    NOT NULL,
    freight         REAL    NOT NULL DEFAULT 0,
    freight_mode    TEXT,                      -- 'air_express' | 'air' | 'road' | 'rail'
    freight_source  TEXT,                      -- 'estimate' | 'quoted' | 'actual'
    insurance       REAL    NOT NULL DEFAULT 0,
    cif             REAL    NOT NULL,

    hs_code         TEXT,
    duty_rate       REAL    NOT NULL,
    duty            REAL    NOT NULL,
    vat_rate        REAL    NOT NULL,
    vat             REAL    NOT NULL,
    fees            REAL    NOT NULL DEFAULT 0,

    total           REAL    NOT NULL,
    per_unit        REAL    NOT NULL,
    assumptions     TEXT,                      -- JSON, printed on every row
    computed_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_landed_offer ON landed_costs(offer_id);


-- ------------------------------------------------------------ purchases ----

-- Institutional memory. Filled from one sentence plus a batched follow-up
-- (design S10). This table is why the text-to-SQL agent is worth having.
CREATE TABLE IF NOT EXISTS purchases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    part_id             INTEGER REFERENCES parts(id),
    supplier_id         INTEGER REFERENCES suppliers(id),
    offer_id            INTEGER REFERENCES offers(id),   -- if it came from a table row

    quantity            INTEGER NOT NULL,
    unit_price          REAL    NOT NULL,
    currency            TEXT    NOT NULL DEFAULT 'USD',
    price_basis         TEXT    NOT NULL,      -- 'FOB' | 'EXW' | 'DDP' | 'landed'
                                               -- never null: $41 FOB != $41 delivered
    freight_paid        REAL,
    duty_vat_paid       REAL,
    total_landed        REAL,                  -- computed
    landed_per_unit     REAL,                  -- computed

    ordered_on          TEXT,
    received_on         TEXT,
    lead_days_actual    INTEGER,               -- computed, beats any promised figure
    condition           TEXT,                  -- 'good' | 'partial' | 'faulty'
    would_buy_again     INTEGER,               -- 1/0, drives supplier weighting
    notes               TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_purchases_part     ON purchases(part_id);
CREATE INDEX IF NOT EXISTS idx_purchases_supplier ON purchases(supplier_id);


-- ----------------------------------------------------------------- rfqs ----

CREATE TABLE IF NOT EXISTS rfqs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    supplier_id     INTEGER REFERENCES suppliers(id),
    ref_code        TEXT    NOT NULL UNIQUE,   -- goes in the subject, matches replies
    thread_id       TEXT,
    subject         TEXT,
    body            TEXT,
    status          TEXT    NOT NULL DEFAULT 'drafted',
                    -- drafted | approved | sent | replied | no_reply | dropped
    sent_at         TEXT,
    replied_at      TEXT,
    last_chase_at   TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);


-- --------------------------------------------------------- search cache ----

-- SerpApi free tier is 250/month. Caching is mandatory, not an optimisation.
CREATE TABLE IF NOT EXISTS search_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    engine          TEXT    NOT NULL,          -- 'google' | 'baidu'
    query           TEXT    NOT NULL,
    results_json    TEXT    NOT NULL,
    fetched_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (engine, query)
);
