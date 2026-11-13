UPDATE candidates SET master_match=NULL, master_ids=NULL;
DROP TABLE master_clients, master_imports;

ALTER TABLE roles DROP CONSTRAINT roles_routes_check;
UPDATE roles SET routes = CASE
    WHEN cardinality(array_remove(routes, 'masterlist')) = 0 THEN ARRAY['review']::text[]
    ELSE array_remove(routes, 'masterlist')
END;
ALTER TABLE roles ADD CONSTRAINT roles_routes_check CHECK (
    cardinality(routes) > 0
    AND routes <@ ARRAY['review','scraping','accounts']::text[]
    AND array_position(routes, NULL) IS NULL
);

ALTER TABLE jobs ADD COLUMN progress jsonb;
CREATE TABLE directory_tag_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    tagged_at timestamptz
);
INSERT INTO directory_tag_state(singleton) VALUES (true);
