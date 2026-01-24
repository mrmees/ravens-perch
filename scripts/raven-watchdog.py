#!/usr/bin/env python3

"""
raven-watchdog.py
-----------------
Watchdog service for Ravens Perch that:
1. Ensures MediaMTX configuration matches raven_settings.yml
2. Provides HTTP API for temporary overrides (resolution, fps)
3. Periodically syncs configuration to MediaMTX

API Endpoints:
  GET  /status              - Service status and sync info
  GET  /cameras             - List all cameras with current settings
  GET  /cameras/<uid>       - Get single camera details
  POST /cameras/<uid>/override - Set temporary overrides
  DELETE /cameras/<uid>/override - Clear overrides (revert to config)
  POST /sync                - Force immediate sync
  POST /reload              - Reload raven_settings.yml from disk

Override JSON format:
{
  "resolution": "1280x720",    # Optional
  "capture_fps": 15,           # Optional
  "output_fps": 5              # Optional
}

Environment Variables:
  WATCHDOG_PORT      - HTTP API port (default: 5051)
  SYNC_INTERVAL      - Seconds between sync checks (default: 30)
  RAVEN_SETTINGS     - Path to raven_settings.yml (auto-detected)
"""

import os
import sys
import json
import time
import signal
import threading
import copy
from pathlib import Path
from flask import Flask, Response, request, jsonify

# Add scripts directory to path for imports
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    load_raven_settings, save_raven_settings,
    get_all_cameras,
    mediamtx_api_available, list_mediamtx_paths, list_active_streams,
    add_or_update_mediamtx_path, delete_mediamtx_path,
    build_ffmpeg_cmd_from_config, get_system_ip,
    detect_moonraker_url, moonraker_api_available,
    has_vaapi_encoder, has_v4l2m2m_encoder,
    list_video_devices, get_device_names,
    validate_camera_settings, get_best_matching_fps,
    update_camera_capabilities
)

# ============================================================================
# CONFIGURATION
# ============================================================================

WATCHDOG_PORT = int(os.environ.get("WATCHDOG_PORT", "5051"))
SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "30"))

# ============================================================================
# GLOBALS
# ============================================================================

app = Flask(__name__)

# Current state
STATE = {
    'settings': None,           # Loaded raven_settings.yml
    'overrides': {},            # uid -> override dict
    'last_sync': None,          # Timestamp of last sync
    'last_sync_result': None,   # Result of last sync
    'sync_count': 0,            # Total syncs performed
    'corrections': 0,           # Total corrections made
}
STATE_LOCK = threading.Lock()
SHUTDOWN_EVENT = threading.Event()

# ============================================================================
# SETTINGS MANAGEMENT
# ============================================================================

def load_settings():
    """Load raven_settings.yml"""
    settings = load_raven_settings()
    if settings:
        with STATE_LOCK:
            STATE['settings'] = settings
        return True
    return False

def get_camera_by_uid(uid):
    """Get camera config by UID"""
    with STATE_LOCK:
        if not STATE['settings']:
            return None
        for cam in STATE['settings'].get('cameras', []):
            if cam.get('uid') == uid:
                return cam
    return None

def get_effective_settings(cam):
    """
    Get effective settings for a camera, applying any overrides.
    
    Returns dict with: resolution, capture_fps, output_fps, and full camera config
    """
    uid = cam.get('uid')
    
    # Base settings from config
    ffmpeg = cam.get('mediamtx', {}).get('ffmpeg', {})
    capture = ffmpeg.get('capture', {})
    encoding = ffmpeg.get('encoding', {})
    
    effective = {
        'resolution': capture.get('resolution', '1280x720'),
        'capture_fps': capture.get('framerate', 30),
        'output_fps': encoding.get('output_fps', capture.get('framerate', 30)),
        'format': capture.get('format', 'mjpeg'),
        'encoder': encoding.get('encoder', 'libx264'),
        'bitrate': encoding.get('bitrate', '4M'),
    }
    
    # Apply overrides if present
    with STATE_LOCK:
        override = STATE['overrides'].get(uid, {})
    
    if override:
        if 'format' in override:
            effective['format'] = override['format']
        if 'resolution' in override:
            effective['resolution'] = override['resolution']
        if 'capture_fps' in override:
            effective['capture_fps'] = override['capture_fps']
        if 'output_fps' in override:
            effective['output_fps'] = override['output_fps']
        effective['has_override'] = True
    else:
        effective['has_override'] = False
    
    return effective

# ============================================================================
# MEDIAMTX SYNC
# ============================================================================

