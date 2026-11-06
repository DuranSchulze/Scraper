ALTER TABLE jobs
    ADD COLUMN control text CHECK (control IN ('pause', 'stop'));
ALTER TABLE jobs DROP CONSTRAINT jobs_status_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_status_check
    CHECK (status IN ('running', 'paused', 'done', 'done_with_errors', 'failed', 'interrupted', 'stopped'));
DROP INDEX jobs_one_running_idx;
CREATE UNIQUE INDEX jobs_one_running_idx ON jobs ((true)) WHERE status IN ('running', 'paused');

CREATE TABLE gone_items (
    source    text NOT NULL,
    mailbox   text NOT NULL,
    source_id text NOT NULL,
    at        timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (source, mailbox, source_id)
);
