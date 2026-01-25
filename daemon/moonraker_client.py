"""
Ravens Perch - Moonraker API Client
"""
import socket
import logging
from typing import Optional, Dict, List, Tuple, Any

import requests

from .config import (
    MOONRAKER_DEFAULT_URL, MOONRAKER_FALLBACK_URLS,
    MEDIAMTX_WEBRTC_PORT, WEB_UI_PORT
)

logger = logging.getLogger(__name__)


class MoonrakerClient:
    """Client for Moonraker API."""

    def __init__(self, url: Optional[str] = None):
        self.base_url = url or MOONRAKER_DEFAULT_URL
        self.session = requests.Session()
        self._webcam_endpoint = "/server/webcams"

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        timeout: int = 5
    ) -> Tuple[bool, Optional[Dict], Optional[str]]:
        """
        Make an API request to Moonraker.

        Returns: (success, data, error_message)
        """
        url = f"{self.base_url.rstrip('/')}{endpoint}"

        try:
            if method == "GET":
                response = self.session.get(url, params=params, timeout=timeout)
            elif method == "POST":
                response = self.session.post(url, json=data, params=params, timeout=timeout)
            elif method == "DELETE":
                response = self.session.delete(url, params=params, timeout=timeout)
            else:
                return False, None, f"Unsupported method: {method}"

            if response.status_code == 200:
                try:
                    result = response.json()
                    return True, result.get('result', result), None
                except ValueError:
                    return True, {}, None
            else:
                return False, None, f"HTTP {response.status_code}: {response.text}"

        except requests.Timeout:
            return False, None, "Request timeout"
        except requests.ConnectionError:
            return False, None, "Connection failed"
        except Exception as e:
            return False, None, str(e)

    def is_available(self) -> bool:
        """Check if Moonraker is responding."""
        success, _, _ = self._request("/server/info", timeout=2)
        return success

    def check_auth_required(self) -> bool:
        """Check if Moonraker requires authentication."""
        success, data, error = self._request("/access/info", timeout=2)
        if success and data:
            return data.get('requires_auth', False)
        return False


# Global client instance
_client: Optional[MoonrakerClient] = None


def get_client() -> MoonrakerClient:
    """Get or create the Moonraker client."""
    global _client
    if _client is None:
        _client = MoonrakerClient()
    return _client


def set_url(url: str):
    """Set the Moonraker URL."""
    global _client
    _client = MoonrakerClient(url)


def detect_moonraker_url() -> Optional[str]:
    """
    Try to detect Moonraker URL from common locations.

    Returns the URL if found, None otherwise.
    """
    urls_to_try = [MOONRAKER_DEFAULT_URL] + MOONRAKER_FALLBACK_URLS

    for url in urls_to_try:
        try:
            client = MoonrakerClient(url)
            if client.is_available():
                logger.info(f"Detected Moonraker at: {url}")
                global _client
                _client = client
                return url
        except Exception:
            continue

    logger.warning("Could not detect Moonraker")
    return None


def is_available() -> bool:
    """Check if Moonraker is responding."""
    return get_client().is_available()


# ============ Webcam API ============

def get_ravens_camera_by_name(webcam_name: str) -> Optional[Dict]:
    """Find an existing Ravens Perch webcam by name."""
    webcams = list_cameras()
    for webcam in webcams:
        if webcam.get('name') == webcam_name:
            return webcam
    return None