def build_path_config(cam, device_path):
    """
    Build MediaMTX path configuration for a camera.
    
    Args:
        cam: Camera config from raven_settings
        device_path: /dev/videoX path
        
    Returns:
        Dict suitable for MediaMTX API
    """
    effective = get_effective_settings(cam)
    
    # Build FFmpeg command with effective settings
    # We need to construct this similarly to common.py but with overrides
    
    ffmpeg_config = cam.get('mediamtx', {}).get('ffmpeg', {})
    capture_config = copy.deepcopy(ffmpeg_config.get('capture', {}))
    encoding_config = copy.deepcopy(ffmpeg_config.get('encoding', {}))
    
    # Apply effective settings (including any overrides)
    capture_config['format'] = effective['format']
    capture_config['resolution'] = effective['resolution']
    capture_config['framerate'] = effective['capture_fps']
    encoding_config['output_fps'] = effective['output_fps']
    
    # Build the FFmpeg command
    modified_cam = copy.deepcopy(cam)
    modified_cam['mediamtx']['ffmpeg']['capture'] = capture_config
    modified_cam['mediamtx']['ffmpeg']['encoding'] = encoding_config
    
    # Detect hardware acceleration
    use_vaapi = has_vaapi_encoder()
    use_v4l2m2m = has_v4l2m2m_encoder()
    
    cmd = build_ffmpeg_cmd_from_config(modified_cam, device_path, use_vaapi, use_v4l2m2m)
    
    if not cmd:
        return None
    
    return {
        "source": "publisher",
        "runOnInit": cmd,
        "runOnInitRestart": True,
    }

def get_device_for_camera(cam, devices_by_name):
    """Find device path for a camera based on hardware_name"""
    hw_name = cam.get('hardware_name')
    if hw_name and hw_name in devices_by_name:
        return devices_by_name[hw_name]
    return None

def sync_to_mediamtx():
    """
    Sync raven_settings configuration to MediaMTX.
    
    Returns:
        Dict with sync results
    """
    result = {
        'success': True,
        'checked': 0,
        'added': 0,
        'updated': 0,
        'removed': 0,
        'errors': [],
        'timestamp': time.time()
    }
    
    # Check if MediaMTX API is available
    if not mediamtx_api_available():
        result['success'] = False
        result['errors'].append("MediaMTX API not available")
        return result
    
    with STATE_LOCK:
        settings = STATE['settings']
    
    if not settings:
        result['success'] = False
        result['errors'].append("No settings loaded")
        return result
    
    cameras = get_all_cameras(settings)
    if not cameras:
        return result  # Nothing to sync
    
    # Get current MediaMTX paths
    current_paths = list_mediamtx_paths()
    
    # Get available video devices
    devices = list_video_devices()
    device_names = get_device_names()
    
    # Build reverse lookup: hardware_name -> device_path
    devices_by_name = {}
    for dev_path in devices:
        name = device_names.get(dev_path)
        if name:
            devices_by_name[name] = dev_path
    
    # Track which paths we manage
    our_uids = set()
    
    for cam in cameras:
        uid = cam.get('uid')
        if not uid:
            continue
        
        our_uids.add(uid)
        result['checked'] += 1
        
        # Skip disabled cameras
        if not cam.get('mediamtx', {}).get('enabled', True):
            # If disabled but exists in MediaMTX, remove it
            if uid in current_paths:
                success, error = delete_mediamtx_path(uid)
                if success:
                    result['removed'] += 1
                else:
                    result['errors'].append(f"{uid}: Failed to remove disabled camera: {error}")
            continue
        
        # Find device for this camera
        device_path = get_device_for_camera(cam, devices_by_name)
        if not device_path:
            # Camera not connected - remove from MediaMTX if present
            if uid in current_paths:
                delete_mediamtx_path(uid)
                result['removed'] += 1
            continue
        
        # Build path config
        path_config = build_path_config(cam, device_path)
        if not path_config:
            result['errors'].append(f"{uid}: Failed to build FFmpeg command")
            continue
        
        # Check if path exists and needs update
        if uid in current_paths:
            # Path exists - check if config matches
            # For now, always update to ensure config is current
            # (Could optimize by comparing source command)
            success, action, error = add_or_update_mediamtx_path(uid, path_config)
            if success:
                if action == 'updated':
                    result['updated'] += 1
            else:
                result['errors'].append(f"{uid}: Update failed: {error}")
        else:
            # Path doesn't exist - add it
            success, action, error = add_or_update_mediamtx_path(uid, path_config)
            if success:
                result['added'] += 1
            else:
                result['errors'].append(f"{uid}: Add failed: {error}")
    
    # Remove paths that we created but are no longer in config
    # (Only remove paths that look like our UIDs - 4 alphanumeric chars)
    import re
    uid_pattern = re.compile(r'^[a-z0-9]{4}$')
    
    for path_name in current_paths:
        if uid_pattern.match(path_name) and path_name not in our_uids:
            success, error = delete_mediamtx_path(path_name)
            if success:
                result['removed'] += 1
    
    # Update state
    with STATE_LOCK:
        STATE['last_sync'] = result['timestamp']
        STATE['last_sync_result'] = result
        STATE['sync_count'] += 1
        STATE['corrections'] += result['added'] + result['updated'] + result['removed']
    
    if result['errors']:
        result['success'] = False
    
    return result

