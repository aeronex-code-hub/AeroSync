-- AeroSync PostgreSQL target schema (commercial build foundation)
CREATE TABLE IF NOT EXISTS mqtt_messages (
  id BIGSERIAL PRIMARY KEY, received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  connection_id TEXT, source_id TEXT, topic TEXT NOT NULL, payload_raw TEXT NOT NULL,
  payload_json JSONB, payload_type TEXT, bytes INTEGER
);
CREATE INDEX IF NOT EXISTS mqtt_messages_received_idx ON mqtt_messages(received_at DESC);
CREATE INDEX IF NOT EXISTS mqtt_messages_topic_idx ON mqtt_messages(topic);

CREATE TABLE IF NOT EXISTS rid_sources (
  id TEXT PRIMARY KEY, name TEXT, vendor TEXT, model TEXT, device_type TEXT,
  topic_prefix TEXT, parser TEXT, status TEXT, latitude DOUBLE PRECISION,
  longitude DOUBLE PRECISION, altitude DOUBLE PRECISION, last_seen TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS rid_targets (
  id TEXT PRIMARY KEY, model TEXT, source_id TEXT REFERENCES rid_sources(id),
  first_seen TIMESTAMPTZ, last_seen TIMESTAMPTZ, status TEXT
);
CREATE TABLE IF NOT EXISTS rid_sessions (
  id BIGSERIAL PRIMARY KEY, target_id TEXT REFERENCES rid_targets(id), source_id TEXT REFERENCES rid_sources(id),
  started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ, snapshot_path TEXT
);
CREATE TABLE IF NOT EXISTS rid_messages (
  id BIGSERIAL PRIMARY KEY, received_at TIMESTAMPTZ NOT NULL DEFAULT now(), source_id TEXT,
  target_id TEXT, topic TEXT, payload_raw TEXT NOT NULL, payload_json JSONB
);
CREATE INDEX IF NOT EXISTS rid_messages_received_idx ON rid_messages(received_at DESC);
CREATE TABLE IF NOT EXISTS rid_track_points (
  id BIGSERIAL PRIMARY KEY, session_id BIGINT REFERENCES rid_sessions(id) ON DELETE CASCADE,
  recorded_at TIMESTAMPTZ NOT NULL, latitude DOUBLE PRECISION NOT NULL,
  longitude DOUBLE PRECISION NOT NULL, altitude DOUBLE PRECISION, height DOUBLE PRECISION,
  speed DOUBLE PRECISION, heading DOUBLE PRECISION, pilot_lat DOUBLE PRECISION,
  pilot_lng DOUBLE PRECISION, home_lat DOUBLE PRECISION, home_lng DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS rid_track_session_time_idx ON rid_track_points(session_id, recorded_at);
CREATE TABLE IF NOT EXISTS system_events (
  id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), level TEXT, module TEXT, message TEXT, details JSONB
);
CREATE TABLE IF NOT EXISTS audit_logs (
  id BIGSERIAL PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), username TEXT, action TEXT, source_ip INET, details JSONB
);
