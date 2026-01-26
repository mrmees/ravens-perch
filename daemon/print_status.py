"""
Ravens Perch - Print Status Monitoring

Polls Moonraker for print status and manages overlay text files
and dynamic framerate switching.
"""
import logging
import os
import threading
import time
from datetime import datetime
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
    hotend_temp: float = 0.0
    hotend_target: float = 0.0
    bed_temp: float = 0.0
    bed_target: float = 0.0
    fan_speed: float = 0.0   # 0-100 percent
    filament_used: float = 0.0  # mm
    print_speed: float = 100.0  # percent (speed factor * 100)
    z_height: float = 0.0  # mm
    live_velocity: float = 0.0  # mm/s - current print head speed
    flow_rate: float = 0.0  # mm/s - current extruder velocity
    filament_type: str = ""  # filament type from print file metadata

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

        # Per-camera overlay settings {camera_id: settings_dict}
        self._camera_overlays: Dict[str, Dict] = {}

        # Ensure overlay directory exists
        self.overlay_dir.mkdir(parents=True, exist_ok=True)

    @property
    def status(self) -> PrintStatus:
        """Get current print status (thread-safe)."""
        with self._lock:
            return self._status

    @property
    def effective_state(self) -> str:
        """Get the current effective state for framerate switching.

        Returns 'printing' or 'standby'. This reflects the state after
        any delay timers have been applied.
        """
        return self._previous_state

    def set_state_change_callback(self, callback: Callable[[str, str], None]):
        """
        Set callback for print state changes.

        Callback receives (old_state, new_state) where state is
        'printing' or 'standby'.
        """
        self._on_state_change = callback

    def set_poll_interval(self, interval: float):
        """Set the polling interval for when printing (1-10 seconds)."""
        interval = max(1.0, min(10.0, interval))
        self.printing_poll_interval = interval
        logger.info(f"Overlay update interval set to {interval} seconds")

    def set_camera_overlay(self, camera_id: str, enabled: bool, settings: Dict = None):
        """Enable/disable overlay for a specific camera."""
        overlay_path = self.get_overlay_path(camera_id)
        if enabled:
            self._camera_overlays[camera_id] = settings or {}
            logger.info(f"Camera {camera_id} overlay enabled, path: {overlay_path}")
            # Create initial overlay file
            self._update_overlay_file(camera_id)
        else:
            self._camera_overlays.pop(camera_id, None)
            logger.info(f"Camera {camera_id} overlay disabled")
            # Clear overlay file
            self._clear_overlay_file(camera_id)

    def update_camera_overlay_settings(self, camera_id: str, settings: Dict):
        """Update overlay settings for a camera."""
        if camera_id in self._camera_overlays:
            self._camera_overlays[camera_id] = settings

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
        filename_changed = False
        new_filename = ""
        try:
            # Query all needed objects
            # print_stats, display_status, virtual_sdcard for basic info
            # extruder, heater_bed for temps
            # fan for fan speed
            # gcode_move for speed and z position
            response = requests.get(
                f"{self.moonraker_url}/printer/objects/query"
                "?print_stats&display_status&virtual_sdcard"
                "&extruder&heater_bed&fan&gcode_move&motion_report",
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
            extruder = data.get("extruder", {})
            heater_bed = data.get("heater_bed", {})
            fan = data.get("fan", {})
            gcode_move = data.get("gcode_move", {})
            motion_report = data.get("motion_report", {})

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
                new_filename = print_stats.get("filename", "")
                old_filename = self._status.filename
                self._status.filename = new_filename
                filename_changed = new_filename and new_filename != old_filename

                # Time
                self._status.time_elapsed = int(print_stats.get("print_duration", 0))

                # Filament used (in mm)
                self._status.filament_used = print_stats.get("filament_used", 0)

                # Temperatures
                self._status.hotend_temp = extruder.get("temperature", 0)
                self._status.hotend_target = extruder.get("target", 0)
                self._status.bed_temp = heater_bed.get("temperature", 0)
                self._status.bed_target = heater_bed.get("target", 0)

                # Fan speed (0-1 to 0-100%)
                self._status.fan_speed = fan.get("speed", 0) * 100

                # Print speed (speed_factor is a multiplier, e.g., 1.0 = 100%)
                self._status.print_speed = gcode_move.get("speed_factor", 1.0) * 100

                # Z height from gcode position
                gcode_position = gcode_move.get("gcode_position", [0, 0, 0, 0])
                if len(gcode_position) >= 3:
                    self._status.z_height = gcode_position[2]

                # Live velocity from motion_report (mm/s)
                self._status.live_velocity = motion_report.get("live_velocity", 0.0)
                self._status.flow_rate = motion_report.get("live_extruder_velocity", 0.0)

                # Try to get layer info from display_status (set by SET_DISPLAY_TEXT macro)
                self._status.current_layer = 0
                self._status.total_layers = 0

                # Check for layer info in display message
                message = display_status.get("message", "")
                if "Layer" in message:
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

            # Fetch filament type outside the lock (HTTP request can be slow)
            if filename_changed:
                self._fetch_filament_type(new_filename)

        except requests.RequestException as e:
            logger.debug(f"Failed to poll Moonraker: {e}")
        except Exception as e:
            logger.error(f"Error parsing Moonraker response: {e}")

    def _fetch_filament_type(self, filename: str):
        """Fetch filament type from print file metadata."""
        try:
            response = requests.get(
                f"{self.moonraker_url}/server/files/metadata",
                params={"filename": filename},
                timeout=5
            )
            if response.status_code == 200:
                data = response.json().get("result", {})
                # Filament type is often in filament_type or slicer metadata
                filament_type = data.get("filament_type", "")
                if isinstance(filament_type, list) and filament_type:
                    filament_type = filament_type[0]  # Take first if list
                with self._lock:
                    self._status.filament_type = filament_type or ""
                logger.debug(f"Fetched filament type: {filament_type}")
        except Exception as e:
            logger.debug(f"Failed to fetch filament type: {e}")

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
        for camera_id, settings in self._camera_overlays.items():
            self._update_overlay_file(camera_id)

    def _format_overlay_text(self, settings: Dict) -> str:
        """Format overlay text based on camera settings."""
        status = self._status
        show_labels = settings.get('overlay_show_labels', True)
        multiline = settings.get('overlay_multiline', False)

        if not status.is_printing:
            if status.state == "complete":
                return "Complete"
            return settings.get('overlay_standby_text') or "On Standby"

        parts = []

        # Progress
        if settings.get('overlay_show_progress', True):
            if show_labels:
                parts.append(f"Progress: {status.progress:.1f}%")
            else:
                parts.append(f"{status.progress:.1f}%")

        # Layer
        if settings.get('overlay_show_layer', True) and status.total_layers > 0:
            if show_labels:
                parts.append(f"Layer: {status.current_layer}/{status.total_layers}")
            else:
                parts.append(f"{status.current_layer}/{status.total_layers}")

        # ETA (time remaining)
        if settings.get('overlay_show_eta', True) and status.time_remaining > 0:
            if show_labels:
                parts.append(f"ETA: {status.format_time(status.time_remaining)}")
            else:
                parts.append(status.format_time(status.time_remaining))

        # Elapsed time
        if settings.get('overlay_show_elapsed', False) and status.time_elapsed > 0:
            if show_labels:
                parts.append(f"Elapsed: {status.format_time(status.time_elapsed)}")
            else:
                parts.append(status.format_time(status.time_elapsed))

        # Filename
        if settings.get('overlay_show_filename', False) and status.filename:
            # Truncate long filenames
            fname = status.filename
            if len(fname) > 20:
                fname = fname[:17] + "..."
            if show_labels:
                parts.append(f"File: {fname}")
            else:
                parts.append(fname)

        # Hotend temp
        if settings.get('overlay_show_hotend_temp', False):
            if show_labels:
                parts.append(f"Hotend: {status.hotend_temp:.0f}/{status.hotend_target:.0f}C")
            else:
                parts.append(f"{status.hotend_temp:.0f}/{status.hotend_target:.0f}C")

        # Bed temp
        if settings.get('overlay_show_bed_temp', False):
            if show_labels:
                parts.append(f"Bed: {status.bed_temp:.0f}/{status.bed_target:.0f}C")
            else:
                parts.append(f"{status.bed_temp:.0f}/{status.bed_target:.0f}C")

        # Fan speed
        if settings.get('overlay_show_fan_speed', False):
            if show_labels:
                parts.append(f"Fan: {status.fan_speed:.0f}%")
            else:
                parts.append(f"{status.fan_speed:.0f}%")

        # Print state
        if settings.get('overlay_show_print_state', False):
            state_display = status.state.capitalize()
            if show_labels:
                parts.append(f"State: {state_display}")
            else:
                parts.append(state_display)

        # Filament used
        if settings.get('overlay_show_filament_used', False) and status.filament_used > 0:
            # Convert mm to meters if large
            if status.filament_used >= 1000:
                filament_str = f"{status.filament_used/1000:.2f}m"
            else:
                filament_str = f"{status.filament_used:.0f}mm"
            if show_labels:
                parts.append(f"Filament: {filament_str}")
            else:
                parts.append(filament_str)

        # Current time
        if settings.get('overlay_show_current_time', False):
            current_time = datetime.now().strftime("%H:%M:%S")
            if show_labels:
                parts.append(f"Time: {current_time}")
            else:
                parts.append(current_time)

        # Print speed
        if settings.get('overlay_show_print_speed', False):
            if show_labels:
                parts.append(f"Speed: {status.print_speed:.0f}%")
            else:
                parts.append(f"{status.print_speed:.0f}%")

        # Z height
        if settings.get('overlay_show_z_height', False):
            if show_labels:
                parts.append(f"Z: {status.z_height:.2f}mm")
            else:
                parts.append(f"{status.z_height:.2f}mm")

        # Live velocity (print head speed)
        if settings.get('overlay_show_live_velocity', False):
            if show_labels:
                parts.append(f"Velocity: {status.live_velocity:.1f}mm/s")
            else:
                parts.append(f"{status.live_velocity:.1f}mm/s")

        # Flow rate (extruder velocity)
        if settings.get('overlay_show_flow_rate', False):
            if show_labels:
                parts.append(f"Flow: {status.flow_rate:.2f}mm/s")
            else:
                parts.append(f"{status.flow_rate:.2f}mm/s")

        # Filament type
        if settings.get('overlay_show_filament_type', False) and status.filament_type:
            if show_labels:
                parts.append(f"Filament: {status.filament_type}")
            else:
                parts.append(status.filament_type)

        if not parts:
            return "Printing..."

        # Join with newline for multiline, spaces for single line
        separator = "\n" if multiline else "  "
        return separator.join(parts)

    def _update_overlay_file(self, camera_id: str):
        """Update the overlay text file for a camera."""
        overlay_path = self.get_overlay_path(camera_id)
        settings = self._camera_overlays.get(camera_id, {})

        with self._lock:
            text = self._format_overlay_text(settings)

        try:
            overlay_path.write_text(text, encoding='utf-8')
            logger.debug(f"Wrote overlay for camera {camera_id}: '{text}' to {overlay_path}")
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
