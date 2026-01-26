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
    is_stream_active, restart_stream, remove_stream, remove_all_streams,
    start_camera_stream
)
from ..moonraker_client import (
    register_camera, update_camera as update_moonraker_camera,
    unregister_camera as unregister_moonraker_camera,
    build_stream_url, build_snapshot_url, get_system_ip, is_available as moonraker_available,
    detect_klipper_ui_theme
)
from ..hardware import estimate_cpu_capability, detect_encoders, get_platform_info, clear_encoder_cache
from ..camera_manager import (
    find_video_devices, get_device_info, probe_capabilities, auto_configure,
    get_v4l2_controls, set_v4l2_control, get_v4l2_control_value,
    get_rejected_cameras
)
from ..bandwidth import get_camera_bandwidth_stats
from ..print_status import get_monitor as get_print_monitor
from ..config import COMMON_RESOLUTIONS, COMMON_FRAMERATES

logger = logging.getLogger(__name__)

bp = Blueprint('cameras', __name__)


# ============ The Raven (Edgar Allan Poe, 1845) ============
# Public domain poem displayed in footer, two lines at a time

RAVEN_LINES = [
    "Once upon a midnight dreary, while I pondered, weak and weary,",
    "Over many a quaint and curious volume of forgotten lore—",
    "While I nodded, nearly napping, suddenly there came a tapping,",
    "As of some one gently rapping, rapping at my chamber door.",
    "'Tis some visitor,' I muttered, 'tapping at my chamber door—",
    "Only this and nothing more.'",
    "Ah, distinctly I remember it was in the bleak December;",
    "And each separate dying ember wrought its ghost upon the floor.",
    "Eagerly I wished the morrow;—vainly I had sought to borrow",
    "From my books surcease of sorrow—sorrow for the lost Lenore—",
    "For the rare and radiant maiden whom the angels name Lenore—",
    "Nameless here for evermore.",
    "And the silken, sad, uncertain rustling of each purple curtain",
    "Thrilled me—filled me with fantastic terrors never felt before;",
    "So that now, to still the beating of my heart, I stood repeating",
    "'Tis some visitor entreating entrance at my chamber door—",
    "Some late visitor entreating entrance at my chamber door;—",
    "This it is and nothing more.'",
    "Presently my soul grew stronger; hesitating then no longer,",
    "'Sir,' said I, 'or Madam, truly your forgiveness I implore;",
    "But the fact is I was napping, and so gently you came rapping,",
    "And so faintly you came tapping, tapping at my chamber door,",
    "That I scarce was sure I heard you'—here I opened wide the door;—",
    "Darkness there and nothing more.",
    "Deep into that darkness peering, long I stood there wondering, fearing,",
    "Doubting, dreaming dreams no mortal ever dared to dream before;",
    "But the silence was unbroken, and the stillness gave no token,",
    "And the only word there spoken was the whispered word, 'Lenore?'",
    "This I whispered, and an echo murmured back the word, 'Lenore!'—",
    "Merely this and nothing more.",
    "Back into the chamber turning, all my soul within me burning,",
    "Soon again I heard a tapping somewhat louder than before.",
    "'Surely,' said I, 'surely that is something at my window lattice;",
    "Let me see, then, what thereat is, and this mystery explore—",
    "Let my heart be still a moment and this mystery explore;—",
    "'Tis the wind and nothing more!'",
    "Open here I flung the shutter, when, with many a flirt and flutter,",
    "In there stepped a stately Raven of the saintly days of yore;",
    "Not the least obeisance made he; not a minute stopped or stayed he;",
    "But, with mien of lord or lady, perched above my chamber door—",
    "Perched upon a bust of Pallas just above my chamber door—",
    "Perched, and sat, and nothing more.",
    "Then this ebony bird beguiling my sad fancy into smiling,",
    "By the grave and stern decorum of the countenance it wore,",
    "'Though thy crest be shorn and shaven, thou,' I said, 'art sure no craven,",
    "Ghastly grim and ancient Raven wandering from the Nightly shore—",
    "Tell me what thy lordly name is on the Night's Plutonian shore!'",
    "Quoth the Raven 'Nevermore.'",
    "Much I marvelled this ungainly fowl to hear discourse so plainly,",
    "Though its answer little meaning—little relevancy bore;",
    "For we cannot help agreeing that no living human being",
    "Ever yet was blessed with seeing bird above his chamber door—",
    "Bird or beast upon the sculptured bust above his chamber door,",
    "With such name as 'Nevermore.'",
    "But the Raven, sitting lonely on the placid bust, spoke only",
    "That one word, as if his soul in that one word he did outpour.",
    "Nothing farther then he uttered—not a feather then he fluttered—",
    "Till I scarcely more than muttered 'Other friends have flown before—",
    "On the morrow he will leave me, as my Hopes have flown before.'",
    "Then the bird said 'Nevermore.'",
    "Startled at the stillness broken by reply so aptly spoken,",
    "'Doubtless,' said I, 'what it utters is its only stock and store",
    "Caught from some unhappy master whom unmerciful Disaster",
    "Followed fast and followed faster till his songs one burden bore—",
    "Till the dirges of his Hope that melancholy burden bore",
    "Of \"Never—nevermore.\"'",
    "But the Raven still beguiling all my fancy into smiling,",
    "Straight I wheeled a cushioned seat in front of bird, and bust and door;",
    "Then, upon the velvet sinking, I betook myself to linking",
    "Fancy unto fancy, thinking what this ominous bird of yore—",
    "What this grim, ungainly, ghastly, gaunt, and ominous bird of yore",
    "Meant in croaking 'Nevermore.'",
    "This I sat engaged in guessing, but no syllable expressing",
    "To the fowl whose fiery eyes now burned into my bosom's core;",
    "This and more I sat divining, with my head at ease reclining",
    "On the cushion's velvet lining that the lamp-light gloated o'er,",
    "But whose velvet-violet lining with the lamp-light gloating o'er,",
    "She shall press, ah, nevermore!",
    "Then, methought, the air grew denser, perfumed from an unseen censer",
    "Swung by Seraphim whose foot-falls tinkled on the tufted floor.",
    "'Wretch,' I cried, 'thy God hath lent thee—by these angels he hath sent thee",
    "Respite—respite and nepenthe from thy memories of Lenore;",
    "Quaff, oh quaff this kind nepenthe and forget this lost Lenore!'",
    "Quoth the Raven 'Nevermore.'",
    "'Prophet!' said I, 'thing of evil!—prophet still, if bird or devil!—",
    "Whether Tempter sent, or whether tempest tossed thee here ashore,",
    "Desolate yet all undaunted, on this desert land enchanted—",
    "On this home by Horror haunted—tell me truly, I implore—",
    "Is there—is there balm in Gilead?—tell me—tell me, I implore!'",
    "Quoth the Raven 'Nevermore.'",
    "'Prophet!' said I, 'thing of evil!—prophet still, if bird or devil!",
    "By that Heaven that bends above us—by that God we both adore—",
    "Tell this soul with sorrow laden if, within the distant Aidenn,",
    "It shall clasp a sainted maiden whom the angels name Lenore—",
    "Clasp a rare and radiant maiden whom the angels name Lenore.'",
    "Quoth the Raven 'Nevermore.'",
    "'Be that word our sign of parting, bird or fiend!' I shrieked, upstarting—",
    "'Get thee back into the tempest and the Night's Plutonian shore!",
    "Leave no black plume as a token of that lie thy soul hath spoken!",
    "Leave my loneliness unbroken!—quit the bust above my door!",
    "Take thy beak from out my heart, and take thy form from off my door!'",
    "Quoth the Raven 'Nevermore.'",
    "And the Raven, never flitting, still is sitting, still is sitting",
    "On the pallid bust of Pallas just above my chamber door;",
    "And his eyes have all the seeming of a demon's that is dreaming,",
    "And the lamp-light o'er him streaming throws his shadow on the floor;",
    "And my soul from out that shadow that lies floating on the floor",
    "Shall be lifted—nevermore!",
]


