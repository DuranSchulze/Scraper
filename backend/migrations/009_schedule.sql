CREATE TABLE schedule (
    id integer PRIMARY KEY CHECK (id = 1),
    enabled boolean NOT NULL,
    interval_minutes integer NOT NULL CHECK (interval_minutes BETWEEN 5 AND 1440),
    updated_at timestamptz NOT NULL DEFAULT now()
);
