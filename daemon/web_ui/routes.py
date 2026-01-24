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
    get_camera_with_settings, get_camera_by_id, get_camera_by_hardware_id,
    update_camera, save_camera_settings, get_camera_settings,
    get_camera_capabilities, get_logs, get_all_settings,
    set_setting, add_log, delete_camera_completely, delete_all_cameras,
    ignore_camera, unignore_camera, get_ignored_cameras, is_camera_ignored,
    create_camera, save_camera_capabilities, mark_camera_connected
)
from ..snapshot_server import grab_snapshot, get_placeholder_image
from ..stream_manager import (
    build_ffmpeg_command, add_or_update_stream, get_stream_urls,
    is_stream_active, restart_stream, remove_stream, remove_all_streams
)
from ..moonraker_client import (
    register_camera, update_camera as update_moonraker_camera,
    unregister_camera as unregister_moonraker_camera,
    build_stream_url, build_snapshot_url, get_system_ip, is_available as moonraker_available
)
from ..hardware import estimate_cpu_capability, detect_encoders, get_platform_info
from ..camera_manager import find_video_devices, get_device_info, probe_capabilities, auto_configure
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


@bp.route('/scan', methods=['POST'])
def scan_cameras():
    """Scan for and add connected cameras."""
    try:
        devices = find_video_devices()
        added = 0
        updated = 0

        for device_path in devices:
            device_info = get_device_info(device_path)
            if not device_info:
                continue

            # Check if camera is ignored
            if is_camera_ignored(device_info.hardware_id):
                continue

            # Check if camera already exists
            existing = get_camera_by_hardware_id(device_info.hardware_id)
            if existing:
                # Update connection status
                mark_camera_connected(existing['id'], device_path)
                updated += 1
                continue

            # Probe capabilities
            capabilities = probe_capabilities(device_path)
            if not capabilities:
                continue

            # Auto-configure settings
            current_count = len(get_all_cameras())
            settings = auto_configure(capabilities, current_count + 1)

            # Create camera
            camera_id = create_camera(
                hardware_name=device_info.hardware_name,
                serial_number=device_info.serial_number,
                device_path=device_path
            )

            # Save settings and capabilities
            save_camera_settings(camera_id, settings)
            save_camera_capabilities(camera_id, capabilities)

            added += 1
            add_log("INFO", f"Added camera: {device_info.hardware_name}", camera_id)

        if added > 0 or updated > 0:
            flash(f"Found {added} new camera(s), updated {updated} existing", "success")
        else:
            flash("No new cameras found", "info")

    except Exception as e:
        logger.error(f"Error scanning for cameras: {e}")
        flash(f"Error scanning: {e}", "error")

    return redirect(url_for('cameras.dashboard'))


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

    # Build current FFmpeg command for display
    ffmpeg_cmd = None
    if camera['connected'] and camera['device_path'] and camera['settings']:
        settings = camera['settings']
        encoder = settings.get('encoder') or 'libx264'
        ffmpeg_cmd = build_ffmpeg_command(
            camera['device_path'],
            settings,
            str(camera_id),
            encoder
        )

    return render_template(
        'camera_detail.html',
        camera=camera,
        capabilities=capabilities,
        resolutions=resolutions,
        framerates=COMMON_FRAMERATES,
        encoders=encoders,
        system_ip=get_system_ip(),
        ffmpeg_cmd=ffmpeg_cmd
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

    # Update Moonraker webcam name if registered
    if camera.get('moonraker_uid') and moonraker_available():
        # Unregister old webcam and re-register with new name
        unregister_moonraker_camera(camera['moonraker_uid'])
        host = get_system_ip()
        stream_url = build_stream_url(str(camera_id), host)
        snapshot_url = build_snapshot_url(str(camera_id), host)
        settings = get_camera_settings(camera_id) or {}
        rotation = settings.get('rotation', 0)

        success, new_uid, _ = register_camera(
            str(camera_id),
            new_name,
            stream_url,
            snapshot_url,
            rotation=rotation
        )
        if success and new_uid:
            update_camera(camera_id, moonraker_uid=new_uid)

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


@bp.route('/<int:camera_id>/delete', methods=['POST'])
def delete_camera(camera_id: int):
    """Delete a camera from the database."""
    camera = get_camera_by_id(camera_id)
    if not camera:
        flash("Camera not found", "error")
        return redirect(url_for('cameras.dashboard'))

    camera_name = camera['friendly_name']
    hardware_id = camera.get('hardware_id')

    # Stop stream if running
    if camera['connected']:
        remove_stream(str(camera_id))

    # Unregister from Moonraker
    if camera.get('moonraker_uid'):
        unregister_moonraker_camera(camera['moonraker_uid'])

    # Check if we should also ignore
    also_ignore = request.form.get('also_ignore') == 'true'

    # Delete from database
    success, deleted_hardware_id = delete_camera_completely(camera_id)

    if success:
        add_log("INFO", f"Deleted camera: {camera_name}")

        if also_ignore and deleted_hardware_id:
            ignore_camera(deleted_hardware_id, camera_name, "Deleted by user")
            flash(f"Camera '{camera_name}' deleted and added to ignore list", "success")
        else:
            flash(f"Camera '{camera_name}' deleted", "success")
    else:
        flash("Failed to delete camera", "error")

    return redirect(url_for('cameras.dashboard'))


@bp.route('/<int:camera_id>/ignore', methods=['POST'])
def ignore_camera_route(camera_id: int):
    """Delete a camera and add it to the ignore list."""
    camera = get_camera_by_id(camera_id)
    if not camera:
        flash("Camera not found", "error")
        return redirect(url_for('cameras.dashboard'))

    camera_name = camera['friendly_name']
    hardware_id = camera.get('hardware_id')

    # Stop stream if running
    if camera['connected']:
        remove_stream(str(camera_id))

    # Unregister from Moonraker
    if camera.get('moonraker_uid'):
        unregister_moonraker_camera(camera['moonraker_uid'])

    # Add to ignore list first
    if hardware_id:
        ignore_camera(hardware_id, camera_name, "Ignored by user")

    # Delete from database
    success, _ = delete_camera_completely(camera_id)

    if success:
        add_log("INFO", f"Ignored camera: {camera_name}")
        flash(f"Camera '{camera_name}' will now be ignored", "success")
    else:
        flash("Failed to ignore camera", "error")

    return redirect(url_for('cameras.dashboard'))


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


@bp.route('/start-fresh', methods=['POST'])
def start_fresh():
    """Remove all cameras and settings, re-detect connected cameras."""
    try:
        # Remove all streams from MediaMTX
        streams_removed = remove_all_streams()
        logger.info(f"Removed {streams_removed} streams from MediaMTX")

        # Unregister all cameras from Moonraker
        if moonraker_available():
            for camera in get_all_cameras():
                if camera.get('moonraker_uid'):
                    unregister_moonraker_camera(camera['moonraker_uid'])

        # Delete all cameras from database
        cameras_deleted = delete_all_cameras()
        logger.info(f"Deleted {cameras_deleted} cameras from database")

        add_log("INFO", f"Start Fresh: Removed {cameras_deleted} cameras. Restart service to re-detect.")

        flash(f"Removed {cameras_deleted} cameras. Restart the service to re-detect connected cameras.", "success")

    except Exception as e:
        logger.error(f"Error during Start Fresh: {e}")
        add_log("ERROR", f"Start Fresh failed: {e}")
        flash(f"Error: {e}", "error")

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
        else:
            resolutions = COMMON_RESOLUTIONS
    else:
        resolutions = COMMON_RESOLUTIONS

    # Return HTML options for HTMX requests
    if request.headers.get('HX-Request'):
        options = ''.join(f'<option value="{res}">{res}</option>' for res in resolutions)
        return options

    return jsonify(resolutions)


@bp.route('/api/framerates/<int:camera_id>')
def api_framerates(camera_id: int):
    """Get available framerates for a camera resolution."""
    fmt = request.args.get('format', 'mjpeg')
    resolution = request.args.get('resolution', '1280x720')

    caps = get_camera_capabilities(camera_id)
    if caps and caps['capabilities']:
        capabilities = caps['capabilities']
        if fmt in capabilities and resolution in capabilities[fmt]:
            framerates = sorted(capabilities[fmt][resolution])
        else:
            framerates = COMMON_FRAMERATES
    else:
        framerates = COMMON_FRAMERATES

    # Return HTML options for HTMX requests
    if request.headers.get('HX-Request'):
        options = ''.join(f'<option value="{fps}">{fps} fps</option>' for fps in framerates)
        return options

    return jsonify(framerates)


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
