#!/usr/bin/env python3

"""
snapfeeder.py
-------------
Flask-based JPEG snapshot server for MediaMTX RTSP streams.

This server:
- Queries MediaMTX API for active camera paths
- Spawns a PyAV capture thread for each RTSP stream
- Encodes to JPEG on-demand using TurboJPEG
- Periodically polls API for new/removed cameras
- One snapshot endpoint per camera: /{path_name}.jpg

Dependencies:
- flask, av, turbojpeg, ffmpeg
"""

import os
import sys
import av
import json
import time
import signal
import threading
import subprocess
import urllib.request
import urllib.error
from flask import Flask, Response, send_file
from io import BytesIO
from turbojpeg import TurboJPEG

# ============================================================================
# CONFIGURATION
# ============================================================================

# MediaMTX API settings
MEDIAMTX_API_HOST = os.environ.get("MEDIAMTX_API_HOST", "localhost")
MEDIAMTX_API_PORT = int(os.environ.get("MEDIAMTX_API_PORT", "9997"))
MEDIAMTX_RTSP_PORT = int(os.environ.get("MEDIAMTX_RTSP_PORT", "8554"))

# Snapshot server settings
SNAPSHOT_PORT = int(os.environ.get("SNAPSHOT_PORT", "5050"))
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "85"))

# How often to poll API for camera changes (seconds)
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))

# ============================================================================
# GLOBALS
# ============================================================================

app = Flask(__name__)
CAMERAS = {}  # path_name → camera info dict
CAMERAS_LOCK = threading.Lock()
JPEG_ENCODER = TurboJPEG()
SHUTDOWN_EVENT = threading.Event()

# ============================================================================
# MEDIAMTX API FUNCTIONS
# ============================================================================

def mediamtx_api_url(endpoint):
    """Build MediaMTX API URL"""
    return f"http://{MEDIAMTX_API_HOST}:{MEDIAMTX_API_PORT}{endpoint}"

def get_mediamtx_paths():
    """
    Query MediaMTX API for all paths.
    
    Returns:
        Dict of path_name → path_info, or empty dict on error
    """
    try:
        req = urllib.request.Request(
            mediamtx_api_url("/v3/paths/list"),
            method="GET"
        )
        req.add_header('Accept', 'application/json')
        
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            paths = {}
            for item in data.get('items', []):
                name = item.get('name')
                if name:
                    paths[name] = item
            
            return paths
    
    except urllib.error.URLError as e:
        print(f"[API] Connection error: {e}")
        return {}
    except Exception as e:
        print(f"[API] Error fetching paths: {e}")
        return {}

def get_rtsp_url(path_name):
    """Build RTSP URL for a given path"""
    return f"rtsp://{MEDIAMTX_API_HOST}:{MEDIAMTX_RTSP_PORT}/{path_name}"

# ============================================================================
# CAPTURE THREAD
# ============================================================================

def capture_loop(name):
    """
    Connects to RTSP stream using PyAV and stores the latest frame.
    JPEG encoding happens on-demand during HTTP request.
    """
    retry_delay = 5
    
    while not SHUTDOWN_EVENT.is_set():
        # Check if camera still exists
        with CAMERAS_LOCK:
            if name not in CAMERAS:
                print(f"[{name}] Camera removed, stopping capture thread")
                return
            cam = CAMERAS[name]
        
        try:
            rtsp_url = get_rtsp_url(name)
            print(f"[{name}] Connecting to {rtsp_url}")
            
            container = av.open(
                rtsp_url,
                options={
                    "rtsp_transport": "tcp",
                    "stimeout": "5000000",  # 5s timeout
                    "fflags": "nobuffer",
                    "flags": "low_delay"
                }
            )
            
            with CAMERAS_LOCK:
                if name in CAMERAS:
                    CAMERAS[name]['container'] = container
                    CAMERAS[name]['connected'] = True
            
            print(f"[{name}] Connected, capturing frames")
            
            for frame in container.decode(video=0):
                if SHUTDOWN_EVENT.is_set():
                    break
                
                with CAMERAS_LOCK:
                    if name not in CAMERAS:
                        break
                    CAMERAS[name]['latest_frame'] = frame
                    CAMERAS[name]['latest_jpeg'] = None  # Invalidate cached JPEG
                    CAMERAS[name]['frame_time'] = time.time()
            
            container.close()
            
        except av.AVError as e:
            print(f"[{name}] AVError: {e}, retrying in {retry_delay}s...")
        except Exception as e:
            print(f"[{name}] Error: {e}, retrying in {retry_delay}s...")
        
        with CAMERAS_LOCK:
            if name in CAMERAS:
                CAMERAS[name]['connected'] = False
                CAMERAS[name]['container'] = None
        
        # Wait before retry, but check for shutdown
        for _ in range(retry_delay * 10):
            if SHUTDOWN_EVENT.is_set():
                return
            time.sleep(0.1)

# ============================================================================
# CAMERA MANAGEMENT
# ============================================================================

def add_camera(name):
    """Add a new camera and start capture thread"""
    with CAMERAS_LOCK:
        if name in CAMERAS:
            return  # Already exists
        
        print(f"[Manager] Adding camera: {name}")
        CAMERAS[name] = {
            'container': None,
            'latest_frame': None,
            'latest_jpeg': None,
            'frame_time': None,
            'connected': False,
            'thread': None
        }
    
    # Start capture thread
    t = threading.Thread(target=capture_loop, args=(name,), daemon=True)
    t.start()
    
    with CAMERAS_LOCK:
        if name in CAMERAS:
            CAMERAS[name]['thread'] = t

