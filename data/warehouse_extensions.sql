-- Administrator-only preparation. Never run through the application engine.
-- Does not modify AdventureWorks base tables or the reference folder.
\set ON_ERROR_STOP on
BEGIN;
CREATE SCHEMA IF NOT EXISTS acbi_demo;
CREATE TABLE IF NOT EXISTS acbi_demo.factory (
    factory_id integer PRIMARY KEY CHECK (factory_id BETWEEN 1 AND 3),
    name text NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS acbi_demo.location_factory (
    locationid integer PRIMARY KEY REFERENCES production.location(locationid),
    factory_id integer NOT NULL REFERENCES acbi_demo.factory(factory_id)
);
INSERT INTO acbi_demo.factory(factory_id, name)
VALUES (1, 'Factory A'), (2, 'Factory B'), (3, 'Factory C')
ON CONFLICT (factory_id) DO NOTHING;
-- Synthetic deterministic mapping. Preserve any existing administrator mapping.
INSERT INTO acbi_demo.location_factory(locationid, factory_id)
SELECT locationid, ((row_number() OVER (ORDER BY locationid) - 1) % 3 + 1)::integer
FROM production.location
ON CONFLICT (locationid) DO NOTHING;
COMMENT ON SCHEMA acbi_demo IS 'Synthetic ACBI factory dimension; not AdventureWorks business facts';
COMMENT ON TABLE acbi_demo.location_factory IS 'Synthetic round-robin location mapping; proposed, pending business approval';
GRANT USAGE ON SCHEMA acbi_demo TO acbi_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA acbi_demo TO acbi_ro;
COMMIT;
