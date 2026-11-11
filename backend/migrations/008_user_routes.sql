ALTER TABLE users
    ADD COLUMN routes text[] NOT NULL DEFAULT ARRAY['review','scraping']::text[],
    ADD CONSTRAINT users_routes_check CHECK (
        cardinality(routes) > 0
        AND routes <@ ARRAY['review','scraping']::text[]
        AND array_position(routes, NULL) IS NULL
    );
