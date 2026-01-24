#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
web_ui.py
---------
Web-based camera configuration interface for Ravens Perch.

Serves a web UI at http://localhost/cameras that allows:
- Viewing all connected cameras with live WebRTC preview
- Selecting resolution, frame rate, format, bitrate
- Toggling Moonraker integration
- Real-time settings changes

Runs on port 80 (requires setcap or root).

Environment Variables:
  WEB_UI_PORT      - HTTP port (default: 80)
  WEB_UI_HOST      - Bind address (default: 0.0.0.0)

Last modified: 2026-01-24
"""

import os
import sys
import json
import time
import signal
import threading
from pathlib import Path
from flask import Flask, Response, request, jsonify, render_template, send_from_directory

# Add scripts directory to path for imports
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from common import (
    load_raven_settings, save_raven_settings,
    get_all_cameras, get_all_video_devices,
    find_camera_by_uid, find_camera_by_hardware,
    create_camera_config, save_camera_config, delete_camera_config,
    mediamtx_api_available, moonraker_api_available,
    detect_moonraker_url, get_system_ip,
    run_v4l2ctl, parse_formats,
    sync_camera_to_mediamtx, sync_camera_to_moonraker,
    delete_mediamtx_path, delete_moonraker_webcam,
    detect_hardware_acceleration,
    sanitize_camera_name, update_camera_capabilities,
    COLOR_CYAN, COLOR_RESET
)

# ============================================================================
# CONFIGURATION
# ============================================================================

WEB_UI_PORT = int(os.environ.get("WEB_UI_PORT", "80"))
WEB_UI_HOST = os.environ.get("WEB_UI_HOST", "0.0.0.0")

# Template and static directories
TEMPLATE_DIR = SCRIPT_DIR / "web_ui" / "templates"
STATIC_DIR = SCRIPT_DIR / "web_ui" / "static"

# ============================================================================
# FLASK APP
# ============================================================================

app = Flask(__name__, 
            template_folder=str(TEMPLATE_DIR),
            static_folder=str(STATIC_DIR))

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_camera_capabilities(device_path):
    """
    Get all available formats, resolutions, and framerates for a device.
    
    Returns:
        Dict: {format: {resolution: [fps_list]}}
    """
    output = run_v4l2ctl(device_path, ["--list-formats-ext"])
    return parse_formats(output)

def find_device_path_for_camera(camera_config):
    """Find the current /dev/videoX path for a camera config."""
    hardware_name = camera_config.get("hardware_name")
    serial_number = camera_config.get("serial_number")
    
    devices = get_all_video_devices()
    
    for dev in devices:
        if dev['hardware_name'] == hardware_name:
            if serial_number:
                if dev['serial_number'] == serial_number:
                    return dev['path']
            else:
                return dev['path']
    
    return None

def camera_to_api_response(cam, device_path=None):
    """Convert a camera config to API response format."""
    uid = cam.get('uid', 'unknown')
    system_ip = get_system_ip()
    
    # Get current capture settings
    ffmpeg = cam.get('mediamtx', {}).get('ffmpeg', {})
    capture = ffmpeg.get('capture', {})
    encoding = ffmpeg.get('encoding', {})
    moonraker = cam.get('moonraker', {})
    
    # Get capabilities if device is connected
    capabilities = cam.get('capabilities', {})
    if device_path and not capabilities:
        capabilities = get_camera_capabilities(device_path)
    
    return {
        'uid': uid,
        'friendly_name': cam.get('friendly_name', uid),
        'hardware_name': cam.get('hardware_name'),
        'serial_number': cam.get('serial_number'),
        'device_path': device_path,
        'connected': device_path is not None,
        
        # Current settings
        'format': capture.get('format', 'mjpeg'),
        'resolution': capture.get('resolution', '1280x720'),
        'framerate': capture.get('framerate', 30),
        'bitrate': encoding.get('bitrate', '4M'),
        'rotation': encoding.get('rotation', 0),
        'encoder': encoding.get('encoder', 'libx264'),
        
        # Moonraker settings
        'moonraker_enabled': moonraker.get('enabled', False),
        'moonraker_uid': moonraker.get('moonraker_uid'),
        
        # Available options
        'capabilities': capabilities,
        
        # Stream URLs
        'urls': {
            'webrtc': f'http://{system_ip}:8889/{uid}/',
            'snapshot': f'http://{system_ip}:5050/{uid}.jpg',
            'rtsp': f'rtsp://{system_ip}:8554/{uid}',
            'hls': f'http://{system_ip}:8888/{uid}/index.m3u8'
        }
    }

# ============================================================================
# WEB UI ROUTES
# ============================================================================

@app.route('/')
def index():
    """Redirect root to /cameras"""
    return render_template('cameras.html')

@app.route('/cameras')
def cameras_page():
    """Serve the main camera configuration page."""
    system_ip = get_system_ip()
    return render_template('cameras.html', system_ip=system_ip)

# ============================================================================
# API ROUTES - CAMERAS
# ============================================================================

@app.route('/api/cameras', methods=['GET'])
def api_list_cameras():
    """
    List all configured cameras with their current settings and status.
    """
    settings = load_raven_settings()
    if not settings:
        return jsonify({'error': 'Failed to load settings'}), 500
    
    cameras = get_all_cameras(settings)
    devices = get_all_video_devices()
    
    # Build device lookup by hardware_name + serial
    device_lookup = {}
    for dev in devices:
        key = (dev['hardware_name'], dev.get('serial_number'))
        device_lookup[key] = dev['path']
    
    result = []
    for cam in cameras:
        # Find device path
        key = (cam.get('hardware_name'), cam.get('serial_number'))
        device_path = device_lookup.get(key)
        
        result.append(camera_to_api_response(cam, device_path))
    
    return jsonify({
        'cameras': result,
        'system_ip': get_system_ip(),
        'mediamtx_available': mediamtx_api_available(),
        'moonraker_available': moonraker_api_available(detect_moonraker_url())
    })

@app.route('/api/cameras/<uid>', methods=['GET'])
def api_get_camera(uid):
    """Get details for a specific camera."""
    settings = load_raven_settings()
    if not settings:
        return jsonify({'error': 'Failed to load settings'}), 500
    
    cam, _ = find_camera_by_uid(settings, uid)
    if not cam:
        return jsonify({'error': 'Camera not found'}), 404
    
    device_path = find_device_path_for_camera(cam)
    return jsonify(camera_to_api_response(cam, device_path))

@app.route('/api/cameras/<uid>', methods=['PUT', 'PATCH'])
def api_update_camera(uid):
    """
    Update camera settings.
    
    Accepts JSON with any of:
    - friendly_name: string
    - format: string (mjpeg, h264, yuyv422, etc.)
    - resolution: string (1280x720, 1920x1080, etc.)
    - framerate: int
    - bitrate: string (1M, 2M, 4M, 8M)
    - rotation: int (0, 90, 180, 270)
    - moonraker_enabled: bool
    """
    settings = load_raven_settings()
    if not settings:
        return jsonify({'error': 'Failed to load settings'}), 500
    
    cam, idx = find_camera_by_uid(settings, uid)
    if not cam:
        return jsonify({'error': 'Camera not found'}), 404
    
    data = request.json
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    # Track what changed
    changes = []
    
    # Update friendly name
    if 'friendly_name' in data:
        cam['friendly_name'] = sanitize_camera_name(data['friendly_name']) or cam['friendly_name']
        changes.append('friendly_name')
    
    # Update capture settings
    capture = cam.setdefault('mediamtx', {}).setdefault('ffmpeg', {}).setdefault('capture', {})
    
    if 'format' in data:
        capture['format'] = data['format'].lower()
        changes.append('format')
    
    if 'resolution' in data:
        capture['resolution'] = data['resolution']
        changes.append('resolution')
    
    if 'framerate' in data:
        try:
            capture['framerate'] = int(data['framerate'])
            changes.append('framerate')
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid framerate'}), 400
    
    # Update encoding settings
    encoding = cam['mediamtx']['ffmpeg'].setdefault('encoding', {})
    
    if 'bitrate' in data:
        encoding['bitrate'] = data['bitrate']
        changes.append('bitrate')
    
    if 'rotation' in data:
        try:
            rotation = int(data['rotation'])
            if rotation not in (0, 90, 180, 270):
                return jsonify({'error': 'Rotation must be 0, 90, 180, or 270'}), 400
            encoding['rotation'] = rotation
            changes.append('rotation')
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid rotation'}), 400
    
    # Update output FPS to match capture FPS
    if 'framerate' in data:
        encoding['output_fps'] = capture['framerate']
    
    # Update Moonraker settings
    if 'moonraker_enabled' in data:
        moonraker = cam.setdefault('moonraker', {})
        moonraker['enabled'] = bool(data['moonraker_enabled'])
        changes.append('moonraker_enabled')
    
    # Save settings
    settings = save_camera_config(settings, cam)
    if not save_raven_settings(settings):
        return jsonify({'error': 'Failed to save settings'}), 500
    
    # Sync to MediaMTX
    sync_errors = []
    if mediamtx_api_available():
        use_vaapi, use_v4l2m2m = detect_hardware_acceleration()
        success, error = sync_camera_to_mediamtx(cam, use_vaapi, use_v4l2m2m)
        if not success:
            sync_errors.append(f'MediaMTX: {error}')
    
    # Sync to Moonraker if enabled
    moonraker_url = detect_moonraker_url()
    if cam.get('moonraker', {}).get('enabled') and moonraker_api_available(moonraker_url):
        success, error, mr_uid = sync_camera_to_moonraker(cam, get_system_ip(), moonraker_url)
        if not success:
            sync_errors.append(f'Moonraker: {error}')
        elif mr_uid:
            # Save the moonraker_uid
            cam['moonraker']['moonraker_uid'] = mr_uid
            save_camera_config(settings, cam)
            save_raven_settings(settings)
    
    return jsonify({
        'success': True,
        'uid': uid,
        'changes': changes,
        'sync_errors': sync_errors if sync_errors else None
    })

@app.route('/api/cameras/<uid>', methods=['DELETE'])
def api_delete_camera(uid):
    """Delete a camera configuration."""
    settings = load_raven_settings()
    if not settings:
        return jsonify({'error': 'Failed to load settings'}), 500
    
    cam, _ = find_camera_by_uid(settings, uid)
    if not cam:
        return jsonify({'error': 'Camera not found'}), 404
    
    # Remove from MediaMTX
    if mediamtx_api_available():
        delete_mediamtx_path(uid)
    
    # Remove from Moonraker
    moonraker_uid = cam.get('moonraker', {}).get('moonraker_uid')
    if moonraker_uid:
        moonraker_url = detect_moonraker_url()
        if moonraker_api_available(moonraker_url):
            delete_moonraker_webcam(moonraker_uid, moonraker_url)
    
    # Remove from settings
    settings = delete_camera_config(settings, uid)
    if not save_raven_settings(settings):
        return jsonify({'error': 'Failed to save settings'}), 500
    
    return jsonify({'success': True, 'uid': uid})

# ============================================================================
# API ROUTES - DEVICES (Unconfigured)
# ============================================================================

@app.route('/api/devices', methods=['GET'])
def api_list_devices():
    """
    List all connected video devices, including unconfigured ones.
    """
    settings = load_raven_settings()
    devices = get_all_video_devices()
    configured_cameras = get_all_cameras(settings) if settings else []
    
    # Build set of configured hardware
    configured_hw = set()
    for cam in configured_cameras:
        key = (cam.get('hardware_name'), cam.get('serial_number'))
        configured_hw.add(key)
    
    result = []
    for dev in devices:
        key = (dev['hardware_name'], dev.get('serial_number'))
        is_configured = key in configured_hw
        
        # Get capabilities
        capabilities = get_camera_capabilities(dev['path'])
        
        # Find the camera config if configured
        camera_uid = None
        if is_configured:
            for cam in configured_cameras:
                if (cam.get('hardware_name'), cam.get('serial_number')) == key:
                    camera_uid = cam.get('uid')
                    break
        
        result.append({
            'path': dev['path'],
            'hardware_name': dev['hardware_name'],
            'serial_number': dev['serial_number'],
            'configured': is_configured,
            'camera_uid': camera_uid,
            'capabilities': capabilities
        })
    
    return jsonify({'devices': result})

@app.route('/api/devices/add', methods=['POST'])
def api_add_device():
    """
    Add a new device as a configured camera.
    
    Accepts JSON with:
    - device_path: string (required) - /dev/videoX
    - friendly_name: string (optional)
    - format: string (optional, default: auto-select)
    - resolution: string (optional, default: auto-select)
    - framerate: int (optional, default: auto-select)
    - moonraker_enabled: bool (optional, default: true)
    """
    data = request.json
    if not data or 'device_path' not in data:
        return jsonify({'error': 'device_path is required'}), 400
    
    device_path = data['device_path']
    
    # Find the device
    devices = get_all_video_devices()
    device_info = None
    for dev in devices:
        if dev['path'] == device_path:
            device_info = dev
            break
    
    if not device_info:
        return jsonify({'error': f'Device not found: {device_path}'}), 404
    
    settings = load_raven_settings()
    if not settings:
        return jsonify({'error': 'Failed to load settings'}), 500
    
    # Check if already configured
    existing, _ = find_camera_by_hardware(
        settings, 
        device_info['hardware_name'], 
        device_info['serial_number']
    )
    if existing:
        return jsonify({
            'error': 'Device already configured',
            'camera_uid': existing.get('uid')
        }), 409
    
    # Get capabilities
    capabilities = get_camera_capabilities(device_path)
    
    # Determine format/resolution/fps
    fmt = data.get('format')
    res = data.get('resolution')
    fps = data.get('framerate')
    
    if not fmt or not res or not fps:
        # Auto-select best settings
        from quick_config import find_best_format, get_quality_specs, estimate_cpu_capability
        
        capability = estimate_cpu_capability()
        num_cameras = len(get_all_cameras(settings)) + 1
        specs = get_quality_specs(capability, num_cameras)
        
        best = find_best_format(capabilities, specs['target_res'], specs['target_fps'])
        
        if best:
            fmt = fmt or best['format']
            res = res or best['resolution']
            fps = fps or best['fps']
        else:
            # Fallback defaults
            fmt = fmt or 'mjpeg'
            res = res or '1280x720'
            fps = fps or 15
    
    # Create camera config
    friendly_name = data.get('friendly_name') or sanitize_camera_name(device_info['hardware_name'])
    
    camera_config = create_camera_config(
        device_info['hardware_name'],
        friendly_name,
        device_info['serial_number']
    )
    
    # Set capture settings
    camera_config['mediamtx']['ffmpeg']['capture'] = {
        'format': fmt,
        'resolution': res,
        'framerate': int(fps)
    }
    
    # Set encoding settings
    use_vaapi, use_v4l2m2m = detect_hardware_acceleration()
    encoder = 'vaapi' if use_vaapi else ('v4l2m2m' if use_v4l2m2m else 'libx264')
    
    camera_config['mediamtx']['ffmpeg']['encoding'] = {
        'encoder': encoder,
        'bitrate': data.get('bitrate', '4M'),
        'preset': 'ultrafast',
        'gop': 15,
        'output_fps': int(fps),
        'rotation': 0
    }
    
    # Set Moonraker settings
    moonraker_enabled = data.get('moonraker_enabled', True)
    camera_config['moonraker'] = {
        'enabled': moonraker_enabled,
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
        return jsonify({'error': 'Failed to save settings'}), 500
    
    uid = camera_config['uid']
    sync_errors = []
    
    # Sync to MediaMTX
    if mediamtx_api_available():
        success, error = sync_camera_to_mediamtx(camera_config, use_vaapi, use_v4l2m2m)
        if not success:
            sync_errors.append(f'MediaMTX: {error}')
    
    # Sync to Moonraker
    if moonraker_enabled:
        moonraker_url = detect_moonraker_url()
        if moonraker_api_available(moonraker_url):
            # Wait a moment for stream to initialize
            time.sleep(2)
            success, error, mr_uid = sync_camera_to_moonraker(
                camera_config, get_system_ip(), moonraker_url
            )
            if success and mr_uid:
                camera_config['moonraker']['moonraker_uid'] = mr_uid
                settings = save_camera_config(settings, camera_config)
                save_raven_settings(settings)
            elif not success:
                sync_errors.append(f'Moonraker: {error}')
    
    return jsonify({
        'success': True,
        'camera': camera_to_api_response(camera_config, device_path),
        'sync_errors': sync_errors if sync_errors else None
    }), 201

# ============================================================================
# API ROUTES - SYSTEM
# ============================================================================

@app.route('/api/status', methods=['GET'])
def api_status():
    """Get system status."""
    settings = load_raven_settings()
    moonraker_url = detect_moonraker_url()
    
    return jsonify({
        'system_ip': get_system_ip(),
        'mediamtx_available': mediamtx_api_available(),
        'moonraker_available': moonraker_api_available(moonraker_url),
        'moonraker_url': moonraker_url,
        'camera_count': len(get_all_cameras(settings)) if settings else 0,
        'device_count': len(get_all_video_devices())
    })

@app.route('/api/sync', methods=['POST'])
def api_sync_all():
    """Force sync all cameras to MediaMTX and Moonraker."""
    settings = load_raven_settings()
    if not settings:
        return jsonify({'error': 'Failed to load settings'}), 500
    
    from common import sync_all_cameras
    
    results = sync_all_cameras(settings)
    
    # Save settings in case moonraker_uids were added
    if results.get('settings_modified'):
        save_raven_settings(settings)
    
    return jsonify({
        'success': True,
        'mediamtx_success': len(results['mediamtx_success']),
        'mediamtx_failed': len(results['mediamtx_failed']),
        'moonraker_success': len(results['moonraker_success']),
        'moonraker_failed': len(results['moonraker_failed']),
        'moonraker_skipped': len(results['moonraker_skipped'])
    })

# ============================================================================
# STATIC FILES
# ============================================================================

@app.route('/static/<path:filename>')
def serve_static(filename):
    """Serve static files."""
    return send_from_directory(str(STATIC_DIR), filename)

# ============================================================================
# SHUTDOWN HANDLING
# ============================================================================

def signal_handler(signum, frame):
    print("\n[Web UI] Shutting down...")
    sys.exit(0)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                 Ravens Perch Web UI                              ║
╠══════════════════════════════════════════════════════════════════╣
║  URL:          http://{get_system_ip()}/cameras                       ║
║  Port:         {WEB_UI_PORT}                                                ║
║  Templates:    {TEMPLATE_DIR}
║  Static:       {STATIC_DIR}
╚══════════════════════════════════════════════════════════════════╝
""")
    
    # Check if port 80 is available
    if WEB_UI_PORT == 80:
        print("Note: Port 80 requires elevated privileges.")
        print("      Run with sudo or use setcap on the Python binary.")
        print()
    
    app.run(host=WEB_UI_HOST, port=WEB_UI_PORT, threaded=True, debug=False)
