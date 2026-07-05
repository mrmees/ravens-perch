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
import sys
import signal
import logging
import threading
import time
import queue

from .config import (
    BASE_DIR, LOG_DIR, LOG_LEVEL,
    WEB_UI_HOST, WEB_UI_PORT
)
from .logging_utils import apply_log_level, resolve_log_level
from .db import init_db, add_log, get_all_cameras, update_camera
from .hardware import (
    detect_encoders, check_ffmpeg_available,
    check_v4l2_utils_available, get_platform_info,
    init_encoder_cache
)
from .camera_identity import IDENTITY_SERIAL, ResolvedDevice
from .camera_manager import (
    CameraMonitor, probe_capabilities, auto_configure,
    add_rejected_camera, remove_rejected_camera
)
from .stream_manager import (
    wait_for_available as wait_for_mediamtx,
    build_ffmpeg_command, add_or_update_stream, remove_stream,
    remove_all_streams, start_camera_stream
)
from .moonraker_client import (
    detect_moonraker_url, register_camera, unregister_camera,
    build_stream_url, build_snapshot_url, build_stream_extra_data, get_system_ip,
    set_url as set_moonraker_url, is_available as moonraker_is_available
)
from .print_status import init_monitor
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
    console_handler.setLevel(getattr(logging, resolve_log_level(LOG_LEVEL), logging.INFO))

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

MEDIAMTX_RECONCILE_INTERVAL = 15.0
MOONRAKER_RECONCILE_INTERVAL = 30.0


