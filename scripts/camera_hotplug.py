#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
camera_hotplug.py
-----------------
Plug-and-play camera detection daemon for Ravens Perch.

This service:
1. Monitors for USB camera connect/disconnect events
2. Auto-configures new cameras with optimal quality settings
3. Adds cameras to both MediaMTX and Moonraker automatically
4. Handles camera disconnection gracefully

Uses pyudev for device monitoring (preferred) or falls back to polling.

Environment Variables:
  HOTPLUG_POLL_INTERVAL  - Polling interval in seconds (default: 3)
  HOTPLUG_DEBOUNCE       - Debounce delay for new devices (default: 2)
  AUTO_ADD_MOONRAKER     - Auto-add to Moonraker (default: true)

Last modified: 2026-01-24
"""

import os
import sys
import time
import signal
import threading
import logging
from pathlib import Path

# Add scripts directory to path for imports
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    load_raven_settings, save_raven_settings,
    get_all_cameras, get_all_video_devices,
    find_camera_by_hardware, create_camera_config, save_camera_config,
    is_capture_device, get_device_serial,
    mediamtx_api_available, moonraker_api_available,
    detect_moonraker_url, get_system_ip,
    run_v4l2ctl, parse_formats,
    sync_camera_to_mediamtx, sync_camera_to_moonraker,
    detect_hardware_acceleration,
    sanitize_camera_name, update_camera_capabilities,
    COLOR_CYAN, COLOR_RESET, COLOR_HIGH, COLOR_YELLOW
)

# ============================================================================
# CONFIGURATION
# ============================================================================

POLL_INTERVAL = int(os.environ.get("HOTPLUG_POLL_INTERVAL", "3"))
DEBOUNCE_DELAY = int(os.environ.get("HOTPLUG_DEBOUNCE", "2"))
AUTO_ADD_MOONRAKER = os.environ.get("AUTO_ADD_MOONRAKER", "true").lower() == "true"

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger('camera_hotplug')

# ============================================================================
# GLOBALS
# ============================================================================

SHUTDOWN_EVENT = threading.Event()
KNOWN_DEVICES = set()  # Set of (hardware_name, serial_number) tuples
PROCESSING_LOCK = threading.Lock()

# ============================================================================
# DEVICE DETECTION
# ============================================================================

def get_current_devices():
    """
    Get set of currently connected camera devices.
    
    Returns:
        Dict mapping (hardware_name, serial_number) to device info
    """
    devices = {}
    for dev in get_all_video_devices():
        key = (dev['hardware_name'], dev.get('serial_number'))
        devices[key] = dev
    return devices

def get_device_capabilities(device_path):
    """Get format/resolution/fps capabilities for a device."""
    output = run_v4l2ctl(device_path, ["--list-formats-ext"])
    return parse_formats(output)

# ============================================================================
# AUTO-CONFIGURATION
# ============================================================================

def auto_configure_camera(device_info):
    """
    Automatically configure a newly detected camera.
    
    Args:
        device_info: Dict with path, hardware_name, serial_number
        
    Returns:
        camera_config if successful, None otherwise
    """
    device_path = device_info['path']
    hardware_name = device_info['hardware_name']
    serial_number = device_info.get('serial_number')
    
    log.info(f"Auto-configuring: {hardware_name} ({device_path})")
    
    # Load current settings
    settings = load_raven_settings()
    if not settings:
        log.error("Failed to load raven_settings.yml")
        return None
    
    # Check if already configured
    existing, _ = find_camera_by_hardware(settings, hardware_name, serial_number)
    if existing:
        log.info(f"Camera already configured as {existing.get('uid')}, syncing...")
        # Just sync the existing camera
        use_vaapi, use_v4l2m2m = detect_hardware_acceleration()
        success, error = sync_camera_to_mediamtx(existing, use_vaapi, use_v4l2m2m)
        if success:
            log.info(f"Synced existing camera {existing.get('uid')} to MediaMTX")
        else:
            log.error(f"Failed to sync to MediaMTX: {error}")
        return existing
    
    # Get device capabilities
    capabilities = get_device_capabilities(device_path)
    if not capabilities:
        log.warning(f"Could not detect capabilities for {hardware_name}")
        # Use defaults
        capabilities = {'mjpeg': {'1280x720': [30, 15], '640x480': [30, 15]}}
    
    # Import quality selection from quick_config
    try:
        from quick_config import find_best_format, get_quality_specs, estimate_cpu_capability
    except ImportError:
        log.error("Could not import quick_config module")
        return None
    
    # Estimate system capability
    capability = estimate_cpu_capability()
    num_cameras = len(get_all_cameras(settings)) + 1
    specs = get_quality_specs(capability, num_cameras)
    
    log.info(f"System capability: {capability}/10, targeting {specs['target_res']} @ {specs['target_fps']} fps")
    
    # Find best format/resolution/fps
    best = find_best_format(capabilities, specs['target_res'], specs['target_fps'])
    
    if not best:
        log.warning("Could not find suitable format, using defaults")
        best = {
            'format': 'mjpeg',
            'resolution': '1280x720',
            'fps': 15
        }
    
    log.info(f"Selected: {best['format']} {best['resolution']} @ {best['fps']} fps")
    
    # Create camera config
    friendly_name = sanitize_camera_name(hardware_name)
    
    # Ensure unique friendly name
    existing_names = [c.get('friendly_name') for c in get_all_cameras(settings)]
    if friendly_name in existing_names:
        counter = 2
        while f"{friendly_name}_{counter}" in existing_names:
            counter += 1
        friendly_name = f"{friendly_name}_{counter}"
    
    camera_config = create_camera_config(hardware_name, friendly_name, serial_number)
    
    # Set capture settings
    camera_config['mediamtx']['ffmpeg']['capture'] = {
        'format': best['format'],
        'resolution': best['resolution'],
        'framerate': best['fps']
    }
    
    # Set encoding settings
    use_vaapi, use_v4l2m2m = detect_hardware_acceleration()
    encoder = 'vaapi' if use_vaapi else ('v4l2m2m' if use_v4l2m2m else 'libx264')
    
    camera_config['mediamtx']['ffmpeg']['encoding'] = {
        'encoder': encoder,
        'bitrate': '4M',
        'preset': 'ultrafast',
        'gop': 15,
        'output_fps': best['fps'],
        'rotation': 0
    }
    
    # Set Moonraker settings
    camera_config['moonraker'] = {
        'enabled': AUTO_ADD_MOONRAKER,
        'moonraker_uid': None,
        'flip_horizontal': False,
        'flip_vertical': False,
        'rotation': 0
    }
    
    # Store capabilities
    camera_config['capabilities'] = capabilities
    
    # Save to settings
    settings = save_camera_config(settings, camera_config)
    if not save_raven_settings(settings):
        log.error("Failed to save camera configuration")
        return None
    
    uid = camera_config['uid']
    log.info(f"Created camera config: {uid} ({friendly_name})")
    
    # Sync to MediaMTX
    if mediamtx_api_available():
        success, error = sync_camera_to_mediamtx(camera_config, use_vaapi, use_v4l2m2m)
        if success:
            log.info(f"Added {uid} to MediaMTX")
        else:
            log.error(f"Failed to add to MediaMTX: {error}")
    else:
        log.warning("MediaMTX API not available, skipping sync")
    
    # Sync to Moonraker
    if AUTO_ADD_MOONRAKER:
        moonraker_url = detect_moonraker_url()
        if moonraker_api_available(moonraker_url):
            # Wait for stream to initialize
            log.info("Waiting for stream to initialize before adding to Moonraker...")
            time.sleep(3)
            
            system_ip = get_system_ip()
            success, error, mr_uid = sync_camera_to_moonraker(
                camera_config, system_ip, moonraker_url
            )
            
            if success:
                log.info(f"Added {uid} to Moonraker (UID: {mr_uid})")
                if mr_uid:
                    camera_config['moonraker']['moonraker_uid'] = mr_uid
                    settings = save_camera_config(settings, camera_config)
                    save_raven_settings(settings)
            else:
                log.error(f"Failed to add to Moonraker: {error}")
        else:
            log.info("Moonraker not available, skipping")
    
    return camera_config

def handle_device_removed(device_key):
    """
    Handle camera disconnection.
    
    We don't remove the camera from settings - it may be reconnected.
    MediaMTX will handle the stream restart automatically when reconnected.
    """
    hardware_name, serial_number = device_key
    log.info(f"Camera disconnected: {hardware_name}")
    # Note: We intentionally don't remove from settings or MediaMTX
    # MediaMTX's runOnInitRestart will handle reconnection

# ============================================================================
# DEVICE MONITORING
# ============================================================================

def try_pyudev_monitor():
    """
    Try to use pyudev for device monitoring (more efficient than polling).
    
    Returns:
        True if pyudev monitoring started, False otherwise
    """
    try:
        import pyudev
        
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem='video4linux')
        
        log.info("Using pyudev for device monitoring")
        
        def device_event_handler():
            for device in iter(monitor.poll, None):
                if SHUTDOWN_EVENT.is_set():
                    break
                
                if device.action == 'add':
                    device_node = device.device_node
                    if device_node and device_node.startswith('/dev/video'):
                        # Debounce
                        time.sleep(DEBOUNCE_DELAY)
                        check_for_new_devices()
                
                elif device.action == 'remove':
                    check_for_removed_devices()
        
        monitor_thread = threading.Thread(target=device_event_handler, daemon=True)
        monitor_thread.start()
        
        return True
    
    except ImportError:
        log.info("pyudev not available, using polling")
        return False
    except Exception as e:
        log.warning(f"pyudev error: {e}, using polling")
        return False

def check_for_new_devices():
    """Check for newly connected devices."""
    global KNOWN_DEVICES
    
    with PROCESSING_LOCK:
        current_devices = get_current_devices()
        current_keys = set(current_devices.keys())
        
        # Find new devices
        new_keys = current_keys - KNOWN_DEVICES
        
        for key in new_keys:
            device_info = current_devices[key]
            log.info(f"New camera detected: {key[0]} ({device_info['path']})")
            
            try:
                auto_configure_camera(device_info)
            except Exception as e:
                log.error(f"Error auto-configuring camera: {e}")
        
        KNOWN_DEVICES = current_keys

def check_for_removed_devices():
    """Check for disconnected devices."""
    global KNOWN_DEVICES
    
    with PROCESSING_LOCK:
        current_devices = get_current_devices()
        current_keys = set(current_devices.keys())
        
        # Find removed devices
        removed_keys = KNOWN_DEVICES - current_keys
        
        for key in removed_keys:
            handle_device_removed(key)
        
        KNOWN_DEVICES = current_keys

def polling_monitor_loop():
    """Fallback polling-based device monitoring."""
    log.info(f"Starting polling monitor (interval: {POLL_INTERVAL}s)")
    
    while not SHUTDOWN_EVENT.is_set():
        try:
            check_for_new_devices()
            check_for_removed_devices()
        except Exception as e:
            log.error(f"Error in polling loop: {e}")
        
        # Sleep in small increments for responsive shutdown
        for _ in range(POLL_INTERVAL * 10):
            if SHUTDOWN_EVENT.is_set():
                return
            time.sleep(0.1)

# ============================================================================
# STARTUP
# ============================================================================

def initialize():
    """Initialize the daemon - discover existing devices."""
    global KNOWN_DEVICES
    
    log.info("Initializing - discovering existing cameras...")
    
    # Get currently connected devices
    current_devices = get_current_devices()
    KNOWN_DEVICES = set(current_devices.keys())
    
    log.info(f"Found {len(KNOWN_DEVICES)} connected camera(s)")
    
    # Check if any need to be synced
    settings = load_raven_settings()
    if settings:
        cameras = get_all_cameras(settings)
        
        for key, device_info in current_devices.items():
            hardware_name, serial_number = key
            existing, _ = find_camera_by_hardware(settings, hardware_name, serial_number)
            
            if existing:
                # Sync existing camera
                log.info(f"Syncing existing camera: {existing.get('uid')} ({hardware_name})")
                use_vaapi, use_v4l2m2m = detect_hardware_acceleration()
                success, error = sync_camera_to_mediamtx(existing, use_vaapi, use_v4l2m2m)
                if not success:
                    log.warning(f"Failed to sync {existing.get('uid')}: {error}")

# ============================================================================
# SHUTDOWN
# ============================================================================

def signal_handler(signum, frame):
    log.info("Shutdown signal received")
    SHUTDOWN_EVENT.set()

# ============================================================================
# MAIN
# ============================================================================

def main():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║              Ravens Perch Camera Hotplug Daemon                 ║
╠══════════════════════════════════════════════════════════════════╣
║  Poll Interval:    {POLL_INTERVAL}s                                            ║
║  Debounce Delay:   {DEBOUNCE_DELAY}s                                            ║
║  Auto Moonraker:   {str(AUTO_ADD_MOONRAKER):<5}                                        ║
╚══════════════════════════════════════════════════════════════════╝
""")
    
    # Initialize - discover existing devices
    initialize()
    
    # Try to use pyudev, fall back to polling
    use_pyudev = try_pyudev_monitor()
    
    if use_pyudev:
        # pyudev is running in a thread, just wait for shutdown
        log.info("Hotplug daemon running (pyudev mode)")
        while not SHUTDOWN_EVENT.is_set():
            time.sleep(1)
    else:
        # Use polling
        log.info("Hotplug daemon running (polling mode)")
        polling_monitor_loop()
    
    log.info("Hotplug daemon stopped")

if __name__ == '__main__':
    main()
