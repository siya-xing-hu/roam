CREATE TABLE roam.datasets (
  key text PRIMARY KEY,
  metadata jsonb NOT NULL,
  source_sha256 text NOT NULL,
  imported_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE roam.regions (
  adcode text PRIMARY KEY CHECK (adcode ~ '^[0-9]{6}$'),
  name text NOT NULL,
  level text NOT NULL CHECK (level IN ('country', 'province', 'city', 'district')),
  parent_adcode text REFERENCES roam.regions(adcode) DEFERRABLE INITIALLY DEFERRED,
  children_count integer NOT NULL CHECK (children_count >= 0),
  bounds double precision[] NOT NULL CHECK (array_length(bounds, 1) = 4),
  coord_system text NOT NULL DEFAULT 'GCJ-02' CHECK (coord_system = 'GCJ-02'),
  geometry jsonb,
  properties jsonb NOT NULL,
  source_path text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX regions_parent_idx ON roam.regions(parent_adcode);

CREATE TABLE roam.attractions (
  id text PRIMARY KEY,
  name text NOT NULL CHECK (length(name) > 0),
  province text NOT NULL,
  city text NOT NULL,
  district text NOT NULL,
  adcode text NOT NULL CHECK (adcode ~ '^[0-9]{6}$'),
  region_adcode text REFERENCES roam.regions(adcode),
  region_resolution text NOT NULL CHECK (region_resolution IN ('exact', 'ancestor', 'unmatched')),
  address text NOT NULL,
  lng double precision NOT NULL CHECK (lng BETWEEN -180 AND 180),
  lat double precision NOT NULL CHECK (lat BETWEEN -90 AND 90),
  coord_system text NOT NULL DEFAULT 'GCJ-02' CHECK (coord_system = 'GCJ-02'),
  level smallint NOT NULL CHECK (level BETWEEN 0 AND 9),
  level_source text NOT NULL CHECK (level_source IN ('official', 'manual')),
  tags text[] NOT NULL DEFAULT '{}',
  source jsonb NOT NULL,
  coordinate_source jsonb,
  ordinal integer NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX attractions_region_rank_idx ON roam.attractions(region_adcode, level DESC, id);
CREATE INDEX attractions_rank_idx ON roam.attractions(level DESC, id);
CREATE INDEX attractions_lng_lat_idx ON roam.attractions(lng, lat);

CREATE TABLE roam.imports (
  source_sha256 text PRIMARY KEY,
  metadata jsonb NOT NULL,
  attraction_count integer NOT NULL,
  region_count integer NOT NULL,
  imported_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN roam.attractions.region_adcode IS '最小可用边界区域；保留原始 adcode，不能将回退解释为已核验区划';
COMMENT ON COLUMN roam.regions.geometry IS '原始 GCJ-02 GeoJSON；不能标记为 EPSG:4326 或直接用于以米为单位的测距';