def get_raven_couplet():
    """Get the next two lines from The Raven and advance the position."""
    position = get_all_settings().get('raven_position', 0)
    try:
        position = int(position)
    except (ValueError, TypeError):
        position = 0

    # Ensure position is valid and even (we read pairs)
    if position < 0 or position >= len(RAVEN_LINES):
        position = 0
    if position % 2 != 0:
        position = position - 1

    # Get two lines
    line1 = RAVEN_LINES[position]
    line2 = RAVEN_LINES[position + 1] if position + 1 < len(RAVEN_LINES) else ""

    # Advance position for next time (wrap around)
    next_position = position + 2
    if next_position >= len(RAVEN_LINES):
        next_position = 0
    set_setting('raven_position', next_position)

    return line1, line2


# ============ Color Utilities ============

def darken_color(hex_color: str, factor: float = 0.15) -> str:
    """Darken a hex color by a factor (0-1)."""
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r = int(r * (1 - factor))
    g = int(g * (1 - factor))
    b = int(b * (1 - factor))
    return f'#{r:02x}{g:02x}{b:02x}'


def get_contrast_text_color(hex_color: str) -> str:
    """Return black or white text color based on background luminance."""
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    # Calculate relative luminance using sRGB coefficients
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return '#000000' if luminance > 0.5 else '#ffffff'


