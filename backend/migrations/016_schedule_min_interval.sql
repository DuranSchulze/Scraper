UPDATE schedule SET interval_minutes = 120 WHERE interval_minutes < 120;

ALTER TABLE schedule
    DROP CONSTRAINT schedule_interval_minutes_check,
    ADD CONSTRAINT schedule_interval_minutes_check CHECK (interval_minutes BETWEEN 120 AND 1440);
