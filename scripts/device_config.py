#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
device_config.py
----------------
Device configuration module for Ravens Perch.
Handles camera selection, format/resolution/FPS configuration.

Philosophy:
- Saves preferences to raven_settings.yml (source of truth)
- Configures MediaMTX via API (ephemeral)
- Uses camera UID as MediaMTX path name

Last modified: 2026-01-12
"""

import re

# Import from common utilities
from common import (
    FORMAT_PRIORITY,
    COLOR_CYAN, COLOR_HIGH, COLOR_MED, COLOR_LOW, COLOR_YELLOW, COLOR_RESET,
    DEFAULT_CAMERA_CONFIG,
    clear_screen, get_system_ip, sanitize_camera_name,
    list_video_devices, get_device_names, get_audio_devices,
    get_all_video_devices, get_device_serial,
    run_v4l2ctl, parse_formats,
    get_v4l2_controls,
    build_ffmpeg_cmd, apply_v4l2_controls,
    detect_hardware_acceleration,
    mediamtx_api_available, add_or_update_mediamtx_path,
    load_raven_settings, save_raven_settings,
    get_all_cameras, find_camera_by_hardware, find_cameras_by_hardware,
    create_camera_config, save_camera_config, deep_copy,
    check_for_duplicate_cameras
)

# ===== FORMAT/RESOLUTION SELECTION =====

def select_best_format_auto(formats_by_type, preferred_res="1280x720"):
    """Automatic selection based on priority"""
    for fmt in FORMAT_PRIORITY:
        if fmt not in formats_by_type:
            continue

        resolutions = formats_by_type[fmt]
        resolution = (
            preferred_res if preferred_res in resolutions else
            sorted(resolutions, key=lambda r: tuple(map(int, r.split('x'))), reverse=True)[0]
        )
        fps = max(resolutions[resolution])
        return fmt, resolution, fps

    return None, None, None

def display_camera_options(device, formats_by_type, device_name=None):
    """Display all available format/resolution options for a camera"""
    print(f"\n{'='*70}")
    if device_name:
        print(f"📹 Camera: {device_name}")
        print(f"   Device: {device}")
    else:
        print(f"📹 Camera: {device}")
    print(f"{'='*70}")
    
    if not formats_by_type:
        print("❌ No supported formats found!")
        return None
    
    print(f"\n{COLOR_YELLOW}⚠️  Note: Cameras may not support all listed options.{COLOR_RESET}")
    print(f"{COLOR_YELLOW}   If your feed doesn't work, try different format/resolution/FPS.{COLOR_RESET}\n")
    
    print(f"{'Opt':>3} | {'Format':^8} | {'Resolution':^12} | {'FPS'}")
    print(f"{'-'*3}-+-{'-'*8}-+-{'-'*12}-+-{'-'*10}")
    
    options = []
    option_num = 1
    
    for fmt in FORMAT_PRIORITY:
        if fmt not in formats_by_type:
            continue
        
        resolutions = formats_by_type[fmt]
        sorted_res = sorted(resolutions.keys(), 
                          key=lambda r: tuple(map(int, r.split('x'))), 
                          reverse=True)
        
        for res in sorted_res:
            fps_list = sorted(resolutions[res], reverse=True)
            
            options.append({
                'num': option_num,
                'format': fmt,
                'resolution': res,
                'fps_list': fps_list
            })
            
            # Quality indicator
            width = int(res.split('x')[0])
            if width >= 1920:
                quality = f"{COLOR_HIGH}HD 1080p+{COLOR_RESET}"
            elif width >= 1280:
                quality = f"{COLOR_MED}HD 720p{COLOR_RESET}"
            elif width >= 640:
                quality = f"{COLOR_LOW}SD 480p{COLOR_RESET}"
            else:
                quality = "Low"
            
            fps_str = "/".join(map(str, fps_list[:3]))
            if len(fps_list) > 3:
                fps_str += "/..."
            
            print(f"{option_num:>3} | {fmt:^8} | {res:^12} | {fps_str}")
            option_num += 1
    
    return options

def select_format_resolution(options):
    """Let user select format/resolution from options"""
    while True:
        choice = input(f"\n{COLOR_CYAN}Select option number (or 'a' for auto, 's' to skip):{COLOR_RESET} ").strip().lower()
        
        if choice == 'a':
            return 'auto'
        if choice == 's':
            return 'skip'
        
        try:
            num = int(choice)
            for opt in options:
                if opt['num'] == num:
                    return opt
        except ValueError:
            pass
        
        print("❌ Invalid selection")

def select_fps(fps_list):
    """Let user select FPS from available options"""
    if len(fps_list) == 1:
        return fps_list[0]
    
    print(f"\nAvailable FPS: {', '.join(map(str, fps_list))}")
    
    while True:
        choice = input(f"{COLOR_CYAN}Select FPS (Enter for {fps_list[0]}):{COLOR_RESET} ").strip()
        
        if not choice:
            return fps_list[0]
        
        try:
            fps = int(choice)
            if fps in fps_list:
                return fps
            print(f"❌ FPS {fps} not available")
        except ValueError:
            print("❌ Invalid number")

def select_output_fps(capture_fps):
    """Optionally select a lower output FPS for frame dropping"""
    print(f"\n{COLOR_CYAN}Output Frame Rate Options:{COLOR_RESET}")
    print(f"   Capture FPS: {capture_fps}")
    print(f"\n   You can reduce output FPS to save CPU/bandwidth.")
    print(f"   This drops frames after capture but before encoding.")
    
    common_fps = [5, 10, 15, 20, 30]
    available = [f for f in common_fps if f < capture_fps]
    
    if not available:
        print(f"\n   No lower FPS options available.")
        return None
    
    print(f"\n   Available lower FPS: {', '.join(map(str, available))}")
    choice = input(f"\n{COLOR_CYAN}Output FPS (Enter to keep {capture_fps}):{COLOR_RESET} ").strip()
    
    if not choice:
        return capture_fps
    
    try:
        fps = int(choice)
        if fps in available:
            return fps
        elif fps == capture_fps:
            return capture_fps
        print(f"❌ FPS {fps} not available, using capture FPS")
        return capture_fps
    except ValueError:
        return capture_fps

# ===== FRIENDLY NAME HANDLING =====

def prompt_for_friendly_name(device_name, existing_names=None):
    """Prompt user for a friendly camera name"""
    existing_names = existing_names or []
    default = sanitize_camera_name(device_name)
    
    # Ensure uniqueness
    if default in existing_names:
        counter = 2
        while f"{default}_{counter}" in existing_names:
            counter += 1
        default = f"{default}_{counter}"
    
    print(f"\n{COLOR_CYAN}Enter friendly name for '{device_name}'")
    print(f"(Press Enter for '{default}'):{COLOR_RESET}")
    
    name = input().strip()
    
    if not name:
        return default
    
    # Sanitize user input
    name = sanitize_camera_name(name)
    
    # Ensure uniqueness
    if name in existing_names:
        counter = 2
        while f"{name}_{counter}" in existing_names:
            counter += 1
        name = f"{name}_{counter}"
    
    return name

# ===== DUPLICATE CAMERA HANDLING =====

def handle_existing_camera(settings, hardware_name, serial_number):
    """
    Handle case where a camera with this hardware_name and serial_number already exists.
    
    Note: Creating duplicate entries (same hardware_name AND serial_number) is not allowed.
    Users can only reconfigure existing entries or skip.
    
    Returns:
        Tuple of (camera_config, is_new, action)
        action is 'configure' or 'skip'
    """
    matches = find_cameras_by_hardware(settings, hardware_name, serial_number)
    
    if not matches:
        return None, True, 'new'
    
    if len(matches) == 1:
        cam, idx = matches[0]
        friendly = cam.get("friendly_name", hardware_name)
        
        print(f"\n{COLOR_YELLOW}Found existing configuration for '{friendly}':{COLOR_RESET}")
        print(f"   UID: {cam.get('uid')}")
        
        capture = cam.get("mediamtx", {}).get("ffmpeg", {}).get("capture", {})
        if capture:
            print(f"   Current: {capture.get('format')} {capture.get('resolution')} @ {capture.get('framerate')} fps")
        
        print(f"\n   [1] Reconfigure this camera")
        print(f"   [2] Skip (keep current settings)")
        
        while True:
            choice = input(f"\n{COLOR_CYAN}Select option:{COLOR_RESET} ").strip()
            
            if choice == '1':
                return cam, False, 'configure'
            elif choice == '2':
                return cam, False, 'skip'
            else:
                print("❌ Invalid option")
    
    # Multiple matches in YAML - this shouldn't happen, but handle it
    # User must select one to update or skip entirely
    print(f"\n{COLOR_YELLOW}Found {len(matches)} configurations for '{hardware_name}':{COLOR_RESET}")
    print(f"   (Multiple entries for the same camera is not supported)")
    
    for i, (cam, idx) in enumerate(matches, 1):
        friendly = cam.get("friendly_name", "Unknown")
        uid = cam.get("uid", "?")
        print(f"   [{i}] Update: {friendly} (UID: {uid})")
    
    print(f"   [s] Skip this device")
    
    while True:
        choice = input(f"\n{COLOR_CYAN}Select option:{COLOR_RESET} ").strip().lower()
        
        if choice == 's':
            return None, False, 'skip'
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(matches):
                cam, _ = matches[idx]
                return cam, False, 'configure'
        except ValueError:
            pass
        
        print("❌ Invalid option")

# ===== MAIN CONFIGURATION =====

def configure_devices(auto_mode=False):
    """
    Main device configuration workflow.
    
    Args:
        auto_mode: If True, automatically configure all cameras with default settings
        
    Returns:
        bool: True if cameras were configured
    """
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("📹 Configure MediaMTX Cameras")
    print(f"{'='*70}{COLOR_RESET}")
    
    # Get system IP
    system_ip = get_system_ip()
    print(f"\n🌐 System IP: {system_ip}")
    
    # Detect hardware acceleration
    print("🔍 Detecting hardware acceleration...")
    use_vaapi, use_v4l2m2m = detect_hardware_acceleration()
    
    if use_vaapi:
        print(f"   ✅ VAAPI hardware encoding available")
    elif use_v4l2m2m:
        print(f"   ✅ V4L2 M2M hardware encoding available (Raspberry Pi)")
    else:
        print(f"   ⚠️  No hardware acceleration available (will use software encoding)")
    
    # Check MediaMTX API
    api_available = mediamtx_api_available()
    if api_available:
        print(f"   ✅ MediaMTX API available")
    else:
        print(f"   {COLOR_YELLOW}⚠️  MediaMTX API not available - configuration will be saved but not applied{COLOR_RESET}")
    
    # Load settings
    settings = load_raven_settings()
    if settings is None:
        print(f"\n{COLOR_LOW}❌ Failed to load raven_settings.yml{COLOR_RESET}")
        input("\nPress Enter to continue...")
        return False
    
    # Get video devices
    devices = get_all_video_devices()
    
    if not devices:
        print(f"\n{COLOR_LOW}❌ No video devices found!{COLOR_RESET}")
        input("\nPress Enter to continue...")
        return False
    
    print(f"\n📹 Found {len(devices)} video device(s)")
    
    # Check for non-compliant duplicate cameras
    has_duplicates, warning_msg, duplicate_keys = check_for_duplicate_cameras(devices)
    if has_duplicates:
        print(f"\n{warning_msg}")
        input("\nPress Enter to acknowledge and continue...")
    
    # Filter out duplicate cameras
    valid_devices = [
        d for d in devices 
        if (d['hardware_name'], d['serial_number']) not in duplicate_keys
    ]
    
    if not valid_devices:
        print(f"\n{COLOR_LOW}❌ No configurable cameras remaining after filtering duplicates!{COLOR_RESET}")
        input("\nPress Enter to continue...")
        return False
    
    if len(valid_devices) < len(devices):
        print(f"\n   Proceeding with {len(valid_devices)} configurable camera(s)")
    
    # Track configured cameras
    configured_cameras = []
    skipped_cameras = []
    existing_friendly_names = [c.get("friendly_name") for c in get_all_cameras(settings)]
    
    # Process each device
    for dev_info in valid_devices:
        dev_path = dev_info['path']
        dev_name = dev_info['hardware_name']
        serial = dev_info['serial_number']
        
        clear_screen()
        print(f"\n{COLOR_CYAN}{'='*70}")
        print(f"📹 Configuring: {dev_name}")
        print(f"{'='*70}{COLOR_RESET}")
        print(f"   Device: {dev_path}")
        if serial:
            print(f"   Serial: {serial}")
        
        # Check for existing configuration
        camera_config, is_new, action = handle_existing_camera(settings, dev_name, serial)
        
        if action == 'skip':
            skipped_cameras.append({'device': dev_path, 'name': dev_name, 'reason': 'Skipped by user'})
            continue
        
        # Get device formats
        output = run_v4l2ctl(dev_path, ["--list-formats-ext"])
        formats = parse_formats(output)
        
        if not formats:
            print(f"\n{COLOR_LOW}❌ Could not detect formats for {dev_name}{COLOR_RESET}")
            skipped_cameras.append({'device': dev_path, 'name': dev_name, 'reason': 'No formats detected'})
            input("\nPress Enter to continue...")
            continue
        
        # Auto mode or manual selection
        if auto_mode:
            fmt, res, fps = select_best_format_auto(formats)
            if not fmt:
                print(f"❌ Could not auto-select format for {dev_name}")
                skipped_cameras.append({'device': dev_path, 'name': dev_name, 'reason': 'Auto-select failed'})
                continue
            output_fps = fps
        else:
            # Display options
            options = display_camera_options(dev_path, formats, dev_name)
            if not options:
                skipped_cameras.append({'device': dev_path, 'name': dev_name, 'reason': 'No options'})
                input("\nPress Enter to continue...")
                continue
            
            # Select format/resolution
            selection = select_format_resolution(options)
            
            if selection == 'skip':
                skipped_cameras.append({'device': dev_path, 'name': dev_name, 'reason': 'Skipped by user'})
                continue
            
            if selection == 'auto':
                fmt, res, fps = select_best_format_auto(formats)
                if not fmt:
                    print(f"❌ Could not auto-select format")
                    skipped_cameras.append({'device': dev_path, 'name': dev_name, 'reason': 'Auto-select failed'})
                    continue
            else:
                fmt = selection['format']
                res = selection['resolution']
                fps = select_fps(selection['fps_list'])
            
            # Output FPS selection
            output_fps = select_output_fps(fps)
            
            print(f"\n✅ Selected: {fmt} {res} @ {fps} fps", end="")
            if output_fps and output_fps != fps:
                print(f" → {output_fps} fps output")
            else:
                print()
        
        # Create or update camera config
        if is_new:
            friendly_name = prompt_for_friendly_name(dev_name, existing_friendly_names)
            camera_config = create_camera_config(dev_name, friendly_name, serial)
            existing_friendly_names.append(friendly_name)
        else:
            friendly_name = camera_config.get("friendly_name", dev_name)
        
        # Update capture settings
        if "mediamtx" not in camera_config:
            camera_config["mediamtx"] = deep_copy(DEFAULT_CAMERA_CONFIG["mediamtx"])
        
        capture = camera_config["mediamtx"]["ffmpeg"]["capture"]
        capture["format"] = fmt
        capture["resolution"] = res
        capture["framerate"] = fps
        
        # Update encoding settings
        encoding = camera_config["mediamtx"]["ffmpeg"]["encoding"]
        encoding["output_fps"] = output_fps or fps
        
        if use_vaapi:
            encoding["encoder"] = "vaapi"
        elif use_v4l2m2m:
            encoding["encoder"] = "v4l2m2m"
        else:
            encoding["encoder"] = "libx264"
        
        # Update device capabilities
        from common import update_camera_capabilities
        success, error = update_camera_capabilities(camera_config, dev_path)
        if success:
            print(f"   📋 Capabilities recorded")
        elif error:
            print(f"   {COLOR_YELLOW}⚠️  Could not record capabilities: {error}{COLOR_RESET}")
        
        # Save to settings
        settings = save_camera_config(settings, camera_config)
        
        # Track configured camera
        configured_cameras.append({
            'uid': camera_config.get('uid'),
            'device': dev_path,
            'device_name': dev_name,
            'friendly_name': friendly_name,
            'format': fmt,
            'resolution': res,
            'fps': fps,
            'output_fps': output_fps
        })
    
    # Save settings to file
    if configured_cameras:
        save_raven_settings(settings)
        print(f"\n💾 Saved configuration to raven_settings.yml")
    
    # Apply to MediaMTX if API available
    if api_available and configured_cameras:
        print(f"\n📡 Applying configuration to MediaMTX...")
        
        for cam in configured_cameras:
            uid = cam['uid']
            
            # Get full camera config
            camera_config, _ = find_camera_by_hardware(settings, cam['device_name'])
            if not camera_config:
                continue
            
            # Build settings dict for FFmpeg command
            encoding = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("encoding", {})
            audio = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("audio", {})
            
            ffmpeg_settings = {
                'bitrate': encoding.get("bitrate", "4M"),
                'encoder_preset': encoding.get("preset", "ultrafast"),
                'rotation': encoding.get("rotation", 0),
                'output_fps': encoding.get("output_fps"),
                'enable_audio': audio.get("enabled", False),
                'audio_device': audio.get("device"),
                'audio_codec': audio.get("codec", "aac"),
            }
            
            # Build FFmpeg command
            ffmpeg_cmd = build_ffmpeg_cmd(
                cam['device'],
                cam['format'],
                cam['resolution'],
                cam['fps'],
                uid,  # Use UID as path name
                use_vaapi,
                use_v4l2m2m,
                ffmpeg_settings
            )
            
            # Apply V4L2 controls if any (independent from stream)
            v4l2_controls = camera_config.get("v4l2-ctl", {})
            if v4l2_controls:
                apply_v4l2_controls(cam['device'], v4l2_controls)
            
            # Configure MediaMTX path
            mtx_config = {
                "source": "publisher",
                "runOnInit": ffmpeg_cmd,
                "runOnInitRestart": True
            }
            
            success, action, error = add_or_update_mediamtx_path(uid, mtx_config)
            
            if success:
                print(f"   ✅ {uid} ({cam['friendly_name']})")
            else:
                print(f"   ❌ {uid} ({cam['friendly_name']}): {error}")
    
    # Summary
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("📊 Configuration Summary")
    print(f"{'='*70}{COLOR_RESET}")
    
    if configured_cameras:
        print(f"\n✅ Configured {len(configured_cameras)} camera(s):")
        for cam in configured_cameras:
            print(f"   - {cam['friendly_name']} ({cam['uid']})")
            print(f"     {cam['format']} {cam['resolution']} @ {cam['output_fps']} fps")
            print(f"     RTSP: rtsp://{system_ip}:8554/{cam['uid']}")
            print(f"     WebRTC: http://{system_ip}:8889/{cam['uid']}/")
    
    if skipped_cameras:
        print(f"\n⏭️  Skipped {len(skipped_cameras)} camera(s):")
        for cam in skipped_cameras:
            print(f"   - {cam['name']}: {cam['reason']}")
    
    if not configured_cameras and not skipped_cameras:
        print("\n⚠️  No cameras configured")
    
    input("\nPress Enter to continue...")
    
    return len(configured_cameras) > 0
