ALTER TABLE raw_items ADD COLUMN last_job bigint;

ALTER TABLE job_mailboxes
    ADD COLUMN gmail_ok boolean NOT NULL DEFAULT false,
    ADD COLUMN gmail_boundary timestamptz;

ALTER TABLE sync_state
    ADD COLUMN gmail_complete_at timestamptz,
    ADD COLUMN gmail_boundary timestamptz;

ALTER TABLE jobs
    ADD COLUMN completed boolean NOT NULL DEFAULT false,
    ADD COLUMN lineage bigint[] NOT NULL DEFAULT '{}';

ALTER TABLE candidates
    ADD COLUMN pending_scrape_job bigint,
    ADD COLUMN pending_scraped_at timestamptz,
    ADD COLUMN last_scrape_job bigint,
    ADD COLUMN last_scraped_at timestamptz,
    ADD COLUMN master_evaluated_at timestamptz;

CREATE INDEX candidates_last_scrape_job_idx ON candidates (last_scrape_job);
