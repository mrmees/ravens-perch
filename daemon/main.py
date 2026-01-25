"""
Ravens Perch - Main Daemon Entry Point

This module orchestrates all components:
- Database initialization
- Hardware encoder detection
- MediaMTX availability check
- Moonraker detection
- Camera monitoring
- Web UI server
"""
import os
import sys
import signal
import logging
import threading
from pathlib import Path

from .config import (
    BASE_DIR, LOG_DIR, LOG_LEVEL,
    WEB_UI_HOST, WEB_UI_PORT
)
from .db import init_db, add_log, get_all_cameras, update_camera
from .hardware import (
    detect_encoders, check_ffmpeg_available,
    check_v4l2_utils_available, get_platform_info,
    init_encoder_cache
)
from .camera_manager import (
    CameraMonitor, DeviceInfo, probe_capabilities,
    auto_configure, add_rejected_camera, remove_rejected_camera
)
from .stream_manager import (
    wait_for_available as wait_for_mediamtx,
    build_ffmpeg_command, add_or_update_stream, remove_stream,
    remove_all_streams, start_camera_stream
)
from .moonraker_client import (
    detect_moonraker_url, register_camera, unregister_camera,
    build_stream_url, build_snapshot_url, get_system_ip
)
from .print_status import init_monitor, get_monitor, stop_monitor
from . import db