# ============================================================================
# SYNC LOOP
# ============================================================================

def sync_loop():
    """Periodically sync configuration to MediaMTX"""
    
    # Initial delay to let services start
    for _ in range(50):  # 5 seconds
        if SHUTDOWN_EVENT.is_set():
            return
        time.sleep(0.1)
    
    while not SHUTDOWN_EVENT.is_set():
        # Reload settings from disk
        load_settings()
        
        # Sync to MediaMTX
        result = sync_to_mediamtx()
        
        if result['added'] or result['updated'] or result['removed']:
            print(f"[Sync] Added: {result['added']}, Updated: {result['updated']}, Removed: {result['removed']}")
        
        if result['errors']:
            for err in result['errors'][:3]:  # Limit error output
                print(f"[Sync] Error: {err}")
        
        # Sleep in small increments for responsive shutdown
        for _ in range(SYNC_INTERVAL * 10):
            if SHUTDOWN_EVENT.is_set():
                return
            time.sleep(0.1)

# ============================================================================
# FLASK API ROUTES
# ============================================================================

@app.route('/status')
def api_status():
    """Service status and sync info"""
    with STATE_LOCK:
        camera_count = len(STATE['settings'].get('cameras', [])) if STATE['settings'] else 0
        override_count = len(STATE['overrides'])
        last_sync = STATE['last_sync']
        sync_count = STATE['sync_count']
        corrections = STATE['corrections']
        last_result = STATE['last_sync_result']
    
    return jsonify({
        'service': 'raven-watchdog',
        'status': 'running',
        'cameras_configured': camera_count,
        'active_overrides': override_count,
        'sync_count': sync_count,
        'total_corrections': corrections,
        'last_sync': last_sync,
        'last_sync_age': round(time.time() - last_sync, 1) if last_sync else None,
        'last_sync_success': last_result.get('success') if last_result else None,
        'mediamtx_api': mediamtx_api_available(),
    })

@app.route('/cameras')
def api_cameras():
    """List all cameras with current effective settings"""
    with STATE_LOCK:
        settings = STATE['settings']
        overrides = STATE['overrides'].copy()
    
    if not settings:
        return jsonify({'error': 'No settings loaded'}), 503
    
    cameras = []
    for cam in settings.get('cameras', []):
        uid = cam.get('uid')
        effective = get_effective_settings(cam)
        
        cameras.append({
            'uid': uid,
            'friendly_name': cam.get('friendly_name'),
            'hardware_name': cam.get('hardware_name'),
            'enabled': cam.get('mediamtx', {}).get('enabled', True),
            'effective': effective,
            'has_override': uid in overrides,
            'override': overrides.get(uid),
        })
    
    return jsonify({'cameras': cameras})

@app.route('/cameras/<uid>')
def api_camera_detail(uid):
    """Get single camera details including capabilities"""
    cam = get_camera_by_uid(uid)
    if not cam:
        return jsonify({'error': 'Camera not found'}), 404
    
    effective = get_effective_settings(cam)
    
    with STATE_LOCK:
        override = STATE['overrides'].get(uid)
    
    return jsonify({
        'uid': uid,
        'friendly_name': cam.get('friendly_name'),
        'hardware_name': cam.get('hardware_name'),
        'enabled': cam.get('mediamtx', {}).get('enabled', True),
        'config': cam.get('mediamtx', {}).get('ffmpeg', {}),
        'effective': effective,
        'override': override,
        'capabilities': cam.get('capabilities', {}),
        'capabilities_updated': cam.get('capabilities_updated'),
    })

