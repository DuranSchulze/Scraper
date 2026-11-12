CREATE TABLE roles (
    id bigserial PRIMARY KEY,
    name text NOT NULL CHECK (length(btrim(name)) > 0),
    routes text[] NOT NULL CHECK (
        cardinality(routes) > 0
        AND routes <@ ARRAY['review','scraping','masterlist','accounts']::text[]
        AND array_position(routes, NULL) IS NULL
    ),
    locked boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX roles_name_unique ON roles (lower(name));

INSERT INTO roles(name,routes,locked) VALUES
    ('Owner', ARRAY['review','scraping','masterlist','accounts'], true),
    ('Developer', ARRAY['review','scraping'], false);
INSERT INTO roles(name,routes)
    SELECT 'Review only', ARRAY['review']
    WHERE EXISTS (SELECT 1 FROM users WHERE role='member' AND 'review'=ANY(routes) AND NOT 'scraping'=ANY(routes));
INSERT INTO roles(name,routes)
    SELECT 'Scraping only', ARRAY['scraping']
    WHERE EXISTS (SELECT 1 FROM users WHERE role='member' AND 'scraping'=ANY(routes) AND NOT 'review'=ANY(routes));

ALTER TABLE users ADD COLUMN role_id bigint REFERENCES roles(id);
UPDATE users SET role_id = (
    SELECT id FROM roles WHERE name = CASE
        WHEN users.role='admin' THEN 'Owner'
        WHEN users.routes @> ARRAY['review','scraping'] THEN 'Developer'
        WHEN 'review'=ANY(users.routes) THEN 'Review only'
        ELSE 'Scraping only'
    END
);
ALTER TABLE users ALTER COLUMN role_id SET NOT NULL;
ALTER TABLE users DROP COLUMN role, DROP COLUMN routes;
