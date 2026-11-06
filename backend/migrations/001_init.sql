-- Initial schema for the workspace client scraper.

-- Incremental sync cursors (Gmail historyId, page tokens) per source and mailbox.
CREATE TABLE sync_state (
    source     text        NOT NULL,
    mailbox    text        NOT NULL DEFAULT '',
    cursor     text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, mailbox)
);

-- Untouched fetched items. Unique key makes re-fetching idempotent.
-- Gmail message ids are per mailbox, so mailbox is part of the key.
CREATE TABLE raw_items (
    id         bigserial   PRIMARY KEY,
    source     text        NOT NULL CHECK (source IN ('gmail', 'contacts', 'sheets')),
    source_id  text        NOT NULL,
    mailbox    text        NOT NULL DEFAULT '',
    payload    jsonb       NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source, mailbox, source_id)
);

-- Final directory. The only table the directory app reads.
CREATE TABLE clients (
    id              bigserial   PRIMARY KEY,
    name            text,
    emails          text[]      NOT NULL DEFAULT '{}',
    phones          text[]      NOT NULL DEFAULT '{}',
    company         text,
    status          text        NOT NULL DEFAULT 'inquiry'
                    CHECK (status IN ('inquiry', 'active', 'past', 'lost')),
    services        text[]      NOT NULL DEFAULT '{}',
    department      text,
    engagement_paid boolean     NOT NULL DEFAULT false,
    first_contact   timestamptz,
    last_contact    timestamptz,
    notes           text,
    approved_by     text,
    approved_at     timestamptz,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX clients_emails_idx ON clients USING gin (emails);

-- Extracted, not yet reviewed records. One per normalized email.
-- fields:   {"name": {"value": "...", "source": "gmail", "confidence": 0.8}, ...}
-- evidence: [{"field": "services", "raw_item_id": 1, "snippet": "..."}, ...]
CREATE TABLE candidates (
    id           bigserial   PRIMARY KEY,
    email_key    text        NOT NULL UNIQUE,
    fields       jsonb       NOT NULL DEFAULT '{}',
    evidence     jsonb       NOT NULL DEFAULT '[]',
    review_state text        NOT NULL DEFAULT 'pending'
                 CHECK (review_state IN ('pending', 'approved', 'rejected', 'merged')),
    -- Set when review_state = 'merged': this record is folded into another candidate.
    merged_into  bigint      REFERENCES candidates (id) ON DELETE SET NULL,
    client_id    bigint      REFERENCES clients (id) ON DELETE SET NULL,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX candidates_review_state_idx ON candidates (review_state);