@bp.context_processor
def inject_accent_color():
    """Inject accent color into all templates."""
    settings = get_all_settings()
    accent = settings.get('accent_color')
    if accent:
        # Generate hover color (slightly darker)
        hover = darken_color(accent, 0.15)
        # Generate contrasting text color for readability
        text = get_contrast_text_color(accent)
        return {
            'accent_color': accent,
            'accent_color_hover': hover,
            'accent_text_color': text
        }
    return {}


@bp.context_processor
def inject_raven_couplet():
    """Inject the current Raven couplet into all templates."""
    line1, line2 = get_raven_couplet()
    return {'raven_line1': line1, 'raven_line2': line2}


def detect_printer_uis():
    """
    Detect which printer UIs (Mainsail/Fluidd) are configured in nginx.
    Returns dict with 'mainsail' and 'fluidd' keys containing port number or None.
    """
    import os
    import re

    result = {'mainsail': None, 'fluidd': None}

    # Common nginx config locations
    nginx_paths = [
        '/etc/nginx/sites-enabled',
        '/etc/nginx/sites-available',
        '/etc/nginx/conf.d'
    ]

    for nginx_dir in nginx_paths:
        if not os.path.isdir(nginx_dir):
            continue

        for filename in os.listdir(nginx_dir):
            filepath = os.path.join(nginx_dir, filename)
            if not os.path.isfile(filepath):
                continue

            try:
                with open(filepath, 'r') as f:
                    content = f.read().lower()

                    # Check for Mainsail
                    if 'mainsail' in content or 'mainsail' in filename.lower():
                        # Try to extract port from listen directive
                        listen_match = re.search(r'listen\s+(\d+)', content)
                        port = listen_match.group(1) if listen_match else '80'
                        result['mainsail'] = port

                    # Check for Fluidd
                    if 'fluidd' in content or 'fluidd' in filename.lower():
                        listen_match = re.search(r'listen\s+(\d+)', content)
                        port = listen_match.group(1) if listen_match else '80'
                        result['fluidd'] = port

            except (IOError, PermissionError):
                continue

    return result


@bp.context_processor
def inject_printer_uis():
    """Inject detected printer UIs into all templates."""
    return {'printer_uis': detect_printer_uis()}


# ============ Dynamic CSS for Fluidd ============

# ============ Dashboard ============

