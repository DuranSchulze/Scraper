-- Keys whose candidates need rebuilding. Extract batches queue keys in the same transaction as their signals;
-- rebuilds run about once a minute, so a person with thousands of signals is not rebuilt on every batch.
CREATE TABLE rebuild_queue (
    key text PRIMARY KEY,
    -- The scrape job whose new or changed signal made this key "New"; null when nothing fresh.
    fresh_job bigint,
    queued_at timestamptz NOT NULL DEFAULT now()
);