class RavensPerchDaemon:
    """Main daemon class that orchestrates all components."""

    def __init__(self):
        self.camera_monitor = None
        self.web_thread = None
        self.running = False
        self.encoders = {}
        self.moonraker_url = None
        self.print_monitor = None
        self._moonraker_queue = queue.Queue()
        self._moonraker_queue_lock = threading.Lock()
        self._queued_moonraker_camera_ids = set()
        self._moonraker_worker = None
        self._mediamtx_reconciler = None
        self._moonraker_reconciler = None
        self._moonraker_cleaned = False
        self._moonraker_announced = False
        self._moonraker_unavailable_logged = False
        self._camera_framerates = {}

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
            self._apply_saved_log_level()
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

            # Step 6: Detect Moonraker and initialize integrations when available.
            logger.info("Detecting Moonraker...")
            self._ensure_moonraker_integration()

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

            # Step 10: Keep runtime service integrations reconciled after restarts
            self._start_mediamtx_reconciler()
            self._start_moonraker_reconciler()

            logger.info("Ravens Perch is running")
            add_log("INFO", "Ravens Perch started successfully")

            # Announce management URL to Klipper console (if Moonraker available)
            if self.moonraker_url and not self._moonraker_announced:
                from .moonraker_client import announce_management_url
                announce_management_url()
                self._moonraker_announced = True

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

        # Stop daemon-owned FFmpeg publishers and remove dynamic MediaMTX paths
        try:
            removed = remove_all_streams()
            if removed:
                logger.info(f"Removed {removed} MediaMTX stream path(s)")
        except Exception as e:
            logger.warning(f"Error stopping streams: {e}")

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

    def _apply_saved_log_level(self):
        """Apply the log level stored by the web UI."""
        configured = db.get_setting('log_level', LOG_LEVEL)
        applied = apply_log_level(configured, LOG_LEVEL)
        logger.info(f"Log level set to {applied}")

    def _resolve_moonraker_url(self):
        """Use configured Moonraker URL when set; otherwise fall back to auto-detection."""
        configured = db.get_setting('moonraker_url')
        configured = str(configured).strip() if configured else ""

        if configured:
            set_moonraker_url(configured)
            if moonraker_is_available():
                logger.info(f"Using configured Moonraker URL: {configured}")
                return configured

            logger.warning(f"Configured Moonraker URL is not available: {configured}")
            add_log("WARNING", f"Configured Moonraker URL is not available: {configured}")
            return None

        return detect_moonraker_url()

    def _start_moonraker_worker(self):
        """Start the Moonraker registration worker if it is not already running."""
        if self._moonraker_worker and self._moonraker_worker.is_alive():
            return

        self._moonraker_worker = threading.Thread(
            target=self._moonraker_registration_worker,
            daemon=True,
            name="moonraker-registration-worker",
        )
        self._moonraker_worker.start()
        logger.info("Moonraker registration worker started")

    def _start_moonraker_reconciler(self):
        """Start a background worker that restores Moonraker integration after boot races."""
        if self._moonraker_reconciler and self._moonraker_reconciler.is_alive():
            return

        self._moonraker_reconciler = threading.Thread(
            target=self._moonraker_reconciler_loop,
            daemon=True,
            name="moonraker-reconciler",
        )
        self._moonraker_reconciler.start()
        logger.info("Moonraker reconciler started")

    def _moonraker_reconciler_loop(self):
        """Periodically ensure Moonraker-dependent integrations are available."""
        while self.running:
            try:
                self._ensure_moonraker_integration()
            except Exception as e:
                logger.warning(f"Moonraker reconciliation failed: {e}", exc_info=True)

            self._sleep_while_running(MOONRAKER_RECONCILE_INTERVAL)

    def _ensure_moonraker_integration(self):
        """Initialize or repair Moonraker integration without touching Moonraker service state."""
        if self.moonraker_url and moonraker_is_available():
            available_url = self.moonraker_url
        else:
            available_url = self._resolve_moonraker_url()

        if not available_url:
            if not self._moonraker_unavailable_logged:
                if self.moonraker_url:
                    logger.warning("Moonraker is unavailable; webcam registration will retry")
                else:
                    logger.warning("Moonraker not found - webcam registration will retry")
                    add_log("WARNING", "Moonraker not found")
                self._moonraker_unavailable_logged = True
            self.moonraker_url = None
            return False

        first_available = self.moonraker_url != available_url
        self.moonraker_url = available_url
        self._moonraker_unavailable_logged = False
        if first_available:
            logger.info(f"Moonraker found at: {self.moonraker_url}")
            add_log("INFO", f"Moonraker found at: {self.moonraker_url}")

        self._cleanup_stale_moonraker_registrations_once()
        self._ensure_print_monitor()
        self._start_moonraker_worker()
        self._queue_missing_moonraker_registrations()
        return True

    def _cleanup_stale_moonraker_registrations_once(self):
        """Clear stored webcam UIDs once after Moonraker becomes reachable."""
        if self._moonraker_cleaned:
            return

        logger.info("Cleaning up stale Moonraker webcam registrations...")
        cleaned = 0
        for camera in db.get_all_cameras():
            if camera.get('moonraker_uid'):
                unregister_camera(camera['moonraker_uid'])
                db.update_camera(camera['id'], moonraker_uid=None)
                cleaned += 1
        if cleaned > 0:
            logger.info(f"Removed {cleaned} stale webcam registration(s)")

        self._moonraker_cleaned = True

    def _ensure_print_monitor(self):
        """Start print status monitoring once Moonraker is reachable."""
        if self.print_monitor:
            return

        logger.info("Initializing print status monitor...")
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

    def _queue_moonraker_registration(self, camera_id: int, friendly_name: str, rotation: int = 0):
        """Queue one camera registration, coalescing duplicate retries."""
        with self._moonraker_queue_lock:
            if camera_id in self._queued_moonraker_camera_ids:
                return
            self._queued_moonraker_camera_ids.add(camera_id)
            self._moonraker_queue.put((camera_id, friendly_name, rotation))

    def _queue_missing_moonraker_registrations(self):
        """Queue connected enabled cameras that are not registered with Moonraker."""
        for camera in db.get_all_cameras_with_settings():
            if not camera['connected'] or not camera['enabled'] or camera.get('moonraker_uid'):
                continue

            settings = camera.get('settings') or {}
            rotation = settings.get('rotation', 0)
            self._queue_moonraker_registration(camera['id'], camera['friendly_name'], rotation)

    def _record_camera_framerate(self, camera_id: int, settings: dict):
        """Track the effective framerate currently used by a daemon-started stream."""
        framerate = (settings or {}).get('framerate')
        if framerate:
            self._camera_framerates[camera_id] = framerate

    def _printer_effective_standby(self) -> bool:
        """Return whether print-aware stream settings should use standby values."""
        if not self.print_monitor:
            return False

        effective_state = getattr(self.print_monitor, 'effective_state', None)
        if effective_state:
            return effective_state == 'standby'

        return not self.print_monitor.status.is_printing

    def _start_mediamtx_reconciler(self):
        """Start a background worker that restores MediaMTX paths after restarts."""
        if self._mediamtx_reconciler and self._mediamtx_reconciler.is_alive():
            return

        self._mediamtx_reconciler = threading.Thread(
            target=self._mediamtx_reconciler_loop,
            daemon=True,
            name="mediamtx-reconciler",
        )
        self._mediamtx_reconciler.start()
        logger.info("MediaMTX reconciler started")

    def _mediamtx_reconciler_loop(self):
        """Periodically ensure connected enabled cameras have MediaMTX paths."""
        last_available = None

        while self.running:
            try:
                available = wait_for_mediamtx(timeout=2)
                if available:
                    if last_available is not True:
                        logger.info("MediaMTX available; reconciling streams")
                    self._reconcile_mediamtx_streams()
                    last_available = True
                else:
                    if last_available is not False:
                        logger.warning("MediaMTX unavailable; streams will reconcile when it returns")
                    last_available = False
            except Exception as e:
                logger.warning(f"MediaMTX reconciliation failed: {e}", exc_info=True)

            self._sleep_while_running(MEDIAMTX_RECONCILE_INTERVAL)

    def _sleep_while_running(self, seconds: float):
        """Sleep in short increments so shutdown is responsive."""
        deadline = time.time() + seconds
        while self.running and time.time() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.time())))

    def _reconcile_mediamtx_streams(self):
        """Recreate MediaMTX paths and daemon-owned publishers for connected cameras."""
        reconciled = 0
        failed = 0

        for camera in db.get_all_cameras_with_settings():
            if not camera['connected'] or not camera['enabled'] or not camera['device_path']:
                continue

            settings = (camera['settings'] or {}).copy()
            camera_id = str(camera['id'])

            overlay_path = None
            if settings.get('overlay_enabled') and self.print_monitor:
                self.print_monitor.set_camera_overlay(camera_id, True, settings)
                overlay_path = str(self.print_monitor.get_overlay_path(camera_id))

            if self.print_monitor and settings.get('standby_enabled') and settings.get('standby_framerate'):
                if self._printer_effective_standby():
                    settings['framerate'] = settings['standby_framerate']

            ffmpeg_cmd = build_ffmpeg_command(
                camera['device_path'],
                settings,
                camera_id,
                settings.get('encoder', 'libx264'),
                overlay_path=overlay_path,
            )
            success, error = add_or_update_stream(camera_id, ffmpeg_cmd)
            if success:
                self._record_camera_framerate(camera['id'], settings)
                reconciled += 1
            else:
                failed += 1
                logger.warning(f"Failed to reconcile stream for camera {camera_id}: {error}")

        if reconciled:
            logger.debug(f"Reconciled {reconciled} MediaMTX stream(s)")
        if failed:
            add_log("WARNING", f"Failed to reconcile {failed} MediaMTX stream(s)")

    def _reset_camera_states(self):
        """Mark all cameras as disconnected on startup."""
        cameras = get_all_cameras()
        for camera in cameras:
            if camera['connected']:
                update_camera(camera['id'], connected=False, device_path=None)

    def _start_web_ui(self):
        """Start the web UI in a background thread."""
        try:
            from .web_ui import routes as web_routes
            web_routes.set_effective_framerate_callback(self._record_camera_framerate)
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

    def _on_camera_connected(self, resolved_device: ResolvedDevice):
        """Handle camera connection event."""
        device_info = resolved_device.device
        logger.info(f"Camera connected: {device_info.hardware_name} at {device_info.path}")

        try:
            if resolved_device.is_rejected or not resolved_device.identity_key:
                add_rejected_camera(
                    device_path=device_info.path,
                    hardware_name=device_info.hardware_name,
                    hardware_id=device_info.hardware_id,
                    reason=resolved_device.rejection_reason or "Unsupported camera",
                )
                return

            if (
                resolved_device.identity_strategy == IDENTITY_SERIAL
                and db.is_camera_ignored(resolved_device.identity_key)
            ):
                logger.info(f"Camera {device_info.hardware_name} is ignored, skipping")
                return

            camera = db.get_camera_by_identity_key(resolved_device.identity_key)

            if camera:
                # Existing camera - update connection status
                camera_id = camera['id']
                db.mark_camera_connected(camera_id, device_info.path)
                capabilities = probe_capabilities(device_info.path)
                if capabilities:
                    db.save_camera_capabilities(camera_id, capabilities)
                    logger.info(f"Refreshed capabilities for camera: {camera['friendly_name']}")
                logger.info(f"Reconnected known camera: {camera['friendly_name']}")
                add_log("INFO", f"Camera reconnected: {camera['friendly_name']}", camera_id)
            else:
                # New camera - probe capabilities and auto-configure
                logger.info("New camera detected, probing capabilities...")
                capabilities = probe_capabilities(device_info.path)

                # Count current cameras for quality adjustment
                current_count = len(get_all_cameras(connected_only=True))

                # Auto-configure settings
                settings = auto_configure(capabilities, current_count + 1)

                # Create camera record
                camera_id = db.create_camera(
                    hardware_name=device_info.hardware_name,
                    serial_number=device_info.serial_number,
                    friendly_name=resolved_device.friendly_name,
                    device_path=device_info.path,
                    identity_key=resolved_device.identity_key,
                    identity_strategy=resolved_device.identity_strategy,
                    by_path=device_info.by_path,
                    by_id=device_info.by_id,
                    reported_serial_number=device_info.serial_number,
                )

                # Save settings and capabilities
                db.save_camera_settings(camera_id, settings)
                db.save_camera_capabilities(camera_id, capabilities)

                logger.info(f"Created new camera record: ID {camera_id}")
                add_log("INFO", f"New camera detected: {device_info.hardware_name}", camera_id)

                # Brief delay after probing to ensure V4L2 device is fully released
                # before daemon-owned FFmpeg tries to open it.
                time.sleep(0.5)

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
                if self._printer_effective_standby():
                    settings['framerate'] = settings['standby_framerate']

            # Start stream (applies v4l2 controls, builds command, starts stream)
            success, error = start_camera_stream(
                device_info.path,
                str(camera_id),
                settings,
                self.print_monitor
            )
            if success:
                self._record_camera_framerate(camera_id, settings)
                logger.info(f"Stream started for camera {camera_id}")
            else:
                logger.error(f"Failed to start stream: {error}")
                add_log("ERROR", f"Failed to start stream: {error}", camera_id)
                return

            # Queue Moonraker registration (processed sequentially in background)
            if self.moonraker_url:
                rotation = settings.get('rotation', 0)
                self._queue_moonraker_registration(camera_id, camera['friendly_name'], rotation)

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
                logger.debug("Unregistered camera from Moonraker")

        except Exception as e:
            logger.error(f"Error handling camera disconnection: {e}", exc_info=True)

    def _moonraker_registration_worker(self):
        """Process Moonraker registrations sequentially from the queue."""
        while self.running:
            try:
                cam_id, friendly_name, rotation = self._moonraker_queue.get(timeout=2)
            except queue.Empty:
                continue

            try:
                camera = db.get_camera_with_settings(cam_id)
                if not camera or not camera['connected'] or not camera['enabled']:
                    logger.debug(f"Skipping Moonraker registration for inactive camera {cam_id}")
                    continue

                settings = camera.get('settings') or {}
                friendly_name = camera['friendly_name']
                rotation = settings.get('rotation', rotation)
                host = get_system_ip()
                stream_url = build_stream_url(str(cam_id), host)
                snapshot_url = build_snapshot_url(str(cam_id), host)
                extra_data = build_stream_extra_data(str(cam_id), host)

                success, moonraker_uid, error = register_camera(
                    str(cam_id),
                    friendly_name,
                    stream_url,
                    snapshot_url,
                    rotation=rotation,
                    extra_data=extra_data
                )

                if success and moonraker_uid:
                    db.update_camera(cam_id, moonraker_uid=moonraker_uid)
                    logger.info(f"Registered camera with Moonraker: {moonraker_uid}")
                else:
                    logger.warning(f"Failed to register with Moonraker: {error}")
            except Exception as e:
                logger.error(f"Moonraker registration error for camera {cam_id}: {e}")
            finally:
                with self._moonraker_queue_lock:
                    self._queued_moonraker_camera_ids.discard(cam_id)
                self._moonraker_queue.task_done()

            # Small delay between registrations to avoid overwhelming Moonraker
            time.sleep(1)

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
