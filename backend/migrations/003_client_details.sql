-- Published client details; the conversation timeline stays in candidates.
ALTER TABLE clients
    ADD COLUMN payments jsonb NOT NULL DEFAULT '[]',
    ADD COLUMN client_since timestamptz,
    ADD COLUMN services_availed text[] NOT NULL DEFAULT '{}',
    ADD COLUMN departments jsonb NOT NULL DEFAULT '[]';
