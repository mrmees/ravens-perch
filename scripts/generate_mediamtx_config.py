#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Ravens Perch Camera Configuration Tool
=======================================
Main orchestrator script for MediaMTX camera configuration.

Philosophy:
- raven_settings.yml is the source of truth for camera preferences
- MediaMTX and Moonraker are configured via API only (changes are ephemeral)
- "Load Configuration" syncs settings from yml to running services
- Services can be restarted from Troubleshooting menu when needed

Modules:
- common.py: Shared utilities and constants
- device_config.py: Camera configuration workflow
- advanced_settings.py: Advanced camera settings menu
- moonraker.py: Moonraker/Fluidd/Mainsail integration
- troubleshooting.py: Diagnostic and troubleshooting tools
- camera_tester.py: Format/resolution testing
- quick_config.py: Auto-configuration

Last modified: 2026-01-12
"""

import sys
import time

# Import from common module
from common import (
    COLOR_CYAN, COLOR_HIGH, COLOR_LOW, COLOR_YELLOW, COLOR_RESET,
    clear_screen, 
    load_raven_settings, save_raven_settings, ensure_raven_settings_exist,
    get_all_cameras, get_all_video_devices,
    check_mediamtx_service_running, start_mediamtx_service,
    mediamtx_api_available, moonraker_api_available,
    find_orphaned_cameras, find_orphaned_moonraker_cameras,
    cleanup_orphaned_cameras, cleanup_orphaned_moonraker_cameras,
    sync_all_cameras, detect_moonraker_url
)

# Import module entry points
from device_config import configure_devices
from advanced_settings import advanced_settings_menu
from moonraker import moonraker_integration_menu
from troubleshooting import troubleshooting_menu
from camera_tester import camera_test_menu
from quick_config import quick_auto_configure

# ===== STARTUP SCAN =====

def startup_scan():
    """
    Perform startup scan:
    1. Ensure raven_settings.yml exists
    2. Detect video devices
    3. Find orphaned cameras in settings
    4. Find orphaned cameras in Moonraker
    5. Check MediaMTX service status
    6. Offer to load existing configuration
    
    Returns:
        Tuple of (settings, should_continue)
    """
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("🦅 Ravens Perch Camera Configuration Tool")
    print(f"{'='*70}{COLOR_RESET}")
    
    # Step 1: Ensure settings file exists
    print("\n📋 Checking configuration...")
    if not ensure_raven_settings_exist():
        print(f"\n{COLOR_LOW}❌ Cannot continue without settings file.{COLOR_RESET}")
        return None, False
    
    settings = load_raven_settings()
    if settings is None:
        print(f"\n{COLOR_LOW}❌ Failed to load settings file.{COLOR_RESET}")
        return None, False
    
    # Step 2: Detect video devices
    print("\n📹 Scanning for video devices...")
    devices = get_all_video_devices()
    cameras = get_all_cameras(settings)
    
    print(f"   Found {len(devices)} video device(s) on system")
    print(f"   Found {len(cameras)} camera(s) in configuration")
    
    # Step 3: Find orphaned cameras in settings
    orphaned_cams = find_orphaned_cameras(settings)
    if orphaned_cams:
        print(f"\n{COLOR_YELLOW}⚠️  Found {len(orphaned_cams)} camera(s) in settings with no matching device:{COLOR_RESET}")
        for cam in orphaned_cams:
            print(f"   - {cam.get('friendly_name', cam.get('hardware_name'))} ({cam.get('hardware_name')})")
        
        choice = input(f"\n{COLOR_CYAN}Remove these cameras from settings? (y/N):{COLOR_RESET} ").strip().lower()
        if choice == 'y':
            settings = cleanup_orphaned_cameras(settings, orphaned_cams)
            save_raven_settings(settings)
            print(f"   ✅ Removed {len(orphaned_cams)} orphaned camera(s) from settings")
    
    # Step 4: Find cameras with stale Moonraker UIDs
    moonraker_url = settings.get("moonraker", {}).get("url") or detect_moonraker_url()
    if moonraker_url and moonraker_api_available(moonraker_url):
        stale_mr_cams = find_orphaned_moonraker_cameras(settings, moonraker_url)
        if stale_mr_cams:
            print(f"\n{COLOR_YELLOW}⚠️  Found {len(stale_mr_cams)} camera(s) with stale Moonraker UIDs:{COLOR_RESET}")
            print(f"   (These webcams were deleted from Moonraker)")
            for cam in stale_mr_cams:
                friendly = cam.get('friendly_name', 'Unknown')
                uid = cam.get('uid', '?')
                print(f"   - {friendly} (UID: {uid})")
            
            choice = input(f"\n{COLOR_CYAN}Clear stale UIDs and re-add on next sync? (y/N):{COLOR_RESET} ").strip().lower()
            if choice == 'y':
                cleared, errors = cleanup_orphaned_moonraker_cameras(stale_mr_cams, moonraker_url)
                if cleared:
                    save_raven_settings(settings)
                    print(f"   ✅ Cleared {cleared} stale moonraker_uid(s)")
                for err in errors:
                    print(f"   {COLOR_LOW}❌ {err}{COLOR_RESET}")
    
    # Step 5: Check MediaMTX service status
    print("\n🔧 Checking services...")
    mtx_running = check_mediamtx_service_running()
    api_available = False
    
    if mtx_running:
        print(f"   ✅ MediaMTX service is running")
        
        # Check API availability
        if mediamtx_api_available():
            print(f"   ✅ MediaMTX API is responding")
            api_available = True
        else:
            print(f"   {COLOR_YELLOW}⚠️  MediaMTX API not responding{COLOR_RESET}")
            
            choice = input(f"\n{COLOR_CYAN}Restart MediaMTX service? (Y/n):{COLOR_RESET} ").strip().lower()
            if choice in ('', 'y', 'yes'):
                print("   Restarting MediaMTX...")
                from common import restart_services
                results = restart_services()
                time.sleep(2)
                
                # Check if API now available
                for _ in range(5):
                    if mediamtx_api_available():
                        print(f"   ✅ MediaMTX API now responding")
                        api_available = True
                        break
                    time.sleep(1)
                else:
                    print(f"   {COLOR_YELLOW}⚠️  API still not responding{COLOR_RESET}")
    else:
        print(f"   {COLOR_YELLOW}⚠️  MediaMTX service is not running{COLOR_RESET}")
        
        choice = input(f"\n{COLOR_CYAN}Start MediaMTX service? (Y/n):{COLOR_RESET} ").strip().lower()
        if choice in ('', 'y', 'yes'):
            print("   Starting MediaMTX...")
            success, error = start_mediamtx_service()
            if success:
                time.sleep(2)  # Wait for service to start
                if check_mediamtx_service_running():
                    print(f"   ✅ MediaMTX started successfully")
                    mtx_running = True
                    
                    # Wait for API
                    print("   Waiting for API...")
                    for _ in range(5):
                        if mediamtx_api_available():
                            print(f"   ✅ MediaMTX API ready")
                            api_available = True
                            break
                        time.sleep(1)
                    else:
                        print(f"   {COLOR_YELLOW}⚠️  API not responding yet{COLOR_RESET}")
                else:
                    print(f"   {COLOR_LOW}❌ Service failed to start{COLOR_RESET}")
            else:
                print(f"   {COLOR_LOW}❌ Failed to start: {error}{COLOR_RESET}")
    
    # Step 6: Offer to load existing configuration
    cameras = get_all_cameras(settings)  # Refresh after potential cleanup
    
    if cameras and api_available:
        print(f"\n📂 Found {len(cameras)} configured camera(s)")
        choice = input(f"\n{COLOR_CYAN}Load existing configuration to MediaMTX/Moonraker? (Y/n):{COLOR_RESET} ").strip().lower()
        
        if choice in ('', 'y', 'yes'):
            results = sync_all_cameras(settings)
            
            # Summary
            mtx_ok = len(results['mediamtx_success'])
            mtx_fail = len(results['mediamtx_failed'])
            mr_ok = len(results['moonraker_success'])
            mr_fail = len(results['moonraker_failed'])
            mr_skip = len(results['moonraker_skipped'])
            
            print(f"\n📊 Sync Results:")
            print(f"   MediaMTX: {mtx_ok} loaded, {mtx_fail} failed")
            if mr_ok or mr_fail:
                print(f"   Moonraker: {mr_ok} loaded, {mr_fail} failed, {mr_skip} skipped")
            
            if mtx_fail:
                print(f"\n{COLOR_YELLOW}⚠️  Some cameras failed to load. Run configuration to fix.{COLOR_RESET}")
    elif not cameras:
        print(f"\n📂 No cameras configured yet")
        print("   Use option 1 or 6 to configure cameras")
    
    input("\nPress Enter to continue to main menu...")
    return settings, True

# ===== MAIN MENU =====

def main_menu(settings):
    """Main menu - choose between different configuration options"""
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("🦅 Ravens Perch Camera Configuration Tool")
    print(f"{'='*70}{COLOR_RESET}")
    
    # Show camera count
    cameras = get_all_cameras(settings)
    if cameras:
        print(f"\n   {len(cameras)} camera(s) configured")
    
    print("\n  [1] Configure MediaMTX Cameras")
    print("      - Select which cameras to use")
    print("      - Choose resolution and format")
    print("      - Basic setup")
    
    print("\n  [2] Moonraker Integration")
    print("      - Add cameras to Moonraker/Fluidd/Mainsail")
    print("      - Manage existing Moonraker cameras")
    print("      - Bulk operations")
    
    print("\n  [3] Advanced Video/Audio Settings")
    print("      - Adjust bitrate, encoder, rotation")
    print("      - Configure V4L2 controls")
    print("      - Audio setup")
    
    print("\n  [4] Troubleshooting")
    print("      - Display FFmpeg commands")
    print("      - Restart services")
    print("      - Diagnostic tools")
    
    print("\n  [5] Camera Tester")
    print("      - Test format/resolution/FPS combinations")
    print("      - Measure CPU usage per combination")
    print("      - Find optimal settings")
    
    print("\n  [6] Quick Auto-Configure")
    print("      - One-click setup for all cameras")
    print("      - Auto-detect optimal settings")
    print("      - Configure MediaMTX + Moonraker")
    
    print("\n  [7] Load Configuration")
    print("      - Sync settings to MediaMTX/Moonraker")
    print("      - Apply saved camera configuration")
    
    print("\n  [q] Quit")
    
    while True:
        choice = input(f"\n{COLOR_CYAN}Select option:{COLOR_RESET} ").strip().lower()
        
        if choice == '1':
            return 'device_config'
        elif choice == '2':
            return 'moonraker'
        elif choice == '3':
            return 'advanced_settings'
        elif choice == '4':
            return 'troubleshooting'
        elif choice == '5':
            return 'camera_tester'
        elif choice == '6':
            return 'quick_config'
        elif choice == '7':
            return 'load_config'
        elif choice == 'q':
            return 'quit'
        else:
            print("❌ Invalid option")

def load_configuration(settings):
    """Load configuration from raven_settings to MediaMTX/Moonraker"""
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("📂 Load Configuration")
    print(f"{'='*70}{COLOR_RESET}")
    
    cameras = get_all_cameras(settings)
    
    if not cameras:
        print("\n⚠️  No cameras configured!")
        print("   Use option 1 or 6 to configure cameras first.")
        input("\nPress Enter to continue...")
        return
    
    # Check MediaMTX
    if not mediamtx_api_available():
        print(f"\n{COLOR_LOW}❌ MediaMTX API not available{COLOR_RESET}")
        print("   Is the MediaMTX service running?")
        print("   Use Troubleshooting menu to restart services.")
        input("\nPress Enter to continue...")
        return
    
    print(f"\nThis will sync {len(cameras)} camera(s) to MediaMTX and Moonraker.")
    print("Existing paths with matching UIDs will be updated.")
    
    choice = input(f"\n{COLOR_CYAN}Proceed? (Y/n):{COLOR_RESET} ").strip().lower()
    if choice not in ('', 'y', 'yes'):
        print("Cancelled.")
        input("\nPress Enter to continue...")
        return
    
    # Sync cameras
    results = sync_all_cameras(settings)
    
    # Summary
    mtx_ok = len(results['mediamtx_success'])
    mtx_fail = len(results['mediamtx_failed'])
    mr_ok = len(results['moonraker_success'])
    mr_fail = len(results['moonraker_failed'])
    mr_skip = len(results['moonraker_skipped'])
    
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("📊 Sync Complete")
    print(f"{'='*70}{COLOR_RESET}")
    
    print(f"\n   MediaMTX: {mtx_ok} loaded successfully")
    if mtx_fail:
        print(f"            {mtx_fail} failed")
    
    if mr_ok or mr_fail or mr_skip:
        print(f"\n   Moonraker: {mr_ok} loaded successfully")
        if mr_fail:
            print(f"             {mr_fail} failed")
        if mr_skip:
            print(f"             {mr_skip} skipped (not enabled)")
    
    if mtx_ok == len(cameras):
        print(f"\n{COLOR_HIGH}✅ All cameras loaded successfully!{COLOR_RESET}")
    elif mtx_fail:
        print(f"\n{COLOR_YELLOW}⚠️  Some cameras failed. Check device connections.{COLOR_RESET}")
    
    input("\nPress Enter to continue...")

# ===== MAIN FUNCTION =====

def main():
    """Main entry point"""
    try:
        # Perform startup scan
        settings, should_continue = startup_scan()
        
        if not should_continue:
            print("👋 Goodbye!")
            sys.exit(1)
        
        # Check for auto mode from command line
        if "--auto" in sys.argv or "-a" in sys.argv:
            print("\n🤖 Running in AUTO mode - configuring all cameras automatically")
            quick_auto_configure()
            print("👋 Goodbye!")
            return
        
        # Interactive mode with menu
        while True:
            # Reload settings in case they changed
            settings = load_raven_settings()
            if settings is None:
                print(f"{COLOR_LOW}❌ Failed to load settings{COLOR_RESET}")
                break
            
            choice = main_menu(settings)
            
            if choice == 'device_config':
                configure_devices(auto_mode=False)
            
            elif choice == 'advanced_settings':
                advanced_settings_menu()
            
            elif choice == 'moonraker':
                moonraker_integration_menu()
            
            elif choice == 'troubleshooting':
                troubleshooting_menu()
            
            elif choice == 'camera_tester':
                camera_test_menu()
            
            elif choice == 'quick_config':
                quick_auto_configure()
            
            elif choice == 'load_config':
                load_configuration(settings)
            
            elif choice == 'quit':
                print("👋 Goodbye!")
                sys.exit(0)
    
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted")
        print("👋 Goodbye!")
        sys.exit(1)

if __name__ == "__main__":
    main()
