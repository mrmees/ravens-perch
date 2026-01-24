"""
Ravens Perch - Web UI Route Handlers
"""
import logging
from flask import (
    Blueprint, render_template, request, jsonify,
    redirect, url_for, Response, flash
)

from ..db import (
    get_all_cameras, get_all_cameras_with_settings,
    get_camera_with_settings, get_camera_by_id,
    update_camera, save_camera_settings, get_camera_settings,
    get_camera_capabilities, get_logs, get_all_settings,
    set_setting, add_log
)
from ..snapshot_server import grab_snapshot, get_placeholder_image
from ..stream_manager import (
    build_ffmpeg_command, add_or_update_stream, get_stream_urls,
    is_stream_active, restart_stream
)
from ..moonraker_client import (
    register_camera, update_camera as update_moonraker_camera,
    build_stream_url, build_snapshot_url, get_system_ip, is_available as moonraker_available
)
from ..hardware import estimate_cpu_capability, detect_encoders, get_platform_info
from ..config import COMMON_RESOLUTIONS, COMMON_FRAMERATES

logger = logging.getLogger(__name__)

bp = Blueprint('cameras', __name__)


# ============ Dashboard ============

@bp.route('/')
def dashboard():
    """Camera dashboard - main page."""
    cameras = get_all_cameras_with_settings()

    # Add stream status to each camera
    for camera in cameras:
        camera['stream_active'] = is_stream_active(str(camera['id']))
        camera['stream_urls'] = get_stream_urls(str(camera['id']), get_system_ip())

    return render_template(
        'dashboard.html',
        cameras=cameras,
        system_ip=get_system_ip()
    )


@bp.route('/api/status')
def api_status():
    """Get all cameras status as JSON (for HTMX polling)."""
    cameras = get_all_cameras()
    status = []

    for camera in cameras:
        status.append({
            'id': camera['id'],
            'name': camera['friendly_name'],
            'connected': camera['connected'],
            'enabled': camera['enabled'],
            'stream_active': is_stream_active(str(camera['id'])),
        })

    return jsonify(status)


@bp.route('/api/camera/<int:camera_id>/card')
def api_camera_card(camera_id: int):
    """Get camera card HTML partial (for HTMX)."""
    camera = get_camera_with_settings(camera_id)
    if not camera:
        return "", 404

    camera['stream_active'] = is_stream_active(str(camera_id))
    camera['stream_urls'] = get_stream_urls(str(camera_id), get_system_ip())

    return render_template('partials/camera_card.html', camera=camera)


# ============ Camera Detail ============

@bp.route('/<int:camera_id>')
def camera_detail(camera_id: int):
    """Camera detail page."""
    camera = get_camera_with_settings(camera_id)
    if not camera:
        flash("Camera not found", "error")
        return redirect(url_for('cameras.dashboard'))

    camera['stream_active'] = is_stream_active(str(camera_id))
    camera['stream_urls'] = get_stream_urls(str(camera_id), get_system_ip())

    # Get capabilities for dropdowns
    caps = get_camera_capabilities(camera_id)
    capabilities = caps['capabilities'] if caps else {}

    # Build resolution options from capabilities
    resolutions = []
    if camera['settings'] and camera['settings'].get('format'):
        fmt = camera['settings']['format']
        if fmt in capabilities:
            resolutions = list(capabilities[fmt].keys())

    if not resolutions:
        resolutions = COMMON_RESOLUTIONS

    # Get encoders
    encoders = detect_encoders()

    return render_template(
        'camera_detail.html',
        camera=camera,
        capabilities=capabilities,
        resolutions=resolutions,
        framerates=COMMON_FRAMERATES,
        encoders=encoders,
        system_ip=get_system_ip()
    )


