-- When a record was first added to the clients table; published_at moves on every re-publish.
ALTER TABLE candidates ADD COLUMN first_published_at timestamptz;
UPDATE candidates SET first_published_at = published_at WHERE published_at IS NOT NULL;
CREATE INDEX candidates_first_published_idx ON candidates (first_published_at) WHERE first_published_at IS NOT NULL;

-- One row per Publish click, for the Logs page and its exports.
CREATE TABLE publish_events (
    id bigserial PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT clock_timestamp(),
    by_user text,
    inserted integer NOT NULL,
    updated integer NOT NULL
);
CREATE INDEX publish_events_at_idx ON publish_events (at);

-- The Logs page filters raw items and jobs by date.
CREATE INDEX IF NOT EXISTS raw_items_fetched_at_idx ON raw_items (fetched_at);
CREATE INDEX IF NOT EXISTS jobs_started_at_idx ON jobs (started_at);