# Configure logging
def setup_logging():
    """Configure logging for the daemon."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "ravens-perch.log"

    # Create formatters
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_formatter = logging.Formatter(
        '%(levelname)s: %(message)s'
    )

    # File handler with rotation
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Reduce noise from libraries
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)

    return logging.getLogger(__name__)


logger = setup_logging()


class RavensPerchDaemon:
    """Main daemon class that orchestrates all components."""

    def __init__(self):
        self.camera_monitor = None
        self.web_thread = None
        self.running = False
        self.encoders = {}
        self.moonraker_url = None
        self.print_monitor = None

    def start(self):
        """Start the daemon and all components."""
        logger.info("=" * 50)
        logger.info("Ravens Perch starting...")
        logger.info("=" * 50)

        self.running = True

        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        try:
            # Step 1: Initialize database
            logger.info("Initializing database...")
            init_db()
            add_log("INFO", "Ravens Perch starting")

            # Initialize encoder cache path
            init_encoder_cache(str(BASE_DIR / "data"))

            # Step 2: Start web UI early so users can access the page during init
            logger.info(f"Starting web UI on {WEB_UI_HOST}:{WEB_UI_PORT}...")
            self._start_web_ui()

            # Step 3: Check dependencies
            self._check_dependencies()

            # Step 4: Detect encoders and wait for MediaMTX in parallel
            import concurrent.futures

            def detect_encoders_task():
                encoders = detect_encoders()
                encoder_list = [k for k, v in encoders.items() if v]
                logger.info(f"Available encoders: {encoder_list}")
                add_log("INFO", f"Available encoders: {encoder_list}")
                return encoders

            def wait_mediamtx_task():
                logger.info("Waiting for MediaMTX...")
                available = wait_for_mediamtx(timeout=30)
                if not available:
                    logger.warning("MediaMTX not available - streams will not work")
                    add_log("WARNING", "MediaMTX not available")
                else:
                    logger.info("MediaMTX is available")
                return available

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                encoder_future = executor.submit(detect_encoders_task)
                mediamtx_future = executor.submit(wait_mediamtx_task)

                self.encoders = encoder_future.result()
                mediamtx_available = mediamtx_future.result()

            # Step 5: Clean up stale MediaMTX streams (if available)
            if mediamtx_available:
                logger.info("Cleaning up stale MediaMTX streams...")
                removed = remove_all_streams()
                if removed > 0:
                    logger.info(f"Removed {removed} stale stream(s)")

            # Step 6: Detect Moonraker
            logger.info("Detecting Moonraker...")
            self.moonraker_url = detect_moonraker_url()
            if self.moonraker_url:
                logger.info(f"Moonraker found at: {self.moonraker_url}")
                add_log("INFO", f"Moonraker found at: {self.moonraker_url}")

                # Step 6b: Clean up stale Moonraker webcam registrations
                logger.info("Cleaning up stale Moonraker webcam registrations...")
                cleaned = 0
                for camera in db.get_all_cameras():
                    if camera.get('moonraker_uid'):
                        unregister_camera(camera['moonraker_uid'])
                        db.update_camera(camera['id'], moonraker_uid=None)
                        cleaned += 1
                if cleaned > 0:
                    logger.info(f"Removed {cleaned} stale webcam registration(s)")
            else:
                logger.warning("Moonraker not found - webcam registration disabled")
                add_log("WARNING", "Moonraker not found")

            # Step 6c: Initialize print status monitor (if Moonraker available)
            if self.moonraker_url:
                logger.info("Initializing print status monitor...")
                # Get overlay update interval from settings (default 5 seconds)
                overlay_interval = db.get_setting('overlay_update_interval', 5)
                self.print_monitor = init_monitor(
                    moonraker_url=self.moonraker_url,
                    data_dir=str(BASE_DIR),
                    printing_poll_interval=float(overlay_interval),
                    standby_poll_interval=30.0,
                    standby_delay=30.0
                )
                self.print_monitor.set_state_change_callback(self._on_print_state_change)
                self.print_monitor.start()
                logger.info(f"Print status monitor started (update interval: {overlay_interval}s)")

            # Step 7: Mark all cameras as disconnected initially
            self._reset_camera_states()

            # Step 8: Start camera monitor
            logger.info("Starting camera monitor...")
            self.camera_monitor = CameraMonitor(
                on_connect=self._on_camera_connected,
                on_disconnect=self._on_camera_disconnected
            )
            self.camera_monitor.start()

            # Step 9: Scan for existing cameras
            logger.info("Scanning for existing cameras...")
            self.camera_monitor.scan_existing()

            logger.info("Ravens Perch is running")
            add_log("INFO", "Ravens Perch started successfully")

            # Announce management URL to Klipper console (if Moonraker available)
            if self.moonraker_url:
                from .moonraker_client import announce_management_url
                announce_management_url()

            # Keep main thread alive
            while self.running:
                signal.pause() if hasattr(signal, 'pause') else threading.Event().wait(1)

        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            add_log("ERROR", f"Fatal error: {e}")
            self.stop()
            sys.exit(1)

    def stop(self):
        """Stop the daemon gracefully."""
        logger.info("Shutting down Ravens Perch...")
        self.running = False

        # Stop print status monitor
        if self.print_monitor:
            self.print_monitor.stop()

        # Stop camera monitor
        if self.camera_monitor:
            self.camera_monitor.stop()

        add_log("INFO", "Ravens Perch stopped")
        logger.info("Ravens Perch stopped")

    def _signal_handler(self, signum, frame):
        """Handle termination signals."""
        logger.info(f"Received signal {signum}")
        self.stop()
        sys.exit(0)

    def _check_dependencies(self):
        """Check that required dependencies are available."""
        platform_info = get_platform_info()
        logger.info(f"Platform: {platform_info.get('platform')} ({platform_info.get('machine')})")

        if not check_ffmpeg_available():
            logger.error("FFmpeg is not available - please install it")
            add_log("ERROR", "FFmpeg not found")
            raise RuntimeError("FFmpeg is required but not found")

        if not check_v4l2_utils_available():
            logger.warning("v4l2-utils not available - some features may not work")
            add_log("WARNING", "v4l2-utils not found")

    def _reset_camera_states(self):
        """Mark all cameras as disconnected on startup."""
        cameras = get_all_cameras()
        for camera in cameras:
            if camera['connected']:
                update_camera(camera['id'], connected=False, device_path=None)

    def _start_web_ui(self):
        """Start the web UI in a background thread."""
        try:
            from .web_ui.app import create_app
            app = create_app()
        except Exception as e:
            logger.error(f"Failed to create Flask app: {e}", exc_info=True)
            add_log("ERROR", f"Web UI failed to initialize: {e}")
            return

        def run_server():
            try:
                logger.info(f"Web UI server starting on {WEB_UI_HOST}:{WEB_UI_PORT}")
                # Use werkzeug server for development
                # In production, use gunicorn or similar
                app.run(
                    host=WEB_UI_HOST,
                    port=WEB_UI_PORT,
                    debug=False,
                    use_reloader=False,
                    threaded=True
                )
            except Exception as e:
                logger.error(f"Web UI server error: {e}", exc_info=True)
                add_log("ERROR", f"Web UI server failed: {e}")

        self.web_thread = threading.Thread(target=run_server, daemon=True)
        self.web_thread.start()
        logger.info("Web UI thread started")

    def _on_camera_connected(self, device_info: DeviceInfo):
        """Handle camera connection event."""
        logger.info(f"Camera connected: {device_info.hardware_name} at {device_info.path}")

        try:
            # Check if camera is on the ignore list
            if db.is_camera_ignored(device_info.hardware_id):
                logger.info(f"Camera {device_info.hardware_name} is ignored, skipping")
                return

            # Check if camera exists in database
            camera = db.get_camera_by_hardware_id(device_info.hardware_id)

            if camera:
                # Check if this camera is already connected (duplicate hardware_id)
                if camera['connected'] and camera['device_path'] != device_info.path:
                    # This is a duplicate camera with no unique serial number
                    logger.warning(
                        f"Duplicate camera detected: {device_info.hardware_name} at {device_info.path}. "
                        f"Another camera with the same identifier is already connected at {camera['device_path']}. "
                        f"Cameras without unique serial numbers are not supported when multiple are connected."
                    )
                    add_log(
                        "WARNING",
                        f"Duplicate camera ignored: {device_info.hardware_name}. "
                        f"Cameras without unique serial numbers are not supported.",
                        camera['id']
                    )
                    # Track this rejected camera for display in the UI
                    add_rejected_camera(
                        device_path=device_info.path,
                        hardware_name=device_info.hardware_name,
                        hardware_id=device_info.hardware_id,
                        reason="Duplicate camera - no unique serial number",
                        existing_camera_id=camera['id']
                    )
                    return

                # Existing camera - update connection status
                camera_id = camera['id']
                db.mark_camera_connected(camera_id, device_info.path)
                logger.info(f"Reconnected known camera: {camera['friendly_name']}")
                add_log("INFO", f"Camera reconnected: {camera['friendly_name']}", camera_id)
            else:
                # New camera - probe capabilities and auto-configure
                logger.info(f"New camera detected, probing capabilities...")
                capabilities = probe_capabilities(device_info.path)

                # Count current cameras for quality adjustment
                current_count = len(get_all_cameras(connected_only=True))

                # Auto-configure settings
                settings = auto_configure(capabilities, current_count + 1)

                # Create camera record
                camera_id = db.create_camera(
                    hardware_name=device_info.hardware_name,
                    serial_number=device_info.serial_number,
                    device_path=device_info.path
                )

                # Save settings and capabilities
                db.save_camera_settings(camera_id, settings)
                db.save_camera_capabilities(camera_id, capabilities)

                logger.info(f"Created new camera record: ID {camera_id}")
                add_log("INFO", f"New camera detected: {device_info.hardware_name}", camera_id)

            # Get current camera data
            camera = db.get_camera_with_settings(camera_id)

            if not camera['enabled']:
                logger.info(f"Camera {camera['friendly_name']} is disabled, not starting stream")
                return

            # Build FFmpeg command and start stream
            settings = camera['settings'] or {}

            # Set up print status overlay if enabled
            if settings.get('overlay_enabled') and self.print_monitor:
                self.print_monitor.set_camera_overlay(str(camera_id), True, settings)

            # Apply standby framerate if enabled and printer is idle
            if self.print_monitor and settings.get('standby_enabled') and settings.get('standby_framerate'):
                if not self.print_monitor.status.is_printing:
                    settings['framerate'] = settings['standby_framerate']

            # Start stream (applies v4l2 controls, builds command, starts stream)
            success, error = start_camera_stream(
                device_info.path,
                str(camera_id),
                settings,
                self.print_monitor
            )
            if success:
                logger.info(f"Stream started for camera {camera_id}")
            else:
                logger.error(f"Failed to start stream: {error}")
                add_log("ERROR", f"Failed to start stream: {error}", camera_id)
                return

            # Register with Moonraker
            if self.moonraker_url:
                host = get_system_ip()
                stream_url = build_stream_url(str(camera_id), host)
                snapshot_url = build_snapshot_url(str(camera_id), host)

                rotation = settings.get('rotation', 0)
                success, moonraker_uid, error = register_camera(
                    str(camera_id),
                    camera['friendly_name'],
                    stream_url,
                    snapshot_url,
                    rotation=rotation
                )

                if success and moonraker_uid:
                    db.update_camera(camera_id, moonraker_uid=moonraker_uid)
                    logger.info(f"Registered camera with Moonraker: {moonraker_uid}")
                else:
                    logger.warning(f"Failed to register with Moonraker: {error}")

        except Exception as e:
            logger.error(f"Error handling camera connection: {e}", exc_info=True)
            add_log("ERROR", f"Error handling camera: {e}")

    def _on_camera_disconnected(self, device_path: str):
        """Handle camera disconnection event."""
        logger.info(f"Camera disconnected: {device_path}")

        # Always try to remove from rejected cameras list (in case it was rejected)
        remove_rejected_camera(device_path)

        try:
            # Find camera by device path
            camera = db.get_camera_by_device_path(device_path)
            if not camera:
                logger.debug(f"No camera found for device path: {device_path}")
                return

            camera_id = camera['id']

            # Mark as disconnected
            db.mark_camera_disconnected(camera_id)
            add_log("INFO", f"Camera disconnected: {camera['friendly_name']}", camera_id)

            # Remove stream from MediaMTX
            remove_stream(str(camera_id))
            logger.debug(f"Removed stream for camera {camera_id}")

            # Unregister from Moonraker
            if camera.get('moonraker_uid'):
                unregister_camera(camera['moonraker_uid'])
                db.update_camera(camera_id, moonraker_uid=None)
                logger.debug(f"Unregistered camera from Moonraker")

        except Exception as e:
            logger.error(f"Error handling camera disconnection: {e}", exc_info=True)

    def _on_print_state_change(self, old_state: str, new_state: str):
        """Handle print state changes (printing <-> standby) for framerate switching."""
        logger.info(f"Print state changed: {old_state} -> {new_state}")

        try:
            # Get all connected cameras
            cameras = db.get_all_cameras_with_settings()

            for camera in cameras:
                if not camera['connected'] or not camera['device_path']:
                    continue

                settings = camera['settings'] or {}

                # Check if standby framerate switching is enabled
                if not settings.get('standby_enabled') or not settings.get('standby_framerate'):
                    continue

                # Determine which framerate to use
                base_fps = settings.get('framerate', 30)
                standby_fps = settings.get('standby_framerate')

                if new_state == 'printing':
                    target_fps = base_fps
                else:
                    target_fps = standby_fps

                # Get current effective framerate
                # (we track what we last set, not the saved setting)
                current_fps = getattr(self, '_camera_framerates', {}).get(camera['id'], base_fps)

                if target_fps == current_fps:
                    # No change needed
                    continue

                logger.info(f"Switching camera {camera['id']} from {current_fps}fps to {target_fps}fps")

                # Build new settings with updated framerate
                new_settings = settings.copy()
                new_settings['framerate'] = target_fps

                # Restart stream with new framerate
                success, error = start_camera_stream(
                    camera['device_path'],
                    str(camera['id']),
                    new_settings,
                    self.print_monitor
                )
                if success:
                    # Track what framerate we set
                    if not hasattr(self, '_camera_framerates'):
                        self._camera_framerates = {}
                    self._camera_framerates[camera['id']] = target_fps
                    add_log("INFO", f"Switched to {new_state} framerate ({target_fps}fps)", camera['id'])
                else:
                    logger.error(f"Failed to switch framerate for camera {camera['id']}: {error}")

        except Exception as e:
            logger.error(f"Error handling print state change: {e}", exc_info=True)


def main():
    """Main entry point."""
    # Ensure we're in the right directory
    if not BASE_DIR.exists():
        BASE_DIR.mkdir(parents=True, exist_ok=True)

    daemon = RavensPerchDaemon()
    daemon.start()


if __name__ == "__main__":
    main()
