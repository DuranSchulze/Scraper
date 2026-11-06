-- Job state and logs survive API restarts; Postgres arbitrates starts across processes.
CREATE TABLE jobs (
    id           bigserial PRIMARY KEY,
    name         text NOT NULL,
    params       jsonb NOT NULL DEFAULT '{}',
    status       text NOT NULL CHECK (status IN ('running', 'done', 'done_with_errors', 'failed', 'interrupted')),
    started_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at  timestamptz,
    heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    failures     int NOT NULL DEFAULT 0,
    error        text
);
CREATE UNIQUE INDEX jobs_one_running_idx ON jobs ((true)) WHERE status='running';

CREATE TABLE job_log (
    id     bigserial PRIMARY KEY,
    job_id bigint NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    line   text NOT NULL
);
CREATE INDEX job_log_job_id_id_idx ON job_log (job_id, id);
