#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
quick_config.py
---------------
Quick auto-configuration module for Ravens Perch.
Automatically detects cameras, tests encoding capabilities, and configures
MediaMTX and Moonraker with optimal settings based on hardware capability.

Philosophy:
- Saves preferences to raven_settings.yml (source of truth)
- Configures MediaMTX/Moonraker via API (ephemeral)
- Uses camera UID as path name (4-char alphanumeric)
- Moonraker camera names use format: {uid}_{friendly_name}

Last modified: 2026-01-12
"""

import subprocess
import re
import time

from common import (
    COLOR_CYAN, COLOR_HIGH, COLOR_MED, COLOR_LOW, COLOR_YELLOW, COLOR_RESET,
    clear_screen, get_system_ip,
    get_all_video_devices, get_device_serial,
    run_v4l2ctl, parse_formats,
    build_ffmpeg_cmd, measure_cpu_usage, get_cpu_core_count,
    detect_hardware_acceleration,
    mediamtx_api_available, add_or_update_mediamtx_path, delete_mediamtx_path,
    list_mediamtx_paths, cleanup_our_mediamtx_paths,
    load_raven_settings, save_raven_settings,
    create_camera_config, save_camera_config, get_all_cameras,
    find_camera_by_hardware, check_for_duplicate_cameras,
    sanitize_camera_name, deep_copy, DEFAULT_CAMERA_CONFIG,
    detect_moonraker_url, moonraker_api_available,
    get_moonraker_webcams, add_moonraker_webcam, delete_moonraker_webcam,
    get_our_moonraker_cameras, truncate_friendly_name
)

# ===== HARDWARE CAPABILITY FUNCTIONS =====

def prompt_hardware_capability():
    """
    Ask user to rate their system's hardware capability on a scale of 1-10.
    
    Returns:
        int: Capability rating 1-10
    """
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("⚡ Hardware Capability Assessment")
    print(f"{'='*70}{COLOR_RESET}")
    
    print("\nRate your system's processing power on a scale of 1-10:")
    print("")
    print("  [1-2]   Low-end SBC (Raspberry Pi 3, Orange Pi Zero)")
    print("  [3-4]   Mid-range SBC (Raspberry Pi 4, Orange Pi 5)")
    print("  [5-6]   High-end SBC or low-end x86 (RK3588, old laptop)")
    print("  [7-8]   Modern x86 system (recent Intel/AMD)")
    print("  [9-10]  Powerful system with GPU acceleration")
    
    while True:
        try:
            choice = input(f"\n{COLOR_CYAN}Enter capability (1-10):{COLOR_RESET} ").strip()
            capability = int(choice)
            if 1 <= capability <= 10:
                return capability
            print("❌ Please enter a number between 1 and 10")
        except ValueError:
            print("❌ Please enter a valid number")

def estimate_cpu_capability():
    """
    Estimate CPU capability based on core count and measure baseline.
    
    Returns:
        int: Estimated capability 1-10
    """
    cores = get_cpu_core_count()
    
    # Rough estimation based on cores
    if cores <= 2:
        base = 3
    elif cores <= 4:
        base = 5
    elif cores <= 6:
        base = 7
    else:
        base = 8
    
    # Measure baseline CPU to adjust
    baseline = measure_cpu_usage(duration=2.0)
    
    # If system is already busy, reduce estimate
    if baseline > 50:
        base = max(1, base - 2)
    elif baseline > 30:
        base = max(1, base - 1)
    
    return min(10, max(1, base))

def get_quality_specs(capability, num_cameras):
    """
    Get target quality specs based on capability and camera count.
    Quality is reduced for multiple cameras.
    
    Returns:
        dict with target_resolution, target_fps, max_pixels, fps_weight
    """
    # Base tiers for single camera
    tiers = {
        1:  {"target_res": "320x240",   "target_fps": 5,  "max_pixels": 76800,    "fps_weight": 0.3},
        2:  {"target_res": "320x240",   "target_fps": 10, "max_pixels": 76800,    "fps_weight": 0.4},
        3:  {"target_res": "640x480",   "target_fps": 10, "max_pixels": 307200,   "fps_weight": 0.4},
        4:  {"target_res": "640x480",   "target_fps": 15, "max_pixels": 307200,   "fps_weight": 0.5},
        5:  {"target_res": "800x600",   "target_fps": 15, "max_pixels": 480000,   "fps_weight": 0.5},
        6:  {"target_res": "1280x720",  "target_fps": 15, "max_pixels": 921600,   "fps_weight": 0.5},
        7:  {"target_res": "1280x720",  "target_fps": 30, "max_pixels": 921600,   "fps_weight": 0.6},
        8:  {"target_res": "1920x1080", "target_fps": 30, "max_pixels": 2073600,  "fps_weight": 0.6},
        9:  {"target_res": "1920x1080", "target_fps": 45, "max_pixels": 2073600,  "fps_weight": 0.7},
        10: {"target_res": "1920x1080", "target_fps": 60, "max_pixels": 8294400,  "fps_weight": 0.8},
    }
    
    # Reduce capability for multiple cameras
    if num_cameras > 1:
        reduction = num_cameras - 1  # Reduce by 1 tier per additional camera
        capability = max(1, capability - reduction)
    
    return tiers.get(capability, tiers[5])

# ===== FORMAT SELECTION =====

def find_best_format(formats_by_type, target_res, target_fps):
    """
    Find the best format/resolution/fps combination for a camera.
    
    Prefers:
    1. MJPEG (lowest CPU decode)
    2. Closest resolution to target
    3. Closest FPS to target
    """
    best = None
    best_score = -1
    
    priority_formats = ["mjpeg", "h264", "yuyv422", "nv12"]
    
    for fmt in priority_formats:
        if fmt not in formats_by_type:
            continue
        
        for res, fps_list in formats_by_type[fmt].items():
            # Parse resolution
            try:
                w, h = map(int, res.split('x'))
                pixels = w * h
            except:
                continue
            
            # Find best FPS
            best_fps = min(fps_list, key=lambda x: abs(x - target_fps))
            if abs(best_fps - target_fps) > 15:
                continue
            
            # Parse target resolution
            try:
                tw, th = map(int, target_res.split('x'))
                target_pixels = tw * th
            except:
                continue
            
            # Score: prefer closer to target resolution and FPS
            res_score = 1.0 - abs(pixels - target_pixels) / max(pixels, target_pixels)
            fps_score = 1.0 - abs(best_fps - target_fps) / max(best_fps, target_fps)
            
            # Format priority bonus
            fmt_bonus = (len(priority_formats) - priority_formats.index(fmt)) * 0.1
            
            score = res_score * 0.5 + fps_score * 0.5 + fmt_bonus
            
            if score > best_score:
                best_score = score
                best = {
                    'format': fmt,
                    'resolution': res,
                    'fps': best_fps
                }
    
    return best

# ===== CPU TESTING =====

def test_single_camera_cpu(device, fmt, res, fps, uid, use_vaapi, use_v4l2m2m, duration=5):
    """
    Test CPU usage for a single camera configuration.
    
    Returns:
        Tuple of (avg_cpu, peak_cpu, success)
    """
    # Build FFmpeg command
    settings = {
        'bitrate': '2M',
        'encoder_preset': 'ultrafast',
        'rotation': 0,
        'output_fps': fps,
    }
    
    ffmpeg_cmd = build_ffmpeg_cmd(device, fmt, res, fps, uid, use_vaapi, use_v4l2m2m, settings)
    
    # Start FFmpeg process
    try:
        process = subprocess.Popen(
            ffmpeg_cmd.split(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        return 0, 0, False
    
    # Wait for startup
    time.sleep(1)
    
    if process.poll() is not None:
        return 0, 0, False
    
    # Measure CPU
    samples = []
    peak = 0
    
    for _ in range(int(duration)):
        cpu = measure_cpu_usage(duration=1.0)
        samples.append(cpu)
        peak = max(peak, cpu)
    
    # Cleanup
    process.terminate()
    try:
        process.wait(timeout=3)
    except:
        process.kill()
    
    avg = sum(samples) / len(samples) if samples else 0
    return avg, peak, True

def test_combined_load(configs, use_vaapi, use_v4l2m2m, duration=8):
    """
    Test CPU usage with all cameras running simultaneously.
    
    Returns:
        Tuple of (success, avg_cpu, peak_cpu, samples)
    """
    processes = []
    
    # Start all cameras
    for config in configs:
        settings = {
            'bitrate': '2M',
            'encoder_preset': 'ultrafast',
            'rotation': 0,
            'output_fps': config['fps'],
        }
        
        ffmpeg_cmd = build_ffmpeg_cmd(
            config['device'],
            config['format'],
            config['resolution'],
            config['fps'],
            config['uid'],
            use_vaapi, use_v4l2m2m,
            settings
        )
        
        try:
            p = subprocess.Popen(
                ffmpeg_cmd.split(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            processes.append(p)
        except:
            pass
    
    # Wait for startup
    time.sleep(2)
    
    # Check all running
    running = all(p.poll() is None for p in processes)
    
    if not running:
        for p in processes:
            p.terminate()
        return False, 0, 0, []
    
    # Measure CPU
    samples = []
    for _ in range(int(duration)):
        cpu = measure_cpu_usage(duration=1.0)
        samples.append(cpu)
    
    # Cleanup
    for p in processes:
        p.terminate()
        try:
            p.wait(timeout=2)
        except:
            p.kill()
    
    avg = sum(samples) / len(samples) if samples else 0
    peak = max(samples) if samples else 0
    
    return True, avg, peak, samples

# ===== MOONRAKER CLEANUP =====

def cleanup_our_moonraker_cameras(settings, moonraker_url):
    """
    Remove all Moonraker cameras that we have moonraker_uids for.
    
    Args:
        settings: Our raven_settings dict
        moonraker_url: Moonraker URL
        
    Returns:
        Number of cameras removed
    """
    our_cams = get_our_moonraker_cameras(settings, moonraker_url)
    removed = 0
    
    for webcam, camera_config in our_cams:
        moonraker_uid = webcam.get('uid')
        if moonraker_uid:
            success, _ = delete_moonraker_webcam(moonraker_uid, moonraker_url)
            if success:
                # Clear the moonraker_uid from our config
                if "moonraker" in camera_config:
                    camera_config["moonraker"]["moonraker_uid"] = None
                removed += 1
    
    return removed

# ===== IDENTICAL CAMERA WARNING =====

# ===== MAIN QUICK CONFIG =====

def quick_auto_configure():
    """
    Main quick auto-configuration workflow.
    
    Returns:
        bool: True if configuration was successful
    """
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("🚀 Quick Auto-Configuration")
    print(f"{'='*70}{COLOR_RESET}")
    
    # System info
    system_ip = get_system_ip()
    print(f"\n🌐 System IP: {system_ip}")
    
    # Hardware acceleration
    print("🔍 Detecting hardware acceleration...")
    use_vaapi, use_v4l2m2m = detect_hardware_acceleration()
    
    if use_vaapi:
        print(f"   ✅ VAAPI hardware encoding")
    elif use_v4l2m2m:
        print(f"   ✅ V4L2 M2M hardware encoding")
    else:
        print(f"   ⚠️  Software encoding only")
    
    # Check APIs
    mtx_api = mediamtx_api_available()
    if mtx_api:
        print(f"   ✅ MediaMTX API available")
    else:
        print(f"   {COLOR_LOW}❌ MediaMTX API not available{COLOR_RESET}")
        print("   Please start MediaMTX service first.")
        input("\nPress Enter to continue...")
        return False
    
    moonraker_url = detect_moonraker_url()
    if moonraker_url:
        print(f"   ✅ Moonraker found at {moonraker_url}")
    else:
        print(f"   ⚠️  Moonraker not detected")
    
    # Detect cameras
    print("\n📹 Detecting cameras...")
    devices = get_all_video_devices()
    
    if not devices:
        print(f"   {COLOR_LOW}❌ No video devices found!{COLOR_RESET}")
        input("\nPress Enter to continue...")
        return False
    
    print(f"   Found {len(devices)} camera(s)")
    
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
    
    # Load settings
    settings = load_raven_settings()
    if settings is None:
        print(f"\n{COLOR_LOW}❌ Failed to load raven_settings.yml{COLOR_RESET}")
        input("\nPress Enter to continue...")
        return False
    
    # Get hardware capability
    print(f"\n{COLOR_CYAN}Step 1: Assess Hardware Capability{COLOR_RESET}")
    print("\n   [a] Let me estimate based on CPU")
    print("   [m] I'll rate it manually")
    
    choice = input(f"\n{COLOR_CYAN}Select option:{COLOR_RESET} ").strip().lower()
    
    if choice == 'a':
        print("\n   Measuring baseline CPU...")
        capability = estimate_cpu_capability()
        print(f"   Estimated capability: {capability}/10")
        
        confirm = input(f"\n{COLOR_CYAN}Use this estimate? (Y/n):{COLOR_RESET} ").strip().lower()
        if confirm == 'n':
            capability = prompt_hardware_capability()
    else:
        capability = prompt_hardware_capability()
    
    # Get quality specs
    specs = get_quality_specs(capability, len(valid_devices))
    print(f"\n✅ Targeting: {specs['target_res']} @ {specs['target_fps']} fps per camera")
    
    # Analyze each camera
    print(f"\n{COLOR_CYAN}Step 2: Analyzing Cameras{COLOR_RESET}")
    
    camera_configs = []
    existing_friendly_names = [c.get("friendly_name") for c in get_all_cameras(settings)]
    
    for dev_info in valid_devices:
        dev_path = dev_info['path']
        dev_name = dev_info['hardware_name']
        serial = dev_info['serial_number']
        
        print(f"\n   📹 {dev_name} ({dev_path})")
        
        # Get formats
        output = run_v4l2ctl(dev_path, ["--list-formats-ext"])
        formats = parse_formats(output)
        
        if not formats:
            print(f"      {COLOR_YELLOW}⚠️  Could not detect formats, skipping{COLOR_RESET}")
            continue
        
        # Find best format
        best = find_best_format(formats, specs['target_res'], specs['target_fps'])
        
        if not best:
            print(f"      {COLOR_YELLOW}⚠️  No suitable format found, skipping{COLOR_RESET}")
            continue
        
        print(f"      Selected: {best['format']} {best['resolution']} @ {best['fps']} fps")
        
        # Check if camera already exists in settings
        existing_cam, existing_idx = find_camera_by_hardware(settings, dev_name, serial)
        
        if existing_cam:
            # Update existing camera config
            camera_config = deep_copy(existing_cam)
            friendly_name = camera_config.get("friendly_name", sanitize_camera_name(dev_name))
            print(f"      Updating existing config: {friendly_name} ({camera_config['uid']})")
        else:
            # Create new camera config
            friendly_name = sanitize_camera_name(dev_name)
            if friendly_name in existing_friendly_names:
                counter = 2
                while f"{friendly_name}_{counter}" in existing_friendly_names:
                    counter += 1
                friendly_name = f"{friendly_name}_{counter}"
            
            camera_config = create_camera_config(dev_name, friendly_name, serial)
            print(f"      Creating new config: {friendly_name} ({camera_config['uid']})")
        
        existing_friendly_names.append(friendly_name)
        
        # Update capture settings
        capture = camera_config["mediamtx"]["ffmpeg"]["capture"]
        capture["format"] = best['format']
        capture["resolution"] = best['resolution']
        capture["framerate"] = best['fps']
        
        # Update encoding
        encoding = camera_config["mediamtx"]["ffmpeg"]["encoding"]
        encoding["output_fps"] = best['fps']
        
        if use_vaapi:
            encoding["encoder"] = "vaapi"
        elif use_v4l2m2m:
            encoding["encoder"] = "v4l2m2m"
        else:
            encoding["encoder"] = "libx264"
        
        camera_configs.append({
            'device': dev_path,
            'device_name': dev_name,
            'friendly_name': friendly_name,
            'format': best['format'],
            'resolution': best['resolution'],
            'fps': best['fps'],
            'uid': camera_config['uid'],
            'config': camera_config
        })
    
    if not camera_configs:
        print(f"\n{COLOR_LOW}❌ No cameras could be configured{COLOR_RESET}")
        input("\nPress Enter to continue...")
        return False
    
    # Confirm
    print(f"\n{COLOR_CYAN}Step 3: Confirm Configuration{COLOR_RESET}")
    print(f"\n   Will configure {len(camera_configs)} camera(s):")
    for cam in camera_configs:
        print(f"   - {cam['friendly_name']} ({cam['uid']})")
        print(f"     {cam['format']} {cam['resolution']} @ {cam['fps']} fps")
    
    confirm = input(f"\n{COLOR_CYAN}Proceed with configuration? (Y/n):{COLOR_RESET} ").strip().lower()
    if confirm == 'n':
        print("Cancelled.")
        input("\nPress Enter to continue...")
        return False
    
    # Save to raven_settings
    print(f"\n{COLOR_CYAN}Step 4: Saving Configuration{COLOR_RESET}")
    
    # Update capabilities for each camera before saving
    from common import update_camera_capabilities
    for cam in camera_configs:
        success, error = update_camera_capabilities(cam['config'], cam['device'])
        if success:
            print(f"   📋 {cam['friendly_name']}: Capabilities recorded")
        elif error:
            print(f"   {COLOR_YELLOW}⚠️  {cam['friendly_name']}: {error}{COLOR_RESET}")
    
    for cam in camera_configs:
        settings = save_camera_config(settings, cam['config'])
    
    save_raven_settings(settings)
    print(f"   ✅ Saved to raven_settings.yml")
    
    # Apply to MediaMTX
    print(f"\n{COLOR_CYAN}Step 5: Configuring MediaMTX{COLOR_RESET}")
    
    for cam in camera_configs:
        uid = cam['uid']
        
        # Build settings
        ffmpeg_settings = {
            'bitrate': '2M',
            'encoder_preset': 'ultrafast',
            'rotation': 0,
            'output_fps': cam['fps'],
        }
        
        ffmpeg_cmd = build_ffmpeg_cmd(
            cam['device'],
            cam['format'],
            cam['resolution'],
            cam['fps'],
            uid,
            use_vaapi, use_v4l2m2m,
            ffmpeg_settings
        )
        
        mtx_config = {
            "source": "publisher",
            "runOnInit": ffmpeg_cmd,
            "runOnInitRestart": True
        }
        
        success, action, error = add_or_update_mediamtx_path(uid, mtx_config)
        
        if success:
            print(f"   ✅ {uid} ({cam['friendly_name']})")
        else:
            print(f"   ❌ {uid}: {error}")
    
    # Wait for FFmpeg streams to initialize before adding to Moonraker
    if moonraker_url and moonraker_api_available(moonraker_url):
        print(f"\n⏳ Waiting for streams to initialize...")
        stream_wait_time = 5  # seconds
        for i in range(stream_wait_time, 0, -1):
            print(f"   Starting Moonraker configuration in {i}s...", end='\r')
            time.sleep(1)
        print(f"   Streams should be ready.              ")
    
    # Add to Moonraker
    if moonraker_url and moonraker_api_available(moonraker_url):
        print(f"\n{COLOR_CYAN}Step 6: Configuring Moonraker{COLOR_RESET}")
        
        for cam in camera_configs:
            uid = cam['uid']
            friendly = cam['friendly_name']
            
            # Moonraker camera name: truncated friendly name
            moonraker_name = truncate_friendly_name(friendly, 20)
            
            stream_url = f"http://{system_ip}:8889/{uid}/"
            snapshot_url = f"http://{system_ip}:5050/{uid}.jpg"
            
            success, result = add_moonraker_webcam(
                moonraker_name,
                stream_url,
                snapshot_url,
                target_fps=cam['fps'],
                url=moonraker_url
            )
            
            if success:
                print(f"   ✅ {moonraker_name}")
                
                # Update camera config with moonraker settings
                cam['config']['moonraker'] = {
                    'enabled': True,
                    'moonraker_uid': result,  # Store Moonraker's UUID
                    'flip_horizontal': False,
                    'flip_vertical': False,
                    'rotation': 0
                }
                settings = save_camera_config(settings, cam['config'])
            else:
                print(f"   ❌ {moonraker_name}: {result}")
        
        # Save updated moonraker settings
        save_raven_settings(settings)
    
    # Summary
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("✅ Quick Configuration Complete!")
    print(f"{'='*70}{COLOR_RESET}")
    
    print(f"\n📹 Configured {len(camera_configs)} camera(s):\n")
    
    for cam in camera_configs:
        uid = cam['uid']
        print(f"   {COLOR_HIGH}{cam['friendly_name']}{COLOR_RESET}")
        print(f"   UID: {uid}")
        print(f"   {cam['format']} {cam['resolution']} @ {cam['fps']} fps")
        print(f"   RTSP:     rtsp://{system_ip}:8554/{uid}")
        print(f"   WebRTC:   http://{system_ip}:8889/{uid}/")
        print(f"   Snapshot: http://{system_ip}:5050/{uid}.jpg")
        print()
    
    if has_duplicates:
        print(f"{COLOR_YELLOW}⚠️  Reminder: Some cameras were skipped due to duplicate hardware.")
        print(f"   See warning above for details.{COLOR_RESET}\n")
    
    input("Press Enter to continue...")
    return True
