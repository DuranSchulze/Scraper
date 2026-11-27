ALTER TABLE users
    ADD COLUMN must_change_password boolean NOT NULL DEFAULT false,
    ADD COLUMN session_version integer NOT NULL DEFAULT 0;
