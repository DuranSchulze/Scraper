-- Engagement stages (inquiry -> quoted -> invoiced -> paid), incremental extraction,
-- and a two-step flow: review in staging (candidates), then publish to clients.

-- Raw items not yet turned into signals have extracted_at NULL.
ALTER TABLE raw_items ADD COLUMN extracted_at timestamptz;
ALTER TABLE raw_items ADD COLUMN extract_error text;
CREATE INDEX raw_items_unextracted_idx ON raw_items (id) WHERE extracted_at IS NULL;

-- One row per (person, source item). Small, so candidates can be rebuilt per person
-- without rescanning email bodies.
CREATE TABLE signals (
    id          bigserial PRIMARY KEY,
    raw_item_id bigint    NOT NULL REFERENCES raw_items (id) ON DELETE CASCADE,
    key         text      NOT NULL,
    data        jsonb     NOT NULL
);
CREATE INDEX signals_key_idx ON signals (key);
CREATE INDEX signals_raw_item_idx ON signals (raw_item_id);

-- Staging: the reviewer's edited record lives here until published.
ALTER TABLE candidates
    ADD COLUMN reviewed     jsonb,
    ADD COLUMN reviewed_by  text,
    ADD COLUMN reviewed_at  timestamptz,
    ADD COLUMN published_at timestamptz;

UPDATE candidates SET reviewed_at = updated_at, published_at = updated_at
WHERE review_state = 'approved' AND client_id IS NOT NULL;

-- Official table gets the engagement stage and the quoted/invoiced amounts.
ALTER TABLE clients
    ADD COLUMN stage text NOT NULL DEFAULT 'inquiry'
        CHECK (stage IN ('inquiry', 'quoted', 'invoiced', 'paid')),
    ADD COLUMN amounts jsonb NOT NULL DEFAULT '[]';
