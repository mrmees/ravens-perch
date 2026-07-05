"""
Ravens Perch - SQLite Database Layer
"""
import sqlite3
import json
import logging
import threading
from datetime import datetime
from typing import Optional, Dict, List, Any, Tuple
from contextlib import contextmanager

from .config import DATABASE_PATH, DATA_DIR

logger = logging.getLogger(__name__)

# Thread-local storage for persistent database connections
_thread_local = threading.local()


def ensure_data_dir():
    """Ensure the data directory exists."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _create_connection() -> sqlite3.Connection:
    """Create and configure a new database connection."""
    ensure_data_dir()
    conn = sqlite3.connect(
        str(DATABASE_PATH),
        timeout=30.0,
        check_same_thread=False  # Safe because we use thread-local storage
    )
    conn.row_factory = sqlite3.Row
    # WAL mode allows concurrent readers with one writer - much better for
    # web UI reads happening while main thread writes
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")  # Faster writes, safe with WAL
    conn.execute("PRAGMA busy_timeout=5000")  # Wait up to 5s for locks
    conn.execute("PRAGMA foreign_keys = ON")
    logger.debug(f"Created new DB connection for thread {threading.current_thread().name}")
    return conn


def _get_thread_connection() -> sqlite3.Connection:
    """Get or create a persistent connection for the current thread."""
    conn = getattr(_thread_local, 'connection', None)
    if conn is None:
        conn = _create_connection()
        _thread_local.connection = conn
    return conn


@contextmanager
def get_connection():
    """Get a database connection with context management.

    Uses thread-local persistent connections to avoid the overhead of
    opening/closing connections and re-running PRAGMAs for each operation.
    """
    conn = _get_thread_connection()
    try:
        yield conn
    except sqlite3.Error as e:
        if conn.in_transaction:
            conn.rollback()
        # If connection is broken, clear it so next call creates a fresh one
        logger.warning(f"Database error, will reconnect: {e}")
        _thread_local.connection = None
        raise
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def close_thread_connection():
    """Close the database connection for the current thread.

    Call this during graceful shutdown to properly close connections.
    """
    conn = getattr(_thread_local, 'connection', None)
    if conn is not None:
        try:
            conn.close()
            logger.debug(f"Closed DB connection for thread {threading.current_thread().name}")
        except Exception as e:
            logger.warning(f"Error closing DB connection: {e}")
        finally:
            _thread_local.connection = None


def _raise_if_duplicate_identity_keys(cursor: sqlite3.Cursor, table_name: str):
    """Raise a clear migration error if identity keys are not unique."""
    cursor.execute(f"""
        SELECT identity_key, COUNT(*) AS duplicate_count
        FROM {table_name}
        WHERE identity_key IS NOT NULL AND identity_key != ''
        GROUP BY identity_key
        HAVING COUNT(*) > 1
        LIMIT 5
    """)
    duplicates = cursor.fetchall()
    if duplicates:
        examples = ", ".join(
            f"{row['identity_key']} ({row['duplicate_count']} rows)"
            for row in duplicates
        )
        message = f"Duplicate identity_key values in {table_name}: {examples}"
        logger.error(message)
        raise RuntimeError(message)


def _legacy_hardware_id_from_serial_identity(identity_key: str) -> Optional[str]:
    """Return the legacy hardware ID form for a canonical serial identity key."""
    prefix = "serial:"
    if not identity_key.startswith(prefix):
        return None

    parts = identity_key.split(":", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return None

    return f"{parts[1]}-{parts[2]}"


def _serial_identity_from_legacy_hardware_id(identity_key: str) -> Optional[str]:
    """Return the canonical serial identity key for a legacy Name-Serial key."""
    if ":" in identity_key or "-" not in identity_key:
        return None

    hardware_name, serial_number = identity_key.rsplit("-", 1)
    if not hardware_name or not serial_number:
        return None

    return f"serial:{hardware_name}:{serial_number}"


def _equivalent_ignore_keys(identity_key: str) -> Tuple[str, ...]:
    """Return canonical and legacy keys that may refer to the same serial camera."""
    keys = [identity_key]

    legacy_hardware_id = _legacy_hardware_id_from_serial_identity(identity_key)
    if legacy_hardware_id and legacy_hardware_id not in keys:
        keys.append(legacy_hardware_id)

    serial_identity = _serial_identity_from_legacy_hardware_id(identity_key)
    if serial_identity and serial_identity not in keys:
        keys.append(serial_identity)

    return tuple(keys)


def init_db():
    """Initialize the database schema."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Cameras table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cameras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hardware_id TEXT UNIQUE NOT NULL,
                identity_key TEXT UNIQUE,
                identity_strategy TEXT DEFAULT 'legacy',
                by_path TEXT,
                by_id TEXT,
                reported_serial_number TEXT,
                hardware_name TEXT NOT NULL,
                serial_number TEXT,
                friendly_name TEXT,
                device_path TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP,
                connected BOOLEAN DEFAULT FALSE,
                enabled BOOLEAN DEFAULT TRUE,
                moonraker_uid TEXT
            )
        """)

        # Camera settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS camera_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id INTEGER UNIQUE REFERENCES cameras(id) ON DELETE CASCADE,
                format TEXT DEFAULT 'mjpeg',
                resolution TEXT DEFAULT '1280x720',
                framerate REAL DEFAULT 30,
                encoder TEXT DEFAULT 'libx264',
                bitrate TEXT DEFAULT '4M',
                preset TEXT DEFAULT 'ultrafast',
                rotation INTEGER DEFAULT 0,
                v4l2_controls TEXT,
                audio_enabled BOOLEAN DEFAULT FALSE,
                audio_device TEXT,
                overlay_enabled BOOLEAN DEFAULT FALSE,
                overlay_font_size INTEGER DEFAULT 24,
                overlay_position TEXT DEFAULT 'bottom_center',
                overlay_color TEXT DEFAULT 'white',
                overlay_font TEXT,
                overlay_multiline BOOLEAN DEFAULT FALSE,
                overlay_show_labels BOOLEAN DEFAULT TRUE,
                overlay_show_progress BOOLEAN DEFAULT TRUE,
                overlay_show_layer BOOLEAN DEFAULT TRUE,
                overlay_show_eta BOOLEAN DEFAULT TRUE,
                overlay_show_elapsed BOOLEAN DEFAULT FALSE,
                overlay_show_filename BOOLEAN DEFAULT FALSE,
                overlay_show_hotend_temp BOOLEAN DEFAULT FALSE,
                overlay_show_bed_temp BOOLEAN DEFAULT FALSE,
                overlay_show_fan_speed BOOLEAN DEFAULT FALSE,
                overlay_show_print_state BOOLEAN DEFAULT FALSE,
                overlay_show_filament_used BOOLEAN DEFAULT FALSE,
                overlay_show_current_time BOOLEAN DEFAULT FALSE,
                overlay_show_print_speed BOOLEAN DEFAULT FALSE,
                overlay_show_z_height BOOLEAN DEFAULT FALSE,
                overlay_show_live_velocity BOOLEAN DEFAULT FALSE,
                overlay_show_flow_rate BOOLEAN DEFAULT FALSE,
                overlay_show_filament_type BOOLEAN DEFAULT FALSE,
                printing_framerate REAL,
                standby_framerate REAL,
                standby_enabled BOOLEAN DEFAULT FALSE,
                overlay_standby_text TEXT
            )
        """)

        # Camera capabilities table (cached)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS camera_capabilities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id INTEGER REFERENCES cameras(id) ON DELETE CASCADE,
                capabilities TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(camera_id)
            )
        """)

        # Global settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Logs table (for web UI display)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                level TEXT,
                message TEXT,
                camera_id INTEGER REFERENCES cameras(id) ON DELETE SET NULL
            )
        """)

        # Ignored cameras table (blacklist)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ignored_cameras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hardware_id TEXT UNIQUE NOT NULL,
                identity_key TEXT UNIQUE,
                hardware_name TEXT,
                reason TEXT,
                ignored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create indexes for common queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cameras_hardware_id ON cameras(hardware_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cameras_connected ON cameras(connected)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)")

        # Migrations: Add new columns to existing tables if they don't exist
        # Check existing columns in camera_settings
        cursor.execute("PRAGMA table_info(camera_settings)")
        existing_columns = {row['name'] for row in cursor.fetchall()}

        new_columns = [
            ("overlay_enabled", "BOOLEAN DEFAULT FALSE"),
            ("overlay_font_size", "INTEGER DEFAULT 24"),
            ("overlay_position", "TEXT DEFAULT 'bottom_center'"),
            ("overlay_color", "TEXT DEFAULT 'white'"),
            ("overlay_font", "TEXT"),
            ("overlay_multiline", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_labels", "BOOLEAN DEFAULT TRUE"),
            ("overlay_show_progress", "BOOLEAN DEFAULT TRUE"),
            ("overlay_show_layer", "BOOLEAN DEFAULT TRUE"),
            ("overlay_show_eta", "BOOLEAN DEFAULT TRUE"),
            ("overlay_show_elapsed", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_filename", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_hotend_temp", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_bed_temp", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_fan_speed", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_print_state", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_filament_used", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_current_time", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_print_speed", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_z_height", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_live_velocity", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_flow_rate", "BOOLEAN DEFAULT FALSE"),
            ("overlay_show_filament_type", "BOOLEAN DEFAULT FALSE"),
            ("printing_framerate", "REAL"),
            ("standby_framerate", "REAL"),
            ("standby_enabled", "BOOLEAN DEFAULT FALSE"),
            ("overlay_standby_text", "TEXT"),
        ]

        for col_name, col_def in new_columns:
            if col_name not in existing_columns:
                try:
                    cursor.execute(f"ALTER TABLE camera_settings ADD COLUMN {col_name} {col_def}")
                    logger.info(f"Added column {col_name} to camera_settings")
                except Exception as e:
                    logger.debug(f"Column {col_name} may already exist: {e}")

        cursor.execute("PRAGMA table_info(cameras)")
        camera_columns = {row['name'] for row in cursor.fetchall()}
        camera_new_columns = [
            ("identity_key", "TEXT"),
            ("identity_strategy", "TEXT DEFAULT 'legacy'"),
            ("by_path", "TEXT"),
            ("by_id", "TEXT"),
            ("reported_serial_number", "TEXT"),
        ]
        for col_name, col_def in camera_new_columns:
            if col_name not in camera_columns:
                cursor.execute(f"ALTER TABLE cameras ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added column {col_name} to cameras")

        cursor.execute("""
            UPDATE cameras
            SET identity_key = 'serial:' || hardware_name || ':' || serial_number
            WHERE serial_number IS NOT NULL
              AND serial_number != ''
              AND (
                  identity_key IS NULL
                  OR identity_key = ''
                  OR (
                      identity_key = hardware_id
                      AND (
                          identity_strategy IS NULL
                          OR identity_strategy = ''
                          OR identity_strategy = 'legacy'
                      )
                  )
              )
        """)
        cursor.execute("""
            UPDATE cameras
            SET identity_key = hardware_id
            WHERE (identity_key IS NULL OR identity_key = '')
              AND (serial_number IS NULL OR serial_number = '')
        """)
        cursor.execute("""
            UPDATE cameras
            SET identity_strategy = 'serial'
            WHERE serial_number IS NOT NULL
              AND serial_number != ''
              AND identity_key = 'serial:' || hardware_name || ':' || serial_number
              AND (
                  identity_strategy IS NULL
                  OR identity_strategy = ''
                  OR identity_strategy = 'legacy'
              )
        """)
        cursor.execute("""
            UPDATE cameras
            SET identity_strategy = 'legacy'
            WHERE (identity_strategy IS NULL OR identity_strategy = '')
              AND (serial_number IS NULL OR serial_number = '')
        """)
        cursor.execute("""
            UPDATE cameras
            SET reported_serial_number = serial_number
            WHERE reported_serial_number IS NULL
              AND serial_number IS NOT NULL
              AND serial_number != ''
        """)
        _raise_if_duplicate_identity_keys(cursor, "cameras")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cameras_identity_key ON cameras(identity_key)")

        cursor.execute("PRAGMA table_info(ignored_cameras)")
        ignored_columns = {row['name'] for row in cursor.fetchall()}
        if "identity_key" not in ignored_columns:
            cursor.execute("ALTER TABLE ignored_cameras ADD COLUMN identity_key TEXT")
            logger.info("Added column identity_key to ignored_cameras")
        cursor.execute("""
            UPDATE ignored_cameras
            SET identity_key = 'serial:' || hardware_name || ':' ||
                substr(hardware_id, length(hardware_name) + 2)
            WHERE (identity_key IS NULL OR identity_key = '')
              AND hardware_name IS NOT NULL
              AND hardware_name != ''
              AND substr(hardware_id, 1, length(hardware_name) + 1) = hardware_name || '-'
              AND substr(hardware_id, length(hardware_name) + 2) != ''
        """)
        cursor.execute("""
            UPDATE ignored_cameras
            SET identity_key = hardware_id
            WHERE identity_key IS NULL OR identity_key = ''
        """)
        _raise_if_duplicate_identity_keys(cursor, "ignored_cameras")
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ignored_cameras_identity_key ON ignored_cameras(identity_key)")

        conn.commit()
        logger.info("Database initialized successfully")


# ============ Camera Functions ============

def get_camera_by_hardware_id(hardware_id: str) -> Optional[Dict]:
    """Lookup camera by hardware ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cameras WHERE hardware_id = ?", (hardware_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_camera_by_identity_key(identity_key: str) -> Optional[Dict]:
    """Lookup camera by canonical identity key."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cameras WHERE identity_key = ?", (identity_key,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_camera_by_id(camera_id: int) -> Optional[Dict]:
    """Lookup camera by database ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_camera_by_device_path(device_path: str) -> Optional[Dict]:
    """Lookup camera by current device path."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM cameras WHERE device_path = ? AND connected = 1",
            (device_path,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def create_camera(hardware_name: str, serial_number: Optional[str],
                  friendly_name: Optional[str] = None,
                  device_path: Optional[str] = None,
                  identity_key: Optional[str] = None,
                  identity_strategy: str = "legacy",
                  by_path: Optional[str] = None,
                  by_id: Optional[str] = None,
                  reported_serial_number: Optional[str] = None) -> int:
    """Create a new camera record. Returns the camera ID.

    If camera with same identity_key already exists, returns existing ID.
    """
    if identity_key is None:
        if serial_number:
            identity_key = f"serial:{hardware_name}:{serial_number}"
            identity_strategy = "serial"
            hardware_id = f"{hardware_name}-{serial_number}"
        else:
            identity_key = hardware_name
            identity_strategy = "legacy"
            hardware_id = hardware_name
    else:
        hardware_id = identity_key
    if reported_serial_number is None:
        reported_serial_number = serial_number
    if not friendly_name:
        friendly_name = hardware_name

    with get_connection() as conn:
        cursor = conn.cursor()

        # Use INSERT OR IGNORE to handle race conditions
        cursor.execute("""
            INSERT OR IGNORE INTO cameras (
                hardware_id, identity_key, identity_strategy, hardware_name,
                serial_number, reported_serial_number, by_path, by_id,
                friendly_name, device_path, connected, last_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        """, (
            hardware_id, identity_key, identity_strategy, hardware_name,
            serial_number, reported_serial_number, by_path, by_id,
            friendly_name, device_path,
        ))

        if cursor.rowcount == 0:
            # Camera already exists, get its ID
            cursor.execute("SELECT id FROM cameras WHERE identity_key = ?", (identity_key,))
            row = cursor.fetchone()
            if row is None:
                cursor.execute("SELECT id FROM cameras WHERE hardware_id = ?", (hardware_id,))
                row = cursor.fetchone()
            if row is None:
                raise RuntimeError(
                    f"Camera insert was ignored but no existing camera matched "
                    f"identity_key={identity_key!r} or hardware_id={hardware_id!r}"
                )
            camera_id = row[0]
            # Update connection status
            cursor.execute("""
                UPDATE cameras
                SET connected = 1,
                    device_path = ?,
                    identity_strategy = ?,
                    by_path = ?,
                    by_id = ?,
                    reported_serial_number = ?,
                    last_seen = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (device_path, identity_strategy, by_path, by_id, reported_serial_number, camera_id))
            conn.commit()
            logger.info(f"Camera already exists {camera_id}: {friendly_name} ({hardware_id})")
            return camera_id

        camera_id = cursor.lastrowid

        # Create default settings for this camera
        cursor.execute("""
            INSERT OR IGNORE INTO camera_settings (camera_id) VALUES (?)
        """, (camera_id,))

        conn.commit()
        logger.info(f"Created camera {camera_id}: {friendly_name} ({hardware_id})")
        return camera_id


def update_camera(camera_id: int, **fields) -> bool:
    """Update camera fields."""
    if not fields:
        return False

    allowed_fields = {
        'friendly_name', 'device_path', 'connected', 'enabled',
        'last_seen', 'moonraker_uid'
    }
    fields = {k: v for k, v in fields.items() if k in allowed_fields}

    if not fields:
        return False

    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [camera_id]

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE cameras SET {set_clause} WHERE id = ?",
            values
        )
        conn.commit()
        return cursor.rowcount > 0


def mark_camera_connected(camera_id: int, device_path: str) -> bool:
    """Mark a camera as connected."""
    return update_camera(
        camera_id,
        device_path=device_path,
        connected=True,
        last_seen=datetime.now().isoformat()
    )


def mark_camera_disconnected(camera_id: int) -> bool:
    """Mark a camera as disconnected."""
    return update_camera(camera_id, connected=False, device_path=None)


def get_all_cameras(connected_only: bool = False) -> List[Dict]:
    """List all cameras."""
    with get_connection() as conn:
        cursor = conn.cursor()
        if connected_only:
            cursor.execute("SELECT * FROM cameras WHERE connected = 1 ORDER BY friendly_name")
        else:
            cursor.execute("SELECT * FROM cameras ORDER BY connected DESC, friendly_name")
        return [dict(row) for row in cursor.fetchall()]


def delete_camera(camera_id: int) -> bool:
    """Delete a camera and all related records."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
        conn.commit()
        return cursor.rowcount > 0


# ============ Camera Settings Functions ============

def get_camera_settings(camera_id: int) -> Optional[Dict]:
    """Get settings for a camera."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM camera_settings WHERE camera_id = ?",
            (camera_id,)
        )
        row = cursor.fetchone()
        if row:
            settings = dict(row)
            # Parse JSON fields
            if settings.get('v4l2_controls'):
                try:
                    settings['v4l2_controls'] = json.loads(settings['v4l2_controls'])
                except json.JSONDecodeError:
                    settings['v4l2_controls'] = {}
            return settings
        return None


def save_camera_settings(camera_id: int, settings_dict: Dict) -> bool:
    """Save settings for a camera."""
    allowed_fields = {
        'format', 'resolution', 'framerate', 'encoder', 'bitrate',
        'preset', 'rotation', 'v4l2_controls', 'audio_enabled', 'audio_device',
        'overlay_enabled', 'overlay_font_size', 'overlay_position', 'overlay_color',
        'overlay_font', 'overlay_multiline', 'overlay_show_labels',
        'overlay_show_progress', 'overlay_show_layer', 'overlay_show_eta',
        'overlay_show_elapsed', 'overlay_show_filename', 'overlay_show_hotend_temp',
        'overlay_show_bed_temp', 'overlay_show_fan_speed', 'overlay_show_print_state',
        'overlay_show_filament_used', 'overlay_show_current_time',
        'overlay_show_print_speed', 'overlay_show_z_height',
        'overlay_show_live_velocity', 'overlay_show_flow_rate',
        'overlay_show_filament_type',
        'overlay_standby_text',
        'printing_framerate', 'standby_framerate', 'standby_enabled'
    }
    settings_dict = {k: v for k, v in settings_dict.items() if k in allowed_fields}

    if not settings_dict:
        return False

    # Serialize JSON fields
    if 'v4l2_controls' in settings_dict and isinstance(settings_dict['v4l2_controls'], dict):
        settings_dict['v4l2_controls'] = json.dumps(settings_dict['v4l2_controls'])

    with get_connection() as conn:
        cursor = conn.cursor()

        # Check if settings exist
        cursor.execute(
            "SELECT id FROM camera_settings WHERE camera_id = ?",
            (camera_id,)
        )

        if cursor.fetchone():
            # Update existing
            set_clause = ", ".join(f"{k} = ?" for k in settings_dict.keys())
            values = list(settings_dict.values()) + [camera_id]
            cursor.execute(
                f"UPDATE camera_settings SET {set_clause} WHERE camera_id = ?",
                values
            )
        else:
            # Insert new
            settings_dict['camera_id'] = camera_id
            columns = ", ".join(settings_dict.keys())
            placeholders = ", ".join("?" * len(settings_dict))
            cursor.execute(
                f"INSERT INTO camera_settings ({columns}) VALUES ({placeholders})",
                list(settings_dict.values())
            )

        conn.commit()
        return True


# ============ Camera Capabilities Functions ============

def get_camera_capabilities(camera_id: int) -> Optional[Dict]:
    """Get cached capabilities for a camera."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT capabilities, updated_at FROM camera_capabilities WHERE camera_id = ?",
            (camera_id,)
        )
        row = cursor.fetchone()
        if row:
            try:
                return {
                    'capabilities': json.loads(row['capabilities']),
                    'updated_at': row['updated_at']
                }
            except json.JSONDecodeError:
                return None
        return None


def save_camera_capabilities(camera_id: int, capabilities: Dict) -> bool:
    """Save capabilities for a camera."""
    capabilities_json = json.dumps(capabilities)

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO camera_capabilities (camera_id, capabilities, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(camera_id) DO UPDATE SET
                capabilities = excluded.capabilities,
                updated_at = CURRENT_TIMESTAMP
        """, (camera_id, capabilities_json))
        conn.commit()
        return True


# ============ Global Settings Functions ============

def get_setting(key: str, default: Any = None) -> Any:
    """Get a global setting."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row:
            try:
                return json.loads(row['value'])
            except json.JSONDecodeError:
                return row['value']
        return default


def set_setting(key: str, value: Any) -> bool:
    """Set a global setting."""
    value_json = json.dumps(value) if not isinstance(value, str) else value

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value_json))
        conn.commit()
        return True


def get_all_settings() -> Dict[str, Any]:
    """Get all global settings."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        settings = {}
        for row in cursor.fetchall():
            try:
                settings[row['key']] = json.loads(row['value'])
            except json.JSONDecodeError:
                settings[row['key']] = row['value']
        return settings


# ============ Log Functions ============

def add_log(level: str, message: str, camera_id: Optional[int] = None) -> int:
    """Add a log entry. Returns the log ID."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO logs (level, message, camera_id)
            VALUES (?, ?, ?)
        """, (level.upper(), message, camera_id))
        log_id = cursor.lastrowid
        conn.commit()
        return log_id


def get_logs(limit: int = 100, level: Optional[str] = None,
             camera_id: Optional[int] = None,
             offset: int = 0) -> List[Dict]:
    """Retrieve logs with optional filtering."""
    with get_connection() as conn:
        cursor = conn.cursor()

        query = "SELECT l.*, c.friendly_name as camera_name FROM logs l "
        query += "LEFT JOIN cameras c ON l.camera_id = c.id "
        conditions = []
        params = []

        if level:
            conditions.append("l.level = ?")
            params.append(level.upper())

        if camera_id:
            conditions.append("l.camera_id = ?")
            params.append(camera_id)

        if conditions:
            query += "WHERE " + " AND ".join(conditions) + " "

        query += "ORDER BY l.timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def clear_old_logs(days: int = 7) -> int:
    """Clear logs older than specified days. Returns count deleted."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM logs
            WHERE timestamp < datetime('now', '-' || ? || ' days')
        """, (days,))
        count = cursor.rowcount
        conn.commit()
        return count


# ============ Utility Functions ============

def get_camera_with_settings(camera_id: int) -> Optional[Dict]:
    """Get camera with its settings in one call."""
    camera = get_camera_by_id(camera_id)
    if camera:
        camera['settings'] = get_camera_settings(camera_id)
        camera['capabilities'] = get_camera_capabilities(camera_id)
    return camera


def get_all_cameras_with_settings(connected_only: bool = False) -> List[Dict]:
    """Get all cameras with their settings."""
    cameras = get_all_cameras(connected_only)
    for camera in cameras:
        camera['settings'] = get_camera_settings(camera['id'])
    return cameras


# ============ Ignored Cameras Functions ============

def is_camera_ignored(identity_key: str) -> bool:
    """Check if an identity key is in the ignore list.

    During the Task 3 identity transition, daemon/routes may still pass a
    legacy hardware_id, and old rows may still store only legacy keys. Bridge
    canonical serial identities and legacy hardware IDs in both directions.
    """
    equivalent_keys = _equivalent_ignore_keys(identity_key)
    placeholders = ",".join("?" for _ in equivalent_keys)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM ignored_cameras
            WHERE identity_key IN ({placeholders})
               OR hardware_id IN ({placeholders})
        """.format(placeholders=placeholders), equivalent_keys + equivalent_keys)
        return cursor.fetchone() is not None


def ignore_camera(identity_key: str, hardware_name: str = None, reason: str = None) -> bool:
    """Add a serial-identified camera to the ignore list."""
    equivalent_keys = _equivalent_ignore_keys(identity_key)
    placeholders = ",".join("?" for _ in equivalent_keys)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id FROM ignored_cameras
            WHERE identity_key IN ({placeholders})
               OR hardware_id IN ({placeholders})
        """.format(placeholders=placeholders), equivalent_keys + equivalent_keys)
        if cursor.fetchone() is not None:
            conn.commit()
            return True

        cursor.execute("""
            INSERT OR IGNORE INTO ignored_cameras (hardware_id, identity_key, hardware_name, reason)
            VALUES (?, ?, ?, ?)
        """, (identity_key, identity_key, hardware_name, reason))
        conn.commit()
        if cursor.rowcount:
            logger.info(f"Added camera to ignore list: {identity_key}")
        return True


def unignore_camera(identity_key: str) -> bool:
    """Remove an identity key from the ignore list."""
    equivalent_keys = _equivalent_ignore_keys(identity_key)
    placeholders = ",".join("?" for _ in equivalent_keys)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM ignored_cameras
            WHERE identity_key IN ({placeholders})
               OR hardware_id IN ({placeholders})
        """.format(placeholders=placeholders), equivalent_keys + equivalent_keys)
        conn.commit()
        return cursor.rowcount > 0


def get_ignored_cameras() -> List[Dict]:
    """Get all ignored cameras."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ignored_cameras ORDER BY ignored_at DESC")
        return [dict(row) for row in cursor.fetchall()]


def delete_camera_completely(camera_id: int) -> Tuple[bool, Optional[str]]:
    """
    Delete a camera and all related data completely.

    Returns: (success, identity_key) - identity key for optional ignore list.
    Falls back to hardware_id for rows without identity_key.
    """
    camera = get_camera_by_id(camera_id)
    if not camera:
        return False, None

    identity_key = camera.get('identity_key') or camera.get('hardware_id')

    with get_connection() as conn:
        cursor = conn.cursor()
        # Delete camera (cascades to settings and capabilities)
        cursor.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
        conn.commit()

        if cursor.rowcount > 0:
            logger.info(f"Deleted camera {camera_id} ({identity_key})")
            return True, identity_key
        return False, None


def delete_all_cameras() -> int:
    """
    Delete all cameras and their settings.

    Returns: count of cameras deleted
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cameras")
        count = cursor.fetchone()[0]

        # Delete all cameras (cascades to settings and capabilities)
        cursor.execute("DELETE FROM cameras")
        conn.commit()

        logger.info(f"Deleted all cameras ({count} total)")
        return count
