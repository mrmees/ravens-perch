"""
Ravens Perch - Print Status Monitoring

Polls Moonraker for print status and manages overlay text files
and dynamic framerate switching.
"""
import logging
import os
import threading
import time
from typing import Dict, Optional, Callable
from dataclasses import dataclass, field
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


@dataclass
class PrintStatus:
    """Current print status from Moonraker."""
    state: str = "standby"  # standby, printing, paused, complete, error
    progress: float = 0.0   # 0-100
    filename: str = ""
    current_layer: int = 0
    total_layers: int = 0
    time_remaining: int = 0  # seconds
    time_elapsed: int = 0    # seconds

    @property
    def is_printing(self) -> bool:
        """Check if actively printing (includes paused)."""
        return self.state in ("printing", "paused")

    def format_time(self, seconds: int) -> str:
        """Format seconds as HH:MM:SS or MM:SS."""
        if seconds <= 0:
            return "--:--"
        hours, remainder = divmod(int(seconds), 3600)
        minutes, secs = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    def format_overlay_text(self) -> str:
        """Format status for FFmpeg overlay."""
        if not self.is_printing:
            if self.state == "complete":
                return "Complete"
            return "On Standby"

        lines = []

        # Progress - always show
        lines.append(f"{self.progress:.1f}%")

        # Layer info
        if self.total_layers > 0:
            lines.append(f"Layer {self.current_layer}/{self.total_layers}")

        # Time remaining
        if self.time_remaining > 0:
            lines.append(f"ETA {self.format_time(self.time_remaining)}")

        result = "  ".join(lines)  # Use spaces instead of | which can cause FFmpeg issues
        # Ensure we never return empty string
        if not result:
            result = "Printing..."
        # Escape special characters for FFmpeg drawtext filter
        # % is interpreted as strftime format, : can cause issues in some contexts
        result = result.replace('%', '%%')
        result = result.replace(':', '\\:')
        return result