@bp.route('/')
def dashboard():
    """Camera dashboard - main page."""
    cameras = get_all_cameras_with_settings()

    # Add stream status to each camera
    for camera in cameras:
        camera['stream_active'] = is_stream_active(str(camera['id']))
        camera['stream_urls'] = get_stream_urls(str(camera['id']), get_system_ip())

    # Get any rejected cameras (e.g., duplicates)
    rejected = get_rejected_cameras()

    return render_template(
        'dashboard.html',
        cameras=cameras,
        rejected_cameras=rejected,
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

            # Start the stream
            ffmpeg_cmd = build_ffmpeg_command(
                device_path,
                settings,
                str(camera_id),
                settings.get('encoder', 'libx264')
            )
            add_or_update_stream(str(camera_id), ffmpeg_cmd)

            # Register with Moonraker
            if moonraker_available():
                camera = get_camera_by_id(camera_id)
                if camera:
                    host = get_system_ip()
                    stream_url = build_stream_url(str(camera_id), host)
                    snapshot_url = build_snapshot_url(str(camera_id), host)
                    rotation = settings.get('rotation', 0)

                    success, uid, _ = register_camera(
                        str(camera_id),
                        camera['friendly_name'],
                        stream_url,
                        snapshot_url,
                        rotation=rotation
                    )
                    if success and uid:
                        update_camera(camera_id, moonraker_uid=uid)

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


@bp.route('/api/health')
def api_health():
    """Simple health check endpoint for install verification."""
    return jsonify({'status': 'ok'})


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
        settings = camera['settings'].copy()  # Copy to avoid modifying original
        encoder = settings.get('encoder') or 'libx264'

        # Get overlay path only if enabled
        overlay_path = None
        print_monitor = get_print_monitor()
        if settings.get('overlay_enabled') and print_monitor:
            overlay_path = str(print_monitor.get_overlay_path(str(camera_id)))

        # Apply standby framerate if enabled and printer is idle
        if settings.get('standby_enabled') and settings.get('standby_framerate') and print_monitor:
            if print_monitor.effective_state == 'standby':
                settings['framerate'] = settings['standby_framerate']

        ffmpeg_cmd = build_ffmpeg_command(
            camera['device_path'],
            settings,
            str(camera_id),
            encoder,
            overlay_path=overlay_path
        )

    return render_template(
        'camera_detail.html',
        camera=camera,
        capabilities=capabilities,
        resolutions=resolutions,
        framerates=COMMON_FRAMERATES,
        encoders=encoders,
        system_ip=get_system_ip(),
        ffmpeg_cmd=ffmpeg_cmd,
        settings=get_all_settings()
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

    # Print integration settings
    if 'overlay_enabled' in request.form:
        # Check if '1' is in the list of values (checkbox + hidden input)
        settings['overlay_enabled'] = '1' in request.form.getlist('overlay_enabled')

    # Overlay customization
    if 'overlay_font_size' in request.form:
        settings['overlay_font_size'] = int(request.form['overlay_font_size'])
    if 'overlay_position' in request.form:
        settings['overlay_position'] = request.form['overlay_position']
    if 'overlay_color' in request.form:
        settings['overlay_color'] = request.form['overlay_color']
    if 'overlay_font' in request.form:
        settings['overlay_font'] = request.form['overlay_font'] or None
    if 'overlay_multiline' in request.form:
        settings['overlay_multiline'] = '1' in request.form.getlist('overlay_multiline')
    if 'overlay_show_labels' in request.form:
        settings['overlay_show_labels'] = '1' in request.form.getlist('overlay_show_labels')

    # Overlay stat toggles
    overlay_stats = [
        'overlay_show_progress', 'overlay_show_layer', 'overlay_show_eta',
        'overlay_show_elapsed', 'overlay_show_filename', 'overlay_show_hotend_temp',
        'overlay_show_bed_temp', 'overlay_show_fan_speed', 'overlay_show_print_state',
        'overlay_show_filament_used', 'overlay_show_current_time',
        'overlay_show_print_speed', 'overlay_show_z_height',
        'overlay_show_live_velocity', 'overlay_show_flow_rate',
        'overlay_show_filament_type'
    ]
    for stat in overlay_stats:
        if stat in request.form:
            settings[stat] = request.form[stat] == '1'

    # V4L2 controls from form (prefixed with 'v4l2_')
    # Only save values that differ from hardware defaults
    v4l2_controls = {}
    hardware_defaults = {}
    if camera['connected'] and camera['device_path']:
        try:
            hw_controls = get_v4l2_controls(camera['device_path'])
            hardware_defaults = {name: info.get('default') for name, info in hw_controls.items()}
        except Exception:
            pass  # If we can't get defaults, save all values

    for key in request.form:
        if key.startswith('v4l2_'):
            control_name = key[5:]  # Remove 'v4l2_' prefix
            try:
                value = int(request.form[key])
                # Only save if different from hardware default
                if control_name not in hardware_defaults or value != hardware_defaults[control_name]:
                    v4l2_controls[control_name] = value
            except (ValueError, TypeError):
                pass  # Skip invalid values
    # Always set v4l2_controls (even if empty) to clear out old defaults
    settings['v4l2_controls'] = v4l2_controls

    if 'standby_enabled' in request.form:
        # Check if '1' is in the list of values (checkbox + hidden input)
        settings['standby_enabled'] = '1' in request.form.getlist('standby_enabled')
        if settings['standby_enabled'] and 'standby_framerate' in request.form:
            val = request.form['standby_framerate']
            settings['standby_framerate'] = int(val) if val else None
        elif not settings['standby_enabled']:
            settings['standby_framerate'] = None

    # Handle global overlay update interval
    if 'overlay_update_interval' in request.form:
        interval = int(request.form['overlay_update_interval'])
        interval = max(1, min(10, interval))
        set_setting('overlay_update_interval', interval)
        print_monitor = get_print_monitor()
        if print_monitor:
            print_monitor.set_poll_interval(float(interval))

    # Save settings
    save_camera_settings(camera_id, settings)
    add_log("INFO", f"Settings updated for camera {camera['friendly_name']}", camera_id)

    # Update print monitor overlay setting if changed
    print_monitor = get_print_monitor()
    if print_monitor:
        current_settings = get_camera_settings(camera_id)
        if current_settings and current_settings.get('overlay_enabled'):
            print_monitor.set_camera_overlay(str(camera_id), True, current_settings)
        elif 'overlay_enabled' in settings:
            print_monitor.set_camera_overlay(str(camera_id), False)

    # Rebuild and update stream using the helper function
    if camera['connected'] and camera['enabled']:
        current_settings = get_camera_settings(camera_id)
        if current_settings and camera['device_path']:
            # Apply standby framerate if enabled and printer is idle
            if current_settings.get('standby_enabled') and current_settings.get('standby_framerate') and print_monitor:
                if print_monitor.effective_state == 'standby':
                    current_settings['framerate'] = current_settings['standby_framerate']

            # Start stream (applies v4l2 controls, builds command, starts stream)
            start_camera_stream(
                camera['device_path'],
                str(camera_id),
                current_settings,
                print_monitor
            )

    # HTMX response - include updated FFmpeg command for OOB swap
    if request.headers.get('HX-Request'):
        # Get the current ffmpeg command to update the Info tab
        ffmpeg_cmd = None
        if camera['connected'] and camera['enabled'] and camera['device_path']:
            current_settings = get_camera_settings(camera_id) or {}
            overlay_path = None
            if current_settings.get('overlay_enabled') and print_monitor:
                overlay_path = str(print_monitor.get_overlay_path(str(camera_id)))
            ffmpeg_cmd = build_ffmpeg_command(
                camera['device_path'],
                current_settings,
                str(camera_id),
                current_settings.get('encoder', 'libx264'),
                overlay_path=overlay_path
            )
        return render_template('partials/settings_success.html', ffmpeg_cmd=ffmpeg_cmd)

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
    camera = get_camera_with_settings(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    if not camera['connected'] or not camera['device_path']:
        message = "Camera not connected"
        if request.headers.get('HX-Request'):
            return message
        flash(message, "error")
        return redirect(url_for('cameras.camera_detail', camera_id=camera_id))

    # Rebuild FFmpeg command with current settings
    settings = camera['settings'] or {}
    v4l2_controls = settings.get('v4l2_controls') or {}
    print_monitor = get_print_monitor()

    # Apply V4L2 controls first (these are already filtered to non-defaults)
    if v4l2_controls:
        from ..camera_manager import apply_v4l2_controls
        apply_v4l2_controls(camera['device_path'], v4l2_controls)

    # Get overlay path only if enabled
    overlay_path = None
    if settings.get('overlay_enabled') and print_monitor:
        overlay_path = str(print_monitor.get_overlay_path(str(camera_id)))

    # Apply standby framerate if enabled and printer is idle
    if settings.get('standby_enabled') and settings.get('standby_framerate') and print_monitor:
        if print_monitor.effective_state == 'standby':
            settings['framerate'] = settings['standby_framerate']

    ffmpeg_cmd = build_ffmpeg_command(
        camera['device_path'],
        settings,
        str(camera_id),
        settings.get('encoder', 'libx264'),
        overlay_path=overlay_path
    )

    # Force restart since user explicitly requested it
    success, error = add_or_update_stream(str(camera_id), ffmpeg_cmd, force=True)

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
    if 'moonraker_url' in request.form:
        set_setting('moonraker_url', request.form['moonraker_url'])

    if 'log_level' in request.form:
        set_setting('log_level', request.form['log_level'])

    # Appearance settings
    if 'accent_color' in request.form:
        accent_color = request.form['accent_color'].upper()
        # Validate hex color format
        if accent_color and accent_color.startswith('#') and len(accent_color) == 7:
            set_setting('accent_color', accent_color)
        elif not accent_color:
            # Clear the setting if empty
            set_setting('accent_color', None)

    if 'custom_accent_color' in request.form:
        custom_color = request.form['custom_accent_color'].upper()
        if custom_color and custom_color.startswith('#') and len(custom_color) == 7:
            set_setting('custom_accent_color', custom_color)

    add_log("INFO", "Global settings updated")

    if request.headers.get('HX-Request'):
        return render_template('partials/settings_success.html')

    flash("Settings saved", "success")
    return redirect(url_for('cameras.settings_page'))


@bp.route('/redetect-encoders', methods=['POST'])
def redetect_encoders():
    """Clear encoder cache and re-detect hardware encoders."""
    clear_encoder_cache()
    encoders = detect_encoders(force=True)
    encoder_list = [k for k, v in encoders.items() if v]
    add_log("INFO", f"Re-detected encoders: {encoder_list}")

    if request.headers.get('HX-Request'):
        return f'<span class="alert alert-success">Encoders re-detected: {", ".join(encoder_list)}</span>'

    flash(f"Encoders re-detected: {', '.join(encoder_list)}", "success")
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


# ============ Help ============

@bp.route('/help')
def help_page():
    """Help and documentation page."""
    return render_template('help.html')


@bp.route('/troubleshooting')
def troubleshooting_page():
    """Troubleshooting and diagnostics page."""
    # Build the diagnostic command that outputs to a file
    diagnostic_command = """(
echo "=== Ravens Perch Diagnostic Report ==="
echo "Generated: $(date)"
echo ""
echo "=== System Information ==="
cat /etc/os-release 2>/dev/null || echo "OS info not available"
echo ""
uname -a
echo ""
echo "CPU:"
cat /proc/cpuinfo | grep -E "^(model name|Hardware)" | head -2
echo ""
echo "Memory:"
free -h
echo ""
echo "Disk:"
df -h /
echo ""
echo "=== Video Devices ==="
v4l2-ctl --list-devices 2>&1
echo ""
echo "=== Device Capabilities ==="
for dev in /dev/video*; do
    if udevadm info "$dev" 2>/dev/null | grep -q ':capture:'; then
        echo "--- $dev ---"
        udevadm info "$dev" 2>/dev/null | grep -E "(ID_MODEL|ID_SERIAL|ID_V4L_CAPABILITIES)"
        v4l2-ctl -d "$dev" --list-formats-ext 2>&1 | head -30
    fi
done
echo ""
echo "=== FFmpeg ==="
ffmpeg -version 2>&1 | head -3
echo ""
echo "Encoders:"
ffmpeg -encoders 2>/dev/null | grep -E "264|265|hevc"
echo ""
echo "Hardware acceleration:"
ffmpeg -hwaccels 2>&1
echo ""
echo "=== Running Processes ==="
echo "FFmpeg:"
ps aux | grep [f]fmpeg
echo ""
echo "MediaMTX:"
ps aux | grep [m]ediamtx
echo ""
echo "=== MediaMTX Status ==="
curl -s http://localhost:9997/v3/paths/list 2>/dev/null | head -50 || echo "MediaMTX API not responding"
echo ""
echo "=== Service Status ==="
systemctl status ravens-perch --no-pager 2>&1 | head -20
echo ""
echo "=== Recent Logs ==="
journalctl -u ravens-perch --no-pager -n 100 2>&1 || echo "No service logs available"
echo ""
echo "=== USB Devices ==="
lsusb
echo ""
lsusb -t
echo ""
echo "=== Kernel Messages (video) ==="
dmesg | grep -iE "(video|uvc|usb)" | tail -30
echo ""
echo "=== Network Ports ==="
ss -tlnp 2>/dev/null | grep -E "(8554|8889|9997|7125|5000)" || netstat -tlnp 2>/dev/null | grep -E "(8554|8889|9997|7125|5000)"
echo ""
echo "=== End of Diagnostic Report ==="
) > ~/ravens-perch-diagnostic.txt 2>&1 && echo "Diagnostic saved to ~/ravens-perch-diagnostic.txt\""""

    return render_template('troubleshooting.html', diagnostic_command=diagnostic_command)


# ============ API Endpoints ============

@bp.route('/api/resolutions/<int:camera_id>')
def api_resolutions(camera_id: int):
    """Get available resolutions for a camera format."""
    fmt = request.args.get('format', 'mjpeg')
    current_resolution = request.args.get('resolution', '')

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
        # Try to preserve current selection, otherwise select first
        preserved = current_resolution in resolutions
        selected_resolution = current_resolution if preserved else (resolutions[0] if resolutions else '')

        options = []
        for res in resolutions:
            selected = 'selected' if res == selected_resolution else ''
            options.append(f'<option value="{res}" {selected}>{res}</option>')

        # Add HX-Trigger header to notify if selection changed
        response = ''.join(options)
        headers = {}
        if not preserved and current_resolution:
            headers['HX-Trigger'] = 'selectionChanged'
        return response, 200, headers

    return jsonify(resolutions)


@bp.route('/api/framerates/<int:camera_id>')
def api_framerates(camera_id: int):
    """Get available framerates for a camera resolution."""
    fmt = request.args.get('format', 'mjpeg')
    resolution = request.args.get('resolution', '1280x720')
    current_framerate = request.args.get('framerate', '')
    current_standby = request.args.get('standby_framerate', '')

    # Convert to int for comparison if provided
    try:
        current_framerate_int = int(current_framerate) if current_framerate else None
    except ValueError:
        current_framerate_int = None

    try:
        current_standby_int = int(current_standby) if current_standby else None
    except ValueError:
        current_standby_int = None

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
        # Try to preserve current selection, otherwise select first
        preserved = current_framerate_int in framerates
        selected_framerate = current_framerate_int if preserved else (framerates[0] if framerates else None)

        options = []
        for fps in framerates:
            selected = 'selected' if fps == selected_framerate else ''
            options.append(f'<option value="{fps}" {selected}>{fps} fps</option>')

        # Also build options for standby framerate dropdown (out-of-band swap)
        standby_preserved = current_standby_int in framerates
        selected_standby = current_standby_int if standby_preserved else (framerates[0] if framerates else None)

        standby_options = []
        for fps in framerates:
            selected = 'selected' if fps == selected_standby else ''
            standby_options.append(f'<option value="{fps}" {selected}>{fps} fps</option>')

        # Return both dropdowns - main one targeted, standby via OOB swap
        response = ''.join(options)
        response += f'<select id="standby_framerate" name="standby_framerate" hx-swap-oob="innerHTML">'
        response += ''.join(standby_options)
        response += '</select>'

        headers = {}
        if not preserved and current_framerate_int is not None:
            headers['HX-Trigger'] = 'selectionChanged'
        return response, 200, headers

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


@bp.route('/api/bandwidth')
def api_bandwidth():
    """Get bandwidth statistics for all cameras."""
    cameras = get_all_cameras_with_settings()
    stats = {}

    for camera in cameras:
        camera_id = str(camera['id'])
        if camera['connected']:
            stats[camera_id] = get_camera_bandwidth_stats(camera)
        else:
            stats[camera_id] = None

    return jsonify(stats)


@bp.route('/api/bandwidth/<int:camera_id>')
def api_bandwidth_camera(camera_id: int):
    """Get bandwidth statistics for a specific camera."""
    camera = get_camera_with_settings(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    if not camera['connected']:
        return jsonify({'error': 'Camera not connected'}), 400

    stats = get_camera_bandwidth_stats(camera)
    return jsonify(stats)


# ============ Camera Status API ============

@bp.route('/api/status/<int:camera_id>')
def api_camera_status(camera_id: int):
    """Get camera status badge HTML for HTMX polling."""
    camera = get_camera_by_id(camera_id)
    if not camera:
        return '<span class="status-badge status-offline">Unknown</span>'

    # Check if stream is active
    camera['stream_active'] = is_stream_active(str(camera_id))

    return render_template('partials/camera_status_badge.html', camera=camera)


# ============ V4L2 Controls API ============

@bp.route('/api/controls/<int:camera_id>')
def api_get_controls(camera_id: int):
    """Get available V4L2 controls for a camera."""
    camera = get_camera_by_id(camera_id)
    if not camera:
        if request.headers.get('HX-Request'):
            return '<p class="form-help">Camera not found</p>'
        return jsonify({'error': 'Camera not found'}), 404

    if not camera['connected'] or not camera['device_path']:
        if request.headers.get('HX-Request'):
            return '<p class="form-help">Camera not connected</p>'
        return jsonify({'error': 'Camera not connected'}), 400

    try:
        # Get available controls from the camera
        controls = get_v4l2_controls(camera['device_path'])

        # Get saved control values from database
        settings = get_camera_settings(camera_id)
        saved_controls = (settings.get('v4l2_controls') or {}) if settings else {}

        # Merge saved values with available controls
        for name, info in controls.items():
            if name in saved_controls:
                info['saved'] = saved_controls[name]

        # Return HTML for HTMX requests
        if request.headers.get('HX-Request'):
            if not controls:
                return '<p class="form-help">No adjustable controls available for this camera.</p>'
            return render_template('partials/v4l2_controls.html',
                                 camera_id=camera_id,
                                 controls=controls)

        return jsonify(controls)

    except Exception as e:
        logger.error(f"Error getting V4L2 controls: {e}")
        if request.headers.get('HX-Request'):
            return f'<p class="form-help" style="color: var(--error);">Error loading controls: {e}</p>'
        return jsonify({'error': str(e)}), 500


@bp.route('/api/controls/<int:camera_id>/<control_name>', methods=['POST'])
def api_set_control(camera_id: int, control_name: str):
    """Set a V4L2 control value and apply it immediately."""
    camera = get_camera_by_id(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    if not camera['connected'] or not camera['device_path']:
        return jsonify({'error': 'Camera not connected'}), 400

    # Get value from request (try form data first, then JSON)
    value = request.form.get('value')
    if value is None:
        data = request.get_json() or {}
        value = data.get('value')

    if value is None:
        return jsonify({'error': 'Value required'}), 400

    try:
        value = int(value)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid value'}), 400

    # Apply immediately to camera
    success = set_v4l2_control(camera['device_path'], control_name, value)

    if not success:
        return jsonify({'error': 'Failed to apply control'}), 500

    # Save to database (only if different from hardware default)
    settings = get_camera_settings(camera_id) or {}
    v4l2_controls = settings.get('v4l2_controls', {}) or {}

    # Get hardware default for this control
    try:
        hw_controls = get_v4l2_controls(camera['device_path'])
        default_value = hw_controls.get(control_name, {}).get('default')
    except Exception:
        default_value = None

    if default_value is not None and value == default_value:
        # Value matches default - remove from saved settings
        v4l2_controls.pop(control_name, None)
    else:
        # Value differs from default - save it
        v4l2_controls[control_name] = value

    save_camera_settings(camera_id, {'v4l2_controls': v4l2_controls})

    # Get the actual current value from camera to confirm
    actual_value = get_v4l2_control_value(camera['device_path'], control_name)

    add_log("INFO", f"Set {control_name}={value} for camera {camera['friendly_name']}", camera_id)

    return jsonify({
        'success': True,
        'control': control_name,
        'value': value,
        'actual': actual_value
    })


@bp.route('/api/controls/<int:camera_id>/<control_name>/preview', methods=['POST'])
def api_preview_control(camera_id: int, control_name: str):
    """Apply a V4L2 control value for preview only (no database save).

    This allows users to see the effect of control changes in real-time
    without committing them. The actual save happens with the form submission.
    """
    camera = get_camera_by_id(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    if not camera['connected'] or not camera['device_path']:
        return jsonify({'error': 'Camera not connected'}), 400

    # Get value from request (try form data first, then JSON)
    value = request.form.get('value')
    if value is None:
        data = request.get_json() or {}
        value = data.get('value')

    if value is None:
        return jsonify({'error': 'Value required'}), 400

    try:
        value = int(value)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid value'}), 400

    # Apply to camera for preview only - no database save
    success = set_v4l2_control(camera['device_path'], control_name, value)

    if not success:
        return jsonify({'error': 'Failed to apply control'}), 500

    # Get the actual current value from camera to confirm
    actual_value = get_v4l2_control_value(camera['device_path'], control_name)

    return jsonify({
        'success': True,
        'control': control_name,
        'value': value,
        'actual': actual_value
    })


@bp.route('/api/controls/<int:camera_id>/<control_name>/reset', methods=['POST'])
def api_reset_control(camera_id: int, control_name: str):
    """Reset a V4L2 control to its default value (preview only, no save).

    This applies the default value for preview. The actual save happens
    with the form submission.
    """
    camera = get_camera_by_id(camera_id)
    if not camera:
        return jsonify({'error': 'Camera not found'}), 404

    if not camera['connected'] or not camera['device_path']:
        return jsonify({'error': 'Camera not connected'}), 400

    # Get control info to find default value
    controls = get_v4l2_controls(camera['device_path'])
    if control_name not in controls:
        return jsonify({'error': 'Control not found'}), 404

    default_value = controls[control_name].get('default')
    if default_value is None:
        return jsonify({'error': 'No default value available'}), 400

    # Apply default value for preview only - no database save
    success = set_v4l2_control(camera['device_path'], control_name, default_value)

    if not success:
        return jsonify({'error': 'Failed to reset control'}), 500

    return jsonify({
        'success': True,
        'control': control_name,
        'value': default_value
    })


# ============ Print Status Diagnostic ============

@bp.route('/api/print-status')
def api_print_status():
    """Get current print status for debugging."""
    monitor = get_print_monitor()
    if not monitor:
        return jsonify({
            'error': 'Print monitor not initialized',
            'moonraker_available': False
        })

    status = monitor.status
    return jsonify({
        'moonraker_available': True,
        'state': status.state,
        'is_printing': status.is_printing,
        'progress': status.progress,
        'filename': status.filename,
        'current_layer': status.current_layer,
        'total_layers': status.total_layers,
        'time_elapsed': status.time_elapsed,
        'time_remaining': status.time_remaining,
        'hotend_temp': status.hotend_temp,
        'hotend_target': status.hotend_target,
        'bed_temp': status.bed_temp,
        'bed_target': status.bed_target,
        'fan_speed': status.fan_speed,
        'print_speed': status.print_speed,
        'z_height': status.z_height,
        'filament_used': status.filament_used,
        'live_velocity': status.live_velocity,
        'flow_rate': status.flow_rate,
        'filament_type': status.filament_type,
        'cameras_with_overlay': list(monitor._camera_overlays.keys()),
        'overlay_dir': str(monitor.overlay_dir),
    })


# ============ System Fonts ============

@bp.route('/api/detect-theme')
def api_detect_theme():
    """Detect Mainsail/Fluidd theme colors from Moonraker."""
    settings = get_all_settings()
    moonraker_url = settings.get('moonraker_url', 'http://127.0.0.1:7125')
    themes = detect_klipper_ui_theme(moonraker_url)
    return jsonify(themes)


@bp.route('/api/reset-poem', methods=['POST'])
def api_reset_poem():
    """Reset The Raven poem to the beginning."""
    set_setting('raven_position', 0)
    return jsonify({'success': True, 'message': 'Poem reset to beginning'})


@bp.route('/api/fonts')
def api_fonts():
    """Get list of available system fonts."""
    import subprocess
    fonts = []

    try:
        # Use fc-list to get system fonts
        result = subprocess.run(
            ['fc-list', '-f', '%{family}\n'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            # Parse and deduplicate font families
            font_set = set()
            for line in result.stdout.strip().split('\n'):
                if line:
                    # Take first family name if comma-separated
                    family = line.split(',')[0].strip()
                    if family:
                        font_set.add(family)
            fonts = sorted(font_set)
    except FileNotFoundError:
        logger.warning("fc-list not found - font selection unavailable")
    except subprocess.TimeoutExpired:
        logger.warning("fc-list timed out")
    except Exception as e:
        logger.error(f"Error listing fonts: {e}")

    # Return HTML select for HTMX requests
    if request.headers.get('HX-Request'):
        # Get current font from query param if provided
        current_font = request.args.get('current', '')

        options = ['<option value="">System Default</option>']
        for font in fonts:
            selected = ' selected' if font == current_font else ''
            options.append(f'<option value="{font}"{selected}>{font}</option>')

        return f'<select id="overlay_font" name="overlay_font">{"".join(options)}</select>'

    return jsonify(fonts)