@app.route('/cameras/<uid>/capabilities')
def api_camera_capabilities(uid):
    """Get just the capabilities for a camera"""
    cam = get_camera_by_uid(uid)
    if not cam:
        return jsonify({'error': 'Camera not found'}), 404
    
    caps = cam.get('capabilities', {})
    
    # Build a summary
    summary = {}
    for fmt, resolutions in caps.items():
        summary[fmt] = {
            'resolutions': list(resolutions.keys()),
            'max_fps': max(max(fps_list) for fps_list in resolutions.values()) if resolutions else 0
        }
    
    return jsonify({
        'uid': uid,
        'capabilities': caps,
        'summary': summary,
        'updated': cam.get('capabilities_updated'),
    })

@app.route('/cameras/<uid>/capabilities/refresh', methods=['POST'])
def api_refresh_capabilities(uid):
    """Refresh capabilities for a camera by re-querying the device"""
    cam = get_camera_by_uid(uid)
    if not cam:
        return jsonify({'error': 'Camera not found'}), 404
    
    success, error = update_camera_capabilities(cam)
    
    if success:
        # Save updated settings
        with STATE_LOCK:
            if STATE['settings']:
                from common import save_raven_settings
                save_raven_settings(STATE['settings'])
        
        return jsonify({
            'success': True,
            'uid': uid,
            'capabilities': cam.get('capabilities', {}),
            'updated': cam.get('capabilities_updated'),
        })
    else:
        return jsonify({
            'success': False,
            'error': error,
        }), 500

@app.route('/cameras/<uid>/override', methods=['POST'])
def api_set_override(uid):
    """
    Set temporary overrides for a camera.
    
    JSON body:
    {
        "format": "mjpeg",         # Optional - capture format
        "resolution": "1280x720",  # Optional
        "capture_fps": 15,         # Optional  
        "output_fps": 5,           # Optional
        "validate": true           # Optional - validate against capabilities (default: true)
    }
    """
    cam = get_camera_by_uid(uid)
    if not cam:
        return jsonify({'error': 'Camera not found'}), 404
    
    try:
        data = request.get_json()
    except:
        return jsonify({'error': 'Invalid JSON'}), 400
    
    if not data:
        return jsonify({'error': 'No override data provided'}), 400
    
    # Check if validation should be skipped
    should_validate = data.get('validate', True)
    
    # Validate override fields
    override = {}
    
    if 'format' in data:
        fmt = data['format']
        if not isinstance(fmt, str):
            return jsonify({'error': 'Invalid format'}), 400
        override['format'] = fmt.lower()
    
    if 'resolution' in data:
        res = data['resolution']
        if not isinstance(res, str) or 'x' not in res:
            return jsonify({'error': 'Invalid resolution format (use WxH)'}), 400
        override['resolution'] = res
    
    if 'capture_fps' in data:
        try:
            fps = int(data['capture_fps'])
            if fps < 1 or fps > 120:
                return jsonify({'error': 'capture_fps must be 1-120'}), 400
            override['capture_fps'] = fps
        except (ValueError, TypeError):
            return jsonify({'error': 'capture_fps must be an integer'}), 400
    
    if 'output_fps' in data:
        try:
            fps = int(data['output_fps'])
            if fps < 1 or fps > 120:
                return jsonify({'error': 'output_fps must be 1-120'}), 400
            override['output_fps'] = fps
        except (ValueError, TypeError):
            return jsonify({'error': 'output_fps must be an integer'}), 400
    
    if not override:
        return jsonify({'error': 'No valid override fields provided'}), 400
    
    # Validate against capabilities if requested
    if should_validate and cam.get('capabilities'):
        # Get effective values for validation
        capture = cam.get('mediamtx', {}).get('ffmpeg', {}).get('capture', {})
        check_format = override.get('format', capture.get('format', 'mjpeg'))
        check_res = override.get('resolution', capture.get('resolution', '1280x720'))
        check_fps = override.get('capture_fps', capture.get('framerate', 30))
        
        valid, error = validate_camera_settings(cam, check_format, check_res, check_fps)
        if not valid:
            # Try to suggest valid alternatives
            caps = cam.get('capabilities', {})
            suggestions = {}
            
            if check_format in caps:
                suggestions['available_resolutions'] = list(caps[check_format].keys())
                if check_res in caps[check_format]:
                    suggestions['available_fps'] = caps[check_format][check_res]
            else:
                suggestions['available_formats'] = list(caps.keys())
            
            return jsonify({
                'error': f'Invalid settings: {error}',
                'validation_failed': True,
                'suggestions': suggestions,
                'hint': 'Set "validate": false to skip validation'
            }), 400
    
    # Apply override
    with STATE_LOCK:
        STATE['overrides'][uid] = override
    
    # Trigger immediate sync
    result = sync_to_mediamtx()
    
    return jsonify({
        'success': True,
        'uid': uid,
        'override': override,
        'sync_result': {
            'updated': result['updated'],
            'errors': result['errors'][:3] if result['errors'] else []
        }
    })