class PrintStatusMonitor:
    """
    Monitors Moonraker for print status updates.

    Manages overlay text files and triggers framerate changes.
    """

    def __init__(
        self,
        moonraker_url: str = "http://localhost:7125",
        data_dir: str = None,
        printing_poll_interval: float = 10.0,
        standby_poll_interval: float = 30.0,
        standby_delay: float = 30.0
    ):
        self.moonraker_url = moonraker_url.rstrip('/')
        self.data_dir = Path(data_dir) if data_dir else Path.home() / ".ravens-perch"
        self.overlay_dir = self.data_dir / "overlays"
        self.printing_poll_interval = printing_poll_interval
        self.standby_poll_interval = standby_poll_interval
        self.standby_delay = standby_delay

        self._status = PrintStatus()
        self._previous_state = "standby"
        self._state_change_time: Optional[float] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Callbacks for state changes
        self._on_state_change: Optional[Callable[[str, str], None]] = None

        # Per-camera overlay enabled tracking
        self._camera_overlays: Dict[str, bool] = {}

        # Ensure overlay directory exists
        self.overlay_dir.mkdir(parents=True, exist_ok=True)

    @property
    def status(self) -> PrintStatus:
        """Get current print status (thread-safe)."""
        with self._lock:
            return self._status

    def set_state_change_callback(self, callback: Callable[[str, str], None]):
        """
        Set callback for print state changes.

        Callback receives (old_state, new_state) where state is
        'printing' or 'standby'.
        """
        self._on_state_change = callback

    def set_camera_overlay(self, camera_id: str, enabled: bool):
        """Enable/disable overlay for a specific camera."""
        self._camera_overlays[camera_id] = enabled
        overlay_path = self.get_overlay_path(camera_id)
        logger.info(f"Camera {camera_id} overlay {'enabled' if enabled else 'disabled'}, path: {overlay_path}")
        if enabled:
            # Create initial overlay file
            self._update_overlay_file(camera_id)
        else:
            # Clear overlay file
            self._clear_overlay_file(camera_id)

    def get_overlay_path(self, camera_id: str) -> Path:
        """Get the overlay text file path for a camera."""
        return self.overlay_dir / f"camera_{camera_id}.txt"

    def start(self):
        """Start the status monitoring thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("Print status monitor started")

    def stop(self):
        """Stop the status monitoring thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Print status monitor stopped")

    def _monitor_loop(self):
        """Main monitoring loop with dynamic polling interval."""
        while self._running:
            try:
                self._poll_status()
                self._check_state_change()
                self._update_all_overlays()
            except Exception as e:
                logger.error(f"Error in print status monitor: {e}")

            # Use shorter interval when printing, longer when idle
            with self._lock:
                is_printing = self._status.is_printing

            if is_printing:
                time.sleep(self.printing_poll_interval)
            else:
                time.sleep(self.standby_poll_interval)

    def _poll_status(self):
        """Poll Moonraker for current print status."""
        try:
            # Query print stats and display status
            # Use URL directly since params with None values don't work correctly
            response = requests.get(
                f"{self.moonraker_url}/printer/objects/query?print_stats&display_status&virtual_sdcard",
                timeout=5
            )

            if response.status_code != 200:
                logger.debug(f"Moonraker returned status {response.status_code}")
                return

            data = response.json().get("result", {}).get("status", {})
            logger.debug(f"Moonraker print_stats: {data.get('print_stats', {})}")

            print_stats = data.get("print_stats", {})
            display_status = data.get("display_status", {})
            virtual_sdcard = data.get("virtual_sdcard", {})

            with self._lock:
                # State
                state = print_stats.get("state", "standby")
                old_state = self._status.state
                if state == "printing":
                    self._status.state = "printing"
                elif state == "paused":
                    self._status.state = "paused"
                elif state == "complete":
                    self._status.state = "complete"
                elif state in ("error", "cancelled"):
                    self._status.state = "error"
                else:
                    self._status.state = "standby"

                if old_state != self._status.state:
                    logger.info(f"Print status changed: {old_state} -> {self._status.state}")

                # Progress
                progress = virtual_sdcard.get("progress", 0) * 100
                self._status.progress = min(100.0, max(0.0, progress))

                # Filename
                self._status.filename = print_stats.get("filename", "")

                # Time
                self._status.time_elapsed = int(print_stats.get("print_duration", 0))

                # Try to get layer info from display_status (set by SET_DISPLAY_TEXT macro)
                # or from file metadata
                self._status.current_layer = 0
                self._status.total_layers = 0

                # Check for layer info in display message
                message = display_status.get("message", "")
                if "Layer" in message:
                    # Try to parse "Layer X/Y" format
                    try:
                        import re
                        match = re.search(r"Layer\s+(\d+)\s*/\s*(\d+)", message, re.IGNORECASE)
                        if match:
                            self._status.current_layer = int(match.group(1))
                            self._status.total_layers = int(match.group(2))
                    except:
                        pass

                # Calculate time remaining based on progress and elapsed time
                if self._status.progress > 0 and self._status.time_elapsed > 0:
                    total_estimated = self._status.time_elapsed / (self._status.progress / 100)
                    self._status.time_remaining = int(total_estimated - self._status.time_elapsed)
                else:
                    self._status.time_remaining = 0

        except requests.RequestException as e:
            logger.debug(f"Failed to poll Moonraker: {e}")
        except Exception as e:
            logger.error(f"Error parsing Moonraker response: {e}")

    def _check_state_change(self):
        """Check for printing/standby state changes and trigger callbacks."""
        with self._lock:
            current_printing = self._status.is_printing

        previous_printing = self._previous_state == "printing"

        if current_printing and not previous_printing:
            # Switched to printing - immediate callback
            self._state_change_time = None
            self._previous_state = "printing"
            if self._on_state_change:
                logger.info("Print started - switching to printing framerate")
                self._on_state_change("standby", "printing")

        elif not current_printing and previous_printing:
            # Switched to standby - start delay timer
            if self._state_change_time is None:
                self._state_change_time = time.time()
                logger.info(f"Print ended - waiting {self.standby_delay}s before switching to standby")
            elif time.time() - self._state_change_time >= self.standby_delay:
                # Delay elapsed, trigger callback
                self._state_change_time = None
                self._previous_state = "standby"
                if self._on_state_change:
                    logger.info("Switching to standby framerate")
                    self._on_state_change("printing", "standby")

    def _update_all_overlays(self):
        """Update overlay files for all cameras with overlay enabled."""
        for camera_id, enabled in self._camera_overlays.items():
            if enabled:
                self._update_overlay_file(camera_id)

    def _update_overlay_file(self, camera_id: str):
        """Update the overlay text file for a camera."""
        overlay_path = self.get_overlay_path(camera_id)

        with self._lock:
            text = self._status.format_overlay_text()

        try:
            overlay_path.write_text(text, encoding='utf-8')
            logger.info(f"Wrote overlay for camera {camera_id}: '{text}' to {overlay_path}")
        except Exception as e:
            logger.error(f"Failed to write overlay file for camera {camera_id}: {e}")

    def _clear_overlay_file(self, camera_id: str):
        """Clear the overlay text file for a camera."""
        overlay_path = self.get_overlay_path(camera_id)
        try:
            overlay_path.write_text("")
        except Exception as e:
            logger.debug(f"Failed to clear overlay file: {e}")


# Global monitor instance
_monitor: Optional[PrintStatusMonitor] = None


def get_monitor() -> Optional[PrintStatusMonitor]:
    """Get the global print status monitor instance."""
    return _monitor


def init_monitor(
    moonraker_url: str,
    data_dir: str = None,
    printing_poll_interval: float = 10.0,
    standby_poll_interval: float = 30.0,
    standby_delay: float = 30.0
) -> PrintStatusMonitor:
    """Initialize the global print status monitor."""
    global _monitor
    _monitor = PrintStatusMonitor(
        moonraker_url=moonraker_url,
        data_dir=data_dir,
        printing_poll_interval=printing_poll_interval,
        standby_poll_interval=standby_poll_interval,
        standby_delay=standby_delay
    )
    return _monitor


def stop_monitor():
    """Stop the global print status monitor."""
    global _monitor
    if _monitor:
        _monitor.stop()
        _monitor = None
