"""
Ravens Perch - SQLite Database Layer
"""
import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from contextlib import contextmanager

from .config import DATABASE_PATH, DATA_DIR

logger = logging.getLogger(__name__)


def ensure_data_dir():
    """Ensure the data directory exists."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_connection():
    """Get a database connection with context management."""
    ensure_data_dir()
    conn = sqlite3.connect(str(DATABASE_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize the database schema."""
    with get_connection() as conn:
        cursor = conn.cursor()

        # Cameras table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cameras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hardware_id TEXT UNIQUE NOT NULL,
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
                framerate INTEGER DEFAULT 30,
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
                printing_framerate INTEGER,
                standby_framerate INTEGER
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
            ("printing_framerate", "INTEGER"),
            ("standby_framerate", "INTEGER"),
        ]

        for col_name, col_def in new_columns:
            if col_name not in existing_columns:
                try:
                    cursor.execute(f"ALTER TABLE camera_settings ADD COLUMN {col_name} {col_def}")
                    logger.info(f"Added column {col_name} to camera_settings")
                except Exception as e:
                    logger.debug(f"Column {col_name} may already exist: {e}")

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
                  device_path: Optional[str] = None) -> int:
    """Create a new camera record. Returns the camera ID.

    If camera with same hardware_id already exists, returns existing ID.
    """
    hardware_id = f"{hardware_name}-{serial_number}" if serial_number else hardware_name
    if not friendly_name:
        friendly_name = hardware_name

    with get_connection() as conn:
        cursor = conn.cursor()

        # Use INSERT OR IGNORE to handle race conditions
        cursor.execute("""
            INSERT OR IGNORE INTO cameras (hardware_id, hardware_name, serial_number,
                                 friendly_name, device_path, connected, last_seen)
            VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        """, (hardware_id, hardware_name, serial_number, friendly_name, device_path))

        if cursor.rowcount == 0:
            # Camera already exists, get its ID
            cursor.execute("SELECT id FROM cameras WHERE hardware_id = ?", (hardware_id,))
            camera_id = cursor.fetchone()[0]
            # Update connection status
            cursor.execute("""
                UPDATE cameras SET connected = 1, device_path = ?, last_seen = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (device_path, camera_id))
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
        'printing_framerate', 'standby_framerate'
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

def is_camera_ignored(hardware_id: str) -> bool:
    """Check if a hardware ID is in the ignore list."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM ignored_cameras WHERE hardware_id = ?",
            (hardware_id,)
        )
        return cursor.fetchone() is not None


def ignore_camera(hardware_id: str, hardware_name: str = None, reason: str = None) -> bool:
    """Add a camera to the ignore list."""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO ignored_cameras (hardware_id, hardware_name, reason)
                VALUES (?, ?, ?)
            """, (hardware_id, hardware_name, reason))
            conn.commit()
            logger.info(f"Added camera to ignore list: {hardware_id}")
            return True
        except sqlite3.IntegrityError:
            # Already ignored
            return True


def unignore_camera(hardware_id: str) -> bool:
    """Remove a camera from the ignore list."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM ignored_cameras WHERE hardware_id = ?",
            (hardware_id,)
        )
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

    Returns: (success, hardware_id) - hardware_id for optional ignore list
    """
    camera = get_camera_by_id(camera_id)
    if not camera:
        return False, None

    hardware_id = camera.get('hardware_id')

    with get_connection() as conn:
        cursor = conn.cursor()
        # Delete camera (cascades to settings and capabilities)
        cursor.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
        conn.commit()

        if cursor.rowcount > 0:
            logger.info(f"Deleted camera {camera_id} ({hardware_id})")
            return True, hardware_id
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