@app.route('/cameras/<uid>/override', methods=['DELETE'])
def api_clear_override(uid):
    """Clear overrides for a camera (revert to config)"""
    cam = get_camera_by_uid(uid)
    if not cam:
        return jsonify({'error': 'Camera not found'}), 404
    
    with STATE_LOCK:
        had_override = uid in STATE['overrides']
        if had_override:
            del STATE['overrides'][uid]
    
    if had_override:
        # Trigger immediate sync
        result = sync_to_mediamtx()
        return jsonify({
            'success': True,
            'uid': uid,
            'message': 'Override cleared, reverted to config',
            'sync_result': {
                'updated': result['updated'],
            }
        })
    else:
        return jsonify({
            'success': True,
            'uid': uid,
            'message': 'No override was set'
        })

@app.route('/cameras/override', methods=['DELETE'])
def api_clear_all_overrides():
    """Clear all overrides"""
    with STATE_LOCK:
        count = len(STATE['overrides'])
        STATE['overrides'].clear()
    
    if count > 0:
        result = sync_to_mediamtx()
        return jsonify({
            'success': True,
            'cleared': count,
            'message': f'Cleared {count} override(s)'
        })
    else:
        return jsonify({
            'success': True,
            'cleared': 0,
            'message': 'No overrides were set'
        })

@app.route('/sync', methods=['POST'])
def api_force_sync():
    """Force immediate sync"""
    load_settings()  # Reload from disk first
    result = sync_to_mediamtx()
    
    return jsonify({
        'success': result['success'],
        'checked': result['checked'],
        'added': result['added'],
        'updated': result['updated'],
        'removed': result['removed'],
        'errors': result['errors'][:5] if result['errors'] else []
    })

@app.route('/reload', methods=['POST'])
def api_reload_settings():
    """Reload raven_settings.yml from disk"""
    success = load_settings()
    
    if success:
        with STATE_LOCK:
            camera_count = len(STATE['settings'].get('cameras', []))
        return jsonify({
            'success': True,
            'message': 'Settings reloaded',
            'cameras': camera_count
        })
    else:
        return jsonify({
            'success': False,
            'error': 'Failed to load settings'
        }), 500

@app.route('/health')
def api_health():
    """Health check endpoint"""
    with STATE_LOCK:
        has_settings = STATE['settings'] is not None
        last_sync = STATE['last_sync']
    
    # Consider unhealthy if no sync in 2x interval
    sync_stale = False
    if last_sync:
        sync_stale = (time.time() - last_sync) > (SYNC_INTERVAL * 2)
    
    healthy = has_settings and mediamtx_api_available() and not sync_stale
    
    return jsonify({
        'healthy': healthy,
        'settings_loaded': has_settings,
        'mediamtx_api': mediamtx_api_available(),
        'sync_stale': sync_stale,
    }), 200 if healthy else 503

# ============================================================================
# SHUTDOWN HANDLING
# ============================================================================

def cleanup():
    """Graceful shutdown"""
    print("\n[Shutdown] Cleaning up...")
    SHUTDOWN_EVENT.set()
    print("[Shutdown] Complete")

def signal_handler(signum, frame):
    cleanup()
    sys.exit(0)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    import atexit
    
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                   Raven Watchdog Service                        ║
╠══════════════════════════════════════════════════════════════════╣
║  API Port:       {WATCHDOG_PORT:<5}                                          ║
║  Sync Interval:  {SYNC_INTERVAL}s                                            ║
╚══════════════════════════════════════════════════════════════════╝
""")
    
    # Register cleanup handlers
    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Load initial settings
    print("[Startup] Loading settings...")
    if load_settings():
        with STATE_LOCK:
            camera_count = len(STATE['settings'].get('cameras', []))
        print(f"[Startup] Loaded {camera_count} camera(s)")
    else:
        print("[Startup] Warning: Could not load settings")
    
    # Start sync thread
    sync_thread = threading.Thread(target=sync_loop, daemon=True)
    sync_thread.start()
    
    # Start Flask
    print(f"\n[Startup] Starting API server on port {WATCHDOG_PORT}...")
    print(f"[Startup] Endpoints:")
    print(f"  GET  /status              - Service status")
    print(f"  GET  /cameras             - List cameras")
    print(f"  POST /cameras/<uid>/override - Set override")
    print(f"  DELETE /cameras/<uid>/override - Clear override")
    print(f"  POST /sync                - Force sync")
    print()
    
    app.run(host='0.0.0.0', port=WATCHDOG_PORT, threaded=True)
