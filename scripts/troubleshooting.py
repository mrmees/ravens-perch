#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
troubleshooting.py
------------------
Troubleshooting and diagnostic tools for Ravens Perch.
Includes service restart, FFmpeg command display, and diagnostics.

Last modified: 2026-01-12
"""

import time

# Import from common utilities
from common import (
    COLOR_CYAN, COLOR_HIGH, COLOR_LOW, COLOR_YELLOW, COLOR_RESET,
    clear_screen, get_system_ip,
    load_raven_settings, get_all_cameras,
    get_all_video_devices, resolve_device_path,
    build_ffmpeg_cmd_from_config, detect_hardware_acceleration,
    check_mediamtx_service_running, restart_services,
    mediamtx_api_available, list_mediamtx_paths,
    moonraker_api_available, get_moonraker_webcams, detect_moonraker_url
)

# ===== SERVICE RESTART =====

def restart_services_menu():
    """Restart MediaMTX and Snapfeeder services"""
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("🔄 Restart Services")
    print(f"{'='*70}{COLOR_RESET}")
    
    print("\nThis will restart the following services:")
    print("  - mediamtx.service (video streaming)")
    print("  - snapfeeder.service (snapshot server)")
    
    print(f"\n{COLOR_YELLOW}⚠️  Active streams will be interrupted briefly.{COLOR_RESET}")
    
    choice = input(f"\n{COLOR_CYAN}Proceed with restart? (y/N):{COLOR_RESET} ").strip().lower()
    
    if choice != 'y':
        print("Cancelled.")
        input("\nPress Enter to continue...")
        return
    
    print("\n🔄 Restarting services...")
    
    results = restart_services()
    
    print()
    for service, success, error in results:
        if success:
            print(f"   ✅ {service}.service restarted")
        else:
            print(f"   ❌ {service}.service failed: {error}")
    
    # Wait and check status
    print("\n⏳ Waiting for services to start...")
    time.sleep(3)
    
    # Check MediaMTX status
    if check_mediamtx_service_running():
        print(f"   ✅ MediaMTX is running")
        
        # Check API
        if mediamtx_api_available():
            print(f"   ✅ MediaMTX API is responding")
        else:
            print(f"   {COLOR_YELLOW}⚠️  MediaMTX API not responding yet{COLOR_RESET}")
    else:
        print(f"   {COLOR_LOW}❌ MediaMTX is not running{COLOR_RESET}")
    
    print(f"\n{COLOR_YELLOW}Note: Camera streams need to be loaded after restart.{COLOR_RESET}")
    print("Use 'Load Configuration' from main menu to restore streams.")
    
    input("\nPress Enter to continue...")

# ===== FFMPEG COMMAND DISPLAY =====

def display_ffmpeg_commands():
    """Display FFmpeg commands reconstructed from raven_settings"""
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("🔍 FFmpeg Commands from Configuration")
    print(f"{'='*70}{COLOR_RESET}")
    
    settings = load_raven_settings()
    if settings is None:
        print("\n❌ Failed to load raven_settings.yml")
        input("\nPress Enter to continue...")
        return
    
    cameras = get_all_cameras(settings)
    
    if not cameras:
        print("\n⚠️  No cameras configured in raven_settings.yml")
        print("   Configure cameras first using option 1 or 6.")
        input("\nPress Enter to continue...")
        return
    
    # Detect hardware acceleration
    use_vaapi, use_v4l2m2m = detect_hardware_acceleration()
    
    hw_accel = "None (software encoding)"
    if use_vaapi:
        hw_accel = "VAAPI"
    elif use_v4l2m2m:
        hw_accel = "V4L2 M2M (Raspberry Pi)"
    
    print(f"\n   Hardware Acceleration: {hw_accel}")
    print(f"   Cameras configured: {len(cameras)}")
    
    for cam in cameras:
        uid = cam.get("uid", "unknown")
        friendly = cam.get("friendly_name", cam.get("hardware_name", "Unknown"))
        hardware = cam.get("hardware_name", "Unknown")
        
        print(f"\n{COLOR_CYAN}{'─'*70}")
        print(f"📹 {friendly}")
        print(f"{'─'*70}{COLOR_RESET}")
        print(f"   UID: {uid}")
        print(f"   Hardware: {hardware}")
        
        # Try to resolve device path
        device_path, warning = resolve_device_path(settings, cam)
        
        if device_path:
            print(f"   Device: {device_path}")
            if warning:
                print(f"   {COLOR_YELLOW}{warning}{COLOR_RESET}")
            
            # Build FFmpeg command
            ffmpeg_cmd = build_ffmpeg_cmd_from_config(cam, device_path, use_vaapi, use_v4l2m2m)
            
            # Clean up for display
            ffmpeg_cmd_clean = ' '.join(ffmpeg_cmd.split())
            
            print(f"\n{COLOR_HIGH}Command (copy/paste ready):{COLOR_RESET}")
            print(ffmpeg_cmd_clean)
        else:
            print(f"   {COLOR_LOW}Device: NOT FOUND - {warning}{COLOR_RESET}")
            print(f"   Cannot generate FFmpeg command without device.")
    
    print(f"\n{COLOR_CYAN}{'='*70}{COLOR_RESET}")
    
    input("\nPress Enter to continue...")

def display_running_streams():
    """Display currently running streams from MediaMTX API"""
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("🎬 Currently Running Streams (MediaMTX)")
    print(f"{'='*70}{COLOR_RESET}")
    
    if not mediamtx_api_available():
        print(f"\n{COLOR_LOW}❌ MediaMTX API not available{COLOR_RESET}")
        print("   Is the MediaMTX service running?")
        input("\nPress Enter to continue...")
        return
    
    paths = list_mediamtx_paths()
    
    if not paths:
        print("\n⚠️  No paths configured in MediaMTX")
        print("   Use 'Load Configuration' to sync from raven_settings.yml")
        input("\nPress Enter to continue...")
        return
    
    system_ip = get_system_ip()
    
    print(f"\n   Found {len(paths)} path(s) in MediaMTX")
    
    for name, config in paths.items():
        print(f"\n{COLOR_CYAN}{'─'*70}")
        print(f"📹 Path: {name}")
        print(f"{'─'*70}{COLOR_RESET}")
        
        # Show URLs
        print(f"   RTSP:     rtsp://{system_ip}:8554/{name}")
        print(f"   WebRTC:   http://{system_ip}:8889/{name}/")
        print(f"   HLS:      http://{system_ip}:8888/{name}/index.m3u8")
        print(f"   Snapshot: http://{system_ip}:5050/{name}.jpg")
        
        # Show source info if available
        source = config.get('source', 'unknown')
        print(f"\n   Source: {source}")
        
        # Show runOnInit if present
        run_on_init = config.get('runOnInit') or config.get('conf', {}).get('runOnInit')
        if run_on_init:
            # Truncate long commands
            if len(run_on_init) > 80:
                run_on_init = run_on_init[:77] + "..."
            print(f"   RunOnInit: {run_on_init}")
    
    print(f"\n{COLOR_CYAN}{'='*70}{COLOR_RESET}")
    
    input("\nPress Enter to continue...")

# ===== DIAGNOSTIC REPORTS =====

def display_system_status():
    """Display comprehensive system status"""
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("📊 System Status")
    print(f"{'='*70}{COLOR_RESET}")
    
    # Video devices
    print(f"\n{COLOR_CYAN}Video Devices:{COLOR_RESET}")
    devices = get_all_video_devices()
    if devices:
        for dev in devices:
            serial_str = f" (Serial: {dev['serial_number']})" if dev['serial_number'] else ""
            print(f"   {dev['path']} - {dev['hardware_name']}{serial_str}")
    else:
        print(f"   {COLOR_YELLOW}No video devices detected{COLOR_RESET}")
    
    # Hardware acceleration
    print(f"\n{COLOR_CYAN}Hardware Acceleration:{COLOR_RESET}")
    use_vaapi, use_v4l2m2m = detect_hardware_acceleration()
    if use_vaapi:
        print(f"   ✅ VAAPI (Intel/AMD)")
    elif use_v4l2m2m:
        print(f"   ✅ V4L2 M2M (Raspberry Pi)")
    else:
        print(f"   ⚠️  None detected (using software encoding)")
    
    # Configuration
    print(f"\n{COLOR_CYAN}Configuration:{COLOR_RESET}")
    settings = load_raven_settings()
    if settings:
        cameras = get_all_cameras(settings)
        print(f"   Cameras configured: {len(cameras)}")
        for cam in cameras:
            enabled = "✅" if cam.get("mediamtx", {}).get("enabled", True) else "❌"
            mr_enabled = "🌙" if cam.get("moonraker", {}).get("enabled", False) else ""
            print(f"   {enabled} {cam.get('uid')} - {cam.get('friendly_name')} {mr_enabled}")
    else:
        print(f"   {COLOR_YELLOW}Could not load settings{COLOR_RESET}")
    
    # MediaMTX status
    print(f"\n{COLOR_CYAN}MediaMTX:{COLOR_RESET}")
    if check_mediamtx_service_running():
        print(f"   ✅ Service running")
        if mediamtx_api_available():
            paths = list_mediamtx_paths()
            print(f"   ✅ API responding ({len(paths)} paths)")
        else:
            print(f"   {COLOR_YELLOW}⚠️  API not responding{COLOR_RESET}")
    else:
        print(f"   {COLOR_LOW}❌ Service not running{COLOR_RESET}")
    
    # Moonraker status
    print(f"\n{COLOR_CYAN}Moonraker:{COLOR_RESET}")
    moonraker_url = detect_moonraker_url()
    if moonraker_url:
        print(f"   ✅ Found at {moonraker_url}")
        webcams = get_moonraker_webcams(moonraker_url)
        print(f"   📷 {len(webcams)} webcam(s) configured")
    else:
        print(f"   {COLOR_YELLOW}⚠️  Not detected (may not be installed){COLOR_RESET}")
    
    # Network
    print(f"\n{COLOR_CYAN}Network:{COLOR_RESET}")
    system_ip = get_system_ip()
    print(f"   System IP: {system_ip}")
    
    input("\nPress Enter to continue...")

# ===== TROUBLESHOOTING MENU =====

def troubleshooting_menu():
    """Main troubleshooting menu"""
    while True:
        clear_screen()
        print(f"\n{COLOR_CYAN}{'='*70}")
        print("🔧 Troubleshooting Menu")
        print(f"{'='*70}{COLOR_RESET}")
        
        print("\n  [1] Display FFmpeg Commands")
        print("      - Show commands for all configured cameras")
        print("      - Copy/paste ready for manual testing")
        
        print("\n  [2] View Running Streams")
        print("      - Show currently active MediaMTX paths")
        print("      - Display stream URLs")
        
        print("\n  [3] System Status")
        print("      - Video devices, hardware acceleration")
        print("      - Service status, configuration summary")
        
        print("\n  [4] Restart Services")
        print("      - Restart MediaMTX and Snapfeeder")
        print("      - Use after configuration changes")
        
        print("\n  [b] Back to main menu")
        
        choice = input(f"\n{COLOR_CYAN}Select option:{COLOR_RESET} ").strip().lower()
        
        if choice == '1':
            display_ffmpeg_commands()
        elif choice == '2':
            display_running_streams()
        elif choice == '3':
            display_system_status()
        elif choice == '4':
            restart_services_menu()
        elif choice == 'b':
            break
        else:
            print("❌ Invalid option")
            time.sleep(1)
