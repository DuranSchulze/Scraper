CREATE TABLE job_mailboxes (
    job_id bigint NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    mailbox text NOT NULL,
    status text NOT NULL CHECK (status IN ('pending','running','done','failed','skipped')),
    fetched integer NOT NULL DEFAULT 0,
    total integer NOT NULL DEFAULT 0,
    rate_limited integer NOT NULL DEFAULT 0,
    error text,
    started_at timestamptz,
    finished_at timestamptz,
    PRIMARY KEY (job_id, mailbox)
);