def remove_camera(name):
    """Remove a camera (thread will stop on next iteration)"""
    with CAMERAS_LOCK:
        if name in CAMERAS:
            print(f"[Manager] Removing camera: {name}")
            cam = CAMERAS.pop(name)
            
            # Close container if open
            container = cam.get('container')
            if container:
                try:
                    container.close()
                except:
                    pass

def sync_cameras():
    """
    Sync cameras with MediaMTX API.
    Adds new cameras, removes old ones.
    """
    api_paths = get_mediamtx_paths()
    
    if not api_paths:
        # API not available or no paths - don't remove existing cameras
        return
    
    with CAMERAS_LOCK:
        current_names = set(CAMERAS.keys())
    
    api_names = set(api_paths.keys())
    
    # Add new cameras
    for name in api_names - current_names:
        add_camera(name)
    
    # Remove cameras no longer in API
    for name in current_names - api_names:
        remove_camera(name)

def camera_poll_loop():
    """Periodically poll API for camera changes"""
    while not SHUTDOWN_EVENT.is_set():
        sync_cameras()
        
        # Sleep in small increments to allow quick shutdown
        for _ in range(POLL_INTERVAL * 10):
            if SHUTDOWN_EVENT.is_set():
                return
            time.sleep(0.1)

# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route('/<name>.jpg')
def serve_snapshot(name):
    """
    Returns latest JPEG snapshot for a camera.
    
    Response codes:
    - 200: Success, returns JPEG
    - 404: Camera not found
    - 503: Frame not ready (camera connecting or no frames yet)
    - 500: Encoding error
    """
    with CAMERAS_LOCK:
        cam = CAMERAS.get(name)
        if not cam:
            return Response("Camera not found", status=404)
        
        frame = cam.get('latest_frame')
        if frame is None:
            return Response("Frame not ready", status=503)
        
        # Return cached JPEG if available
        if cam.get('latest_jpeg'):
            return Response(cam['latest_jpeg'], mimetype='image/jpeg')
        
        # Encode frame to JPEG
        try:
            jpeg_buf = JPEG_ENCODER.encode(
                frame.to_ndarray(format='bgr24'),
                quality=JPEG_QUALITY,
                pixel_format=1  # TJPF_BGR
            )
            cam['latest_jpeg'] = jpeg_buf
            return Response(jpeg_buf, mimetype='image/jpeg')
        except Exception as e:
            return Response(f"Encoding error: {e}", status=500)

@app.route('/status')
def status():
    """Return JSON status of all cameras"""
    with CAMERAS_LOCK:
        status_data = {}
        for name, cam in CAMERAS.items():
            status_data[name] = {
                'connected': cam.get('connected', False),
                'has_frame': cam.get('latest_frame') is not None,
                'frame_age': None
            }
            if cam.get('frame_time'):
                status_data[name]['frame_age'] = round(time.time() - cam['frame_time'], 1)
    
    return Response(
        json.dumps(status_data, indent=2),
        mimetype='application/json'
    )

@app.route('/health')
def health():
    """Health check endpoint"""
    with CAMERAS_LOCK:
        camera_count = len(CAMERAS)
        connected_count = sum(1 for c in CAMERAS.values() if c.get('connected'))
    
    return Response(
        json.dumps({
            'status': 'ok',
            'cameras': camera_count,
            'connected': connected_count
        }),
        mimetype='application/json'
    )

# ============================================================================
# SHUTDOWN HANDLING
# ============================================================================

def cleanup():
    """Graceful shutdown: stop all threads and close connections"""
    print("\n[Shutdown] Cleaning up...")
    SHUTDOWN_EVENT.set()
    
    with CAMERAS_LOCK:
        for name, cam in CAMERAS.items():
            container = cam.get('container')
            if container:
                try:
                    container.close()
                except:
                    pass
    
    print("[Shutdown] Complete")

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    cleanup()
    sys.exit(0)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    import atexit
    
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                    Snapfeeder - JPEG Server                     ║
╠══════════════════════════════════════════════════════════════════╣
║  MediaMTX API:  http://{MEDIAMTX_API_HOST}:{MEDIAMTX_API_PORT:<5}                            ║
║  RTSP Base:     rtsp://{MEDIAMTX_API_HOST}:{MEDIAMTX_RTSP_PORT:<5}                           ║
║  Snapshot Port: {SNAPSHOT_PORT:<5}                                            ║
║  Poll Interval: {POLL_INTERVAL}s                                              ║
╚══════════════════════════════════════════════════════════════════╝
""")
    
    # Register cleanup handlers
    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Initial camera sync
    print("[Startup] Fetching cameras from MediaMTX API...")
    sync_cameras()
    
    with CAMERAS_LOCK:
        if CAMERAS:
            print(f"[Startup] Found {len(CAMERAS)} camera(s):")
            for name in CAMERAS:
                print(f"  - {name}")
        else:
            print("[Startup] No cameras found yet (will poll for changes)")
    
    # Start camera polling thread
    poll_thread = threading.Thread(target=camera_poll_loop, daemon=True)
    poll_thread.start()
    
    # Give capture threads a moment to connect
    time.sleep(1)
    
    # Start Flask
    print(f"\n[Startup] Starting snapshot server on port {SNAPSHOT_PORT}...")
    app.run(host='0.0.0.0', port=SNAPSHOT_PORT, threaded=True)
