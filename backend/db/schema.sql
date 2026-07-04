-- Office Electrical Monitor — SQLite schema.
-- Verbatim source of truth: PROJECT_PLAN.md §4. Keep the two files in sync.
-- 3 rooms x (2 fans + 3 lights) = 15 devices total (see PROJECT_PLAN.md §0.2 / §0 for the
-- device-count correction — the brief's own "18" does not match its own per-room breakdown).

CREATE TABLE IF NOT EXISTS room (
  id          TEXT PRIMARY KEY,     -- 'drawing', 'work1', 'work2'
  name        TEXT NOT NULL,
  description TEXT
);

CREATE TABLE IF NOT EXISTS device (
  id            TEXT PRIMARY KEY,   -- 'drawing.fan.1'
  room_id       TEXT NOT NULL REFERENCES room(id),
  kind          TEXT NOT NULL,      -- 'fan' | 'light'
  device_index  INTEGER NOT NULL,   -- 1..2 fans, 1..3 lights
                                     -- (named device_index, not index -- `index` is a
                                     -- reserved SQLite keyword and breaks CREATE TABLE unquoted)
  name          TEXT NOT NULL,      -- 'Drawing Room Fan 1'
  state         TEXT NOT NULL,      -- 'on' | 'off'
  wattage       INTEGER NOT NULL,   -- 60 fan, 15 light
  last_changed  TEXT NOT NULL       -- ISO8601
);

-- Derived state for the "all devices in a room have been on continuously >2h" alert.
-- last_changed per device resets the moment any single device toggles, so per-device
-- state cannot detect room-wide continuous-on by itself.
-- We persist this so the evaluator survives restarts.
CREATE TABLE IF NOT EXISTS room_state (
  room_id          TEXT PRIMARY KEY REFERENCES room(id),
  all_on_since     TEXT,                -- ISO8601, NULL = not currently all on
  last_alert_at    TEXT,                -- debounce for 'all_on_2h' kind
  last_hours_alert TEXT                 -- debounce for 'after_hours' kind
);

CREATE TABLE IF NOT EXISTS alert (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id     TEXT REFERENCES room(id),
  device_id   TEXT REFERENCES device(id),
  kind        TEXT NOT NULL,        -- 'after_hours' | 'all_on_2h'
  message     TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  acked       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS daily_usage (
  day          TEXT PRIMARY KEY,    -- 'YYYY-MM-DD'
  watt_seconds REAL DEFAULT 0       -- integrated power for kWh calc
);

CREATE INDEX IF NOT EXISTS idx_device_room ON device(room_id);
CREATE INDEX IF NOT EXISTS idx_alerts_time ON alert(created_at DESC);