def register_camera(
    camera_id: str,
    friendly_name: str,
    stream_url: str,
    snapshot_url: str,
    flip_horizontal: bool = False,
    flip_vertical: bool = False,
    rotation: int = 0
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Register a camera with Moonraker.

    If a camera with the same name already exists, it will be updated.
    Returns: (success, moonraker_uid, error_message)
    """
    client = get_client()

    # Create webcam name from friendly name
    webcam_name = friendly_name.replace(' ', '_').lower()

    # Check if this camera already exists
    existing = get_ravens_camera_by_name(webcam_name)
    if existing:
        existing_uid = existing.get('uid')
        logger.info(f"Camera {webcam_name} already exists (uid: {existing_uid}), updating...")

        # Update existing camera
        success, error = update_camera(
            existing_uid,
            stream_url=stream_url,
            snapshot_url=snapshot_url,
            flip_horizontal=flip_horizontal,
            flip_vertical=flip_vertical,
            rotation=rotation,
            enabled=True
        )

        if success:
            return True, existing_uid, None
        else:
            # If update fails, try to delete and recreate
            logger.warning(f"Update failed, removing and re-registering: {error}")
            unregister_camera(existing_uid)

    data = {
        "name": webcam_name,
        "location": "printer",
        "service": "webrtc-mediamtx",
        "enabled": True,
        "icon": "mdiWebcam",
        "target_fps": 30,
        "target_fps_idle": 5,
        "stream_url": stream_url,
        "snapshot_url": snapshot_url,
        "flip_horizontal": flip_horizontal,
        "flip_vertical": flip_vertical,
        "rotation": rotation,
        "aspect_ratio": "16:9",
    }

    success, result, error = client._request(
        "/server/webcams/item",
        method="POST",
        data=data,
        timeout=10  # Longer timeout for webcam registration
    )

    if success and result:
        uid = result.get('webcam', {}).get('uid')
        logger.info(f"Registered camera with Moonraker: {webcam_name} (uid: {uid})")
        return True, uid, None
    else:
        logger.error(f"Failed to register camera {webcam_name}: {error}")
        return False, None, error


def update_camera(
    moonraker_uid: str,
    **updates
) -> Tuple[bool, Optional[str]]:
    """
    Update a camera in Moonraker.

    Returns: (success, error_message)
    """
    client = get_client()

    success, _, error = client._request(
        "/server/webcams/item",
        method="POST",
        params={"uid": moonraker_uid},
        data=updates
    )

    if success:
        logger.info(f"Updated camera in Moonraker: {moonraker_uid}")
    else:
        logger.error(f"Failed to update camera {moonraker_uid}: {error}")

    return success, error


def unregister_camera(moonraker_uid: str) -> Tuple[bool, Optional[str]]:
    """
    Unregister a camera from Moonraker.

    Returns: (success, error_message)
    """
    client = get_client()

    success, _, error = client._request(
        "/server/webcams/item",
        method="DELETE",
        params={"uid": moonraker_uid}
    )

    if success:
        logger.info(f"Unregistered camera from Moonraker: {moonraker_uid}")
    else:
        # Not an error if webcam doesn't exist
        if error and "not found" in error.lower():
            return True, None
        logger.error(f"Failed to unregister camera {moonraker_uid}: {error}")

    return success, error


def list_cameras() -> List[Dict]:
    """
    List all webcams registered in Moonraker.

    Returns list of webcam dicts.
    """
    client = get_client()

    success, data, error = client._request("/server/webcams/list")

    if success and data:
        webcams = data.get('webcams', [])
        return webcams
    else:
        logger.error(f"Failed to list cameras: {error}")
        return []


def get_camera_by_ravens_id(camera_id: str) -> Optional[Dict]:
    """Find a Moonraker webcam by Ravens Perch camera ID."""
    webcams = list_cameras()

    for webcam in webcams:
        extra_data = webcam.get('extra_data', {})
        if extra_data.get('ravens_perch_id') == camera_id:
            return webcam

    return None




# ============ Console & Notifications ============

def print_to_console(message: str) -> bool:
    """
    Print a message to the Klipper console via Moonraker.
    Uses RESPOND TYPE=error which displays highlighted in Fluidd/Mainsail.
    """
    client = get_client()

    # Escape single quotes in message
    escaped_message = message.replace("'", "\\'")

    success, _, error = client._request(
        "/printer/gcode/script",
        method="POST",
        data={"script": f"RESPOND TYPE=error MSG='{escaped_message}'"}
    )

    if not success:
        logger.debug(f"Failed to print to console: {error}")

    return success


def announce_management_url() -> None:
    """
    Announce the Ravens Perch management URL via Moonraker notification.
    Called on service startup.
    """
    # Get hostname
    hostname = socket.getfqdn()
    if '.' not in hostname:
        hostname = f"{hostname}.local"

    # Get IP
    ip = get_system_ip()

    # Detect port from nginx config (check for fluidd or mainsail)
    port = "80"
    import os
    import re
    for nginx_dir in ['/etc/nginx/sites-enabled', '/etc/nginx/sites-available']:
        if not os.path.isdir(nginx_dir):
            continue
        for filename in os.listdir(nginx_dir):
            filepath = os.path.join(nginx_dir, filename)
            if os.path.isfile(filepath):
                try:
                    with open(filepath, 'r') as f:
                        content = f.read().lower()
                        if 'fluidd' in content or 'mainsail' in content:
                            match = re.search(r'listen\s+(\d+)', content)
                            if match:
                                port = match.group(1)
                                break
                except (IOError, PermissionError):
                    continue

    # Build URLs
    if port == "80":
        url_hostname = f"http://{hostname}/cameras/"
        url_ip = f"http://{ip}/cameras/"
    else:
        url_hostname = f"http://{hostname}:{port}/cameras/"
        url_ip = f"http://{ip}:{port}/cameras/"

    # Print to console using M118
    print_to_console("Ravens Perch camera manager available at:")
    print_to_console(f"  {url_hostname}")
    print_to_console(f"  {url_ip}")


def send_notification(title: str, message: str, level: str = "info") -> bool:
    """
    Send a notification through Moonraker.

    Levels: info, warning, error
    """
    client = get_client()

    # Use server announcement API
    data = {
        "entry": {
            "title": title,
            "description": message,
            "priority": level,
        }
    }

    success, _, error = client._request(
        "/server/announcements/add",
        method="POST",
        data=data
    )

    if not success:
        logger.debug(f"Failed to send notification: {error}")

    return success


# ============ URL Construction ============

def get_system_ip() -> str:
    """Get the system's LAN IP address."""
    try:
        # Create a socket to determine the local IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def build_stream_url(camera_id: str, host: Optional[str] = None) -> str:
    """
    Build WebRTC stream URL for a camera.

    Uses the system IP if host is not provided.
    """
    if host is None:
        host = get_system_ip()

    path_name = camera_id.replace(' ', '_').lower()
    return f"http://{host}:{MEDIAMTX_WEBRTC_PORT}/{path_name}/"


def build_snapshot_url(camera_id: str, host: Optional[str] = None) -> str:
    """
    Build snapshot URL for a camera.

    Uses the system IP if host is not provided.
    Connects directly to the Flask app port to avoid nginx dependency.
    """
    if host is None:
        host = get_system_ip()

    return f"http://{host}:{WEB_UI_PORT}/cameras/snapshot/{camera_id}.jpg"


# ============ Server Info ============

def get_server_info() -> Optional[Dict]:
    """Get Moonraker server info."""
    client = get_client()
    success, data, _ = client._request("/server/info")
    return data if success else None


def get_printer_info() -> Optional[Dict]:
    """Get printer info from Moonraker."""
    client = get_client()
    success, data, _ = client._request("/printer/info")
    return data if success else None


# ============ Theme Detection ============

def detect_klipper_ui_theme(moonraker_url: Optional[str] = None) -> Dict[str, Optional[str]]:
    """
    Query Moonraker for Mainsail/Fluidd theme colors.

    Returns: {'mainsail': '#color' or None, 'fluidd': '#color' or None}
    """
    result: Dict[str, Optional[str]] = {'mainsail': None, 'fluidd': None}

    if moonraker_url:
        client = MoonrakerClient(moonraker_url)
    else:
        client = get_client()

    # Query Mainsail uiSettings
    try:
        success, data, _ = client._request(
            "/server/database/item",
            params={'namespace': 'mainsail', 'key': 'uiSettings'},
            timeout=5
        )
        if success and data:
            value = data.get('value', {})
            if isinstance(value, dict):
                result['mainsail'] = value.get('primary')
    except Exception as e:
        logger.debug(f"Failed to query Mainsail theme: {e}")

    # Query Fluidd uiSettings
    try:
        success, data, _ = client._request(
            "/server/database/item",
            params={'namespace': 'fluidd', 'key': 'uiSettings'},
            timeout=5
        )
        if success and data:
            value = data.get('value', {})
            if isinstance(value, dict):
                theme = value.get('theme', {})
                if isinstance(theme, dict):
                    result['fluidd'] = theme.get('color')
    except Exception as e:
        logger.debug(f"Failed to query Fluidd theme: {e}")

    return result