@bp.route('/<int:camera_id>/settings', methods=['POST'])
def update_settings(camera_id: int):
    """Update camera settings."""
    camera = get_camera_by_id(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    # Get form data
    settings = {}

    if 'resolution' in request.form:
        settings['resolution'] = request.form['resolution']
    if 'framerate' in request.form:
        settings['framerate'] = int(request.form['framerate'])
    if 'format' in request.form:
        settings['format'] = request.form['format']
    if 'encoder' in request.form:
        settings['encoder'] = request.form['encoder']
    if 'bitrate' in request.form:
        settings['bitrate'] = request.form['bitrate']
    if 'rotation' in request.form:
        settings['rotation'] = int(request.form['rotation'])

    # Save settings
    save_camera_settings(camera_id, settings)
    add_log("INFO", f"Settings updated for camera {camera['friendly_name']}", camera_id)

    # Rebuild and update stream
    if camera['connected'] and camera['enabled']:
        current_settings = get_camera_settings(camera_id)
        if current_settings and camera['device_path']:
            ffmpeg_cmd = build_ffmpeg_command(
                camera['device_path'],
                current_settings,
                str(camera_id),
                current_settings.get('encoder', 'libx264')
            )
            add_or_update_stream(str(camera_id), ffmpeg_cmd)

    # HTMX response
    if request.headers.get('HX-Request'):
        return render_template('partials/settings_success.html')

    flash("Settings updated successfully", "success")
    return redirect(url_for('cameras.camera_detail', camera_id=camera_id))


@bp.route('/<int:camera_id>/enable', methods=['POST'])
def toggle_enable(camera_id: int):
    """Enable or disable a camera."""
    camera = get_camera_by_id(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    # Toggle enabled state
    new_state = not camera['enabled']
    update_camera(camera_id, enabled=new_state)

    action = "enabled" if new_state else "disabled"
    add_log("INFO", f"Camera {camera['friendly_name']} {action}", camera_id)

    if request.headers.get('HX-Request'):
        return render_template('partials/enable_button.html',
                             camera_id=camera_id, enabled=new_state)

    flash(f"Camera {action}", "success")
    return redirect(url_for('cameras.camera_detail', camera_id=camera_id))


@bp.route('/<int:camera_id>/rename', methods=['POST'])
def rename_camera(camera_id: int):
    """Rename a camera."""
    camera = get_camera_by_id(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    new_name = request.form.get('friendly_name', '').strip()
    if not new_name:
        if request.headers.get('HX-Request'):
            return "Name cannot be empty", 400
        flash("Name cannot be empty", "error")
        return redirect(url_for('cameras.camera_detail', camera_id=camera_id))

    old_name = camera['friendly_name']
    update_camera(camera_id, friendly_name=new_name)
    add_log("INFO", f"Camera renamed from '{old_name}' to '{new_name}'", camera_id)

    if request.headers.get('HX-Request'):
        return new_name

    flash("Camera renamed successfully", "success")
    return redirect(url_for('cameras.camera_detail', camera_id=camera_id))


@bp.route('/<int:camera_id>/restart', methods=['POST'])
def restart_camera_stream(camera_id: int):
    """Restart camera stream."""
    camera = get_camera_by_id(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    success, error = restart_stream(str(camera_id))

    if success:
        add_log("INFO", f"Stream restarted for camera {camera['friendly_name']}", camera_id)
        message = "Stream restarted"
    else:
        add_log("WARNING", f"Failed to restart stream: {error}", camera_id)
        message = f"Failed to restart: {error}"

    if request.headers.get('HX-Request'):
        return message

    flash(message, "success" if success else "error")
    return redirect(url_for('cameras.camera_detail', camera_id=camera_id))


# ============ Snapshots ============

@bp.route('/snapshot/<camera_id>.jpg')
def snapshot(camera_id: str):
    """Get JPEG snapshot for a camera."""
    # Handle both numeric IDs and string IDs
    try:
        cam_id = int(camera_id)
        camera = get_camera_by_id(cam_id)
        if camera and camera['connected']:
            jpeg_data = grab_snapshot(str(cam_id))
            if jpeg_data:
                return Response(jpeg_data, mimetype='image/jpeg')
    except ValueError:
        # String ID - try to grab snapshot directly
        jpeg_data = grab_snapshot(camera_id)
        if jpeg_data:
            return Response(jpeg_data, mimetype='image/jpeg')

    # Return placeholder
    return Response(get_placeholder_image(), mimetype='image/jpeg')


# ============ Global Settings ============

@bp.route('/settings')
def settings_page():
    """Global settings page."""
    settings = get_all_settings()
    platform_info = get_platform_info()
    encoders = detect_encoders()
    cpu_rating = estimate_cpu_capability()

    return render_template(
        'settings.html',
        settings=settings,
        platform_info=platform_info,
        encoders=encoders,
        cpu_rating=cpu_rating,
        moonraker_available=moonraker_available()
    )


@bp.route('/settings', methods=['POST'])
def update_global_settings():
    """Update global settings."""
    if 'cpu_threshold' in request.form:
        set_setting('cpu_threshold', int(request.form['cpu_threshold']))

    if 'moonraker_url' in request.form:
        set_setting('moonraker_url', request.form['moonraker_url'])

    if 'log_level' in request.form:
        set_setting('log_level', request.form['log_level'])

    add_log("INFO", "Global settings updated")

    if request.headers.get('HX-Request'):
        return render_template('partials/settings_success.html')

    flash("Settings saved", "success")
    return redirect(url_for('cameras.settings_page'))


# ============ Logs ============

@bp.route('/logs')
def logs_page():
    """Log viewer page."""
    level = request.args.get('level', None)
    page = int(request.args.get('page', 1))
    per_page = 50

    logs = get_logs(
        limit=per_page,
        offset=(page - 1) * per_page,
        level=level
    )

    return render_template(
        'logs.html',
        logs=logs,
        current_level=level,
        page=page
    )


@bp.route('/api/logs')
def api_logs():
    """Get logs as JSON."""
    level = request.args.get('level', None)
    limit = int(request.args.get('limit', 50))

    logs = get_logs(limit=limit, level=level)
    return jsonify(logs)


# ============ API Endpoints ============

@bp.route('/api/resolutions/<int:camera_id>')
def api_resolutions(camera_id: int):
    """Get available resolutions for a camera format."""
    fmt = request.args.get('format', 'mjpeg')

    caps = get_camera_capabilities(camera_id)
    if caps and caps['capabilities']:
        capabilities = caps['capabilities']
        if fmt in capabilities:
            resolutions = list(capabilities[fmt].keys())
            return jsonify(resolutions)

    return jsonify(COMMON_RESOLUTIONS)


@bp.route('/api/framerates/<int:camera_id>')
def api_framerates(camera_id: int):
    """Get available framerates for a camera resolution."""
    fmt = request.args.get('format', 'mjpeg')
    resolution = request.args.get('resolution', '1280x720')

    caps = get_camera_capabilities(camera_id)
    if caps and caps['capabilities']:
        capabilities = caps['capabilities']
        if fmt in capabilities and resolution in capabilities[fmt]:
            framerates = capabilities[fmt][resolution]
            return jsonify(sorted(framerates))

    return jsonify(COMMON_FRAMERATES)


@bp.route('/api/system')
def api_system():
    """Get system information."""
    return jsonify({
        'platform': get_platform_info(),
        'encoders': detect_encoders(),
        'cpu_rating': estimate_cpu_capability(),
        'system_ip': get_system_ip(),
        'moonraker_available': moonraker_available(),
    })
