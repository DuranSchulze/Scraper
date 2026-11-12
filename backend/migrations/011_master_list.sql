CREATE TABLE master_clients (
    id bigserial PRIMARY KEY,
    row_no integer NOT NULL,
    registered_name text NOT NULL DEFAULT '',
    alias text NOT NULL DEFAULT '',
    trade_name text NOT NULL DEFAULT '',
    tin text NOT NULL DEFAULT '',
    intercompany text NOT NULL DEFAULT '',
    engagement_status text NOT NULL DEFAULT '',
    contact_person text NOT NULL DEFAULT '',
    emails text[] NOT NULL DEFAULT '{}',
    domains text[] NOT NULL DEFAULT '{}'
);
CREATE INDEX master_clients_emails_idx ON master_clients USING gin (emails);
CREATE INDEX master_clients_domains_idx ON master_clients USING gin (domains);

CREATE TABLE master_imports (
    id bigserial PRIMARY KEY,
    filename text NOT NULL,
    sheet text NOT NULL,
    rows integer NOT NULL,
    emails integer NOT NULL,
    imported_by text NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE candidates
    ADD COLUMN master_match text CHECK (master_match IN ('email','domain')),
    ADD COLUMN master_ids bigint[];
