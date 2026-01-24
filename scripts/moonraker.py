#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
moonraker.py
------------
Moonraker integration module for Ravens Perch.
Handles adding, updating, and managing webcams in Moonraker/Fluidd/Mainsail.

Philosophy:
- Moonraker camera names use format: {uid}_{friendly_name}
- This allows matching our cameras by the UID prefix
- Settings are saved to raven_settings.yml

Last modified: 2026-01-12
"""

from common import (
    COLOR_CYAN, COLOR_HIGH, COLOR_MED, COLOR_LOW, COLOR_YELLOW, COLOR_RESET,
    clear_screen, get_system_ip, sanitize_camera_name,
    load_raven_settings, save_raven_settings,
    get_all_cameras, save_camera_config, deep_copy,
    detect_moonraker_url, moonraker_api_available,
    get_moonraker_webcams, add_moonraker_webcam, delete_moonraker_webcam,
    get_our_moonraker_cameras, get_moonraker_webcam_by_uid,
    sync_moonraker_settings_to_config, truncate_friendly_name
)

# ===== MOONRAKER CAMERA MANAGEMENT =====

def display_moonraker_status(moonraker_url, settings=None):
    """Display current Moonraker webcam status"""
    print(f"\n{COLOR_CYAN}Moonraker Webcam Status{COLOR_RESET}")
    print(f"   URL: {moonraker_url}")
    
    webcams = get_moonraker_webcams(moonraker_url)
    
    if not webcams:
        print(f"\n   No webcams configured in Moonraker")
        return []
    
    print(f"\n   Found {len(webcams)} webcam(s):")
    
    # Build a set of moonraker_uids we manage
    our_moonraker_uids = set()
    if settings:
        for cam in settings.get("cameras", []):
            moonraker_uid = cam.get("moonraker", {}).get("moonraker_uid")
            if moonraker_uid:
                our_moonraker_uids.add(moonraker_uid)
    
    our_cams = []
    other_cams = []
    
    for cam in webcams:
        webcam_uid = cam.get('uid')
        if webcam_uid and webcam_uid in our_moonraker_uids:
            our_cams.append(cam)
        else:
            other_cams.append(cam)
    
    if our_cams:
        print(f"\n   {COLOR_HIGH}Ravens Perch cameras:{COLOR_RESET}")
        for cam in our_cams:
            name = cam.get('name', 'Unknown')
            stream = cam.get('stream_url', 'N/A')
            webcam_uid = cam.get('uid', 'N/A')
            print(f"   - {name}")
            print(f"     Moonraker UID: {webcam_uid[:8]}..., Stream: {stream}")
    
    if other_cams:
        print(f"\n   {COLOR_YELLOW}Other cameras (not managed by Ravens Perch):{COLOR_RESET}")
        for cam in other_cams:
            name = cam.get('name', 'Unknown')
            print(f"   - {name}")
    
    return webcams

def add_camera_to_moonraker(camera_config, moonraker_url, settings):
    """Add a camera to Moonraker"""
    uid = camera_config.get("uid")
    friendly = camera_config.get("friendly_name", "Camera")
    
    # Use truncated friendly name (no uid prefix)
    moonraker_name = truncate_friendly_name(friendly, 20)
    
    system_ip = get_system_ip()
    stream_url = f"http://{system_ip}:8889/{uid}/"
    snapshot_url = f"http://{system_ip}:5050/{uid}.jpg"
    
    # Get FPS from capture settings
    capture = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("capture", {})
    target_fps = capture.get("framerate", 15)
    
    # Get existing moonraker settings if any (preserved flip/rotation)
    moonraker = camera_config.get("moonraker", {})
    flip_h = moonraker.get("flip_horizontal", False)
    flip_v = moonraker.get("flip_vertical", False)
    rotation = moonraker.get("rotation", 0)
    
    print(f"\n   Adding to Moonraker: {moonraker_name}")
    print(f"   Stream:   {stream_url}")
    print(f"   Snapshot: {snapshot_url}")
    
    success, result = add_moonraker_webcam(
        moonraker_name,
        stream_url,
        snapshot_url,
        target_fps=target_fps,
        url=moonraker_url,
        flip_horizontal=flip_h,
        flip_vertical=flip_v,
        rotation=rotation
    )
    
    if success:
        print(f"   ✅ Added successfully")
        
        # Update camera config with moonraker settings
        camera_config["moonraker"] = {
            "enabled": True,
            "moonraker_uid": result,  # Store Moonraker's UUID
            "flip_horizontal": flip_h,
            "flip_vertical": flip_v,
            "rotation": rotation
        }
        
        # Save to settings
        settings = save_camera_config(settings, camera_config)
        save_raven_settings(settings)
        
        return True
    else:
        print(f"   ❌ Failed: {result}")
        return False

def remove_camera_from_moonraker(camera_config, moonraker_url, settings):
    """Remove a camera from Moonraker"""
    moonraker = camera_config.get("moonraker", {})
    
    if not moonraker.get("enabled"):
        print(f"   Camera not configured in Moonraker")
        return False
    
    moonraker_uid = moonraker.get("moonraker_uid")
    friendly = camera_config.get("friendly_name", "Camera")
    
    if not moonraker_uid:
        print(f"   ⚠️  No moonraker_uid stored, camera may not exist in Moonraker")
        # Clear moonraker settings anyway
        camera_config["moonraker"] = {
            "enabled": False,
            "moonraker_uid": None,
            "flip_horizontal": False,
            "flip_vertical": False,
            "rotation": 0
        }
        settings = save_camera_config(settings, camera_config)
        save_raven_settings(settings)
        return True
    
    success, error = delete_moonraker_webcam(moonraker_uid, moonraker_url)
    
    if success:
        print(f"   ✅ Removed from Moonraker: {friendly}")
        
        # Clear moonraker settings but preserve flip/rotation preferences
        camera_config["moonraker"] = {
            "enabled": False,
            "moonraker_uid": None,
            "flip_horizontal": moonraker.get("flip_horizontal", False),
            "flip_vertical": moonraker.get("flip_vertical", False),
            "rotation": moonraker.get("rotation", 0)
        }
        settings = save_camera_config(settings, camera_config)
        save_raven_settings(settings)
        
        return True
    else:
        print(f"   ❌ Failed to remove: {error}")
        return False

def add_all_cameras_to_moonraker(moonraker_url, settings):
    """Add all configured cameras to Moonraker"""
    cameras = get_all_cameras(settings)
    
    if not cameras:
        print(f"\n   No cameras configured")
        return
    
    added = 0
    skipped = 0
    failed = 0
    
    for cam in cameras:
        uid = cam.get("uid")
        friendly = cam.get("friendly_name", "Unknown")
        
        # Check if already in Moonraker
        moonraker = cam.get("moonraker", {})
        if moonraker.get("enabled"):
            print(f"\n   {friendly} ({uid}): Already configured, skipping")
            skipped += 1
            continue
        
        # Make a copy to modify
        camera_config = deep_copy(cam)
        
        if add_camera_to_moonraker(camera_config, moonraker_url, settings):
            added += 1
            # Reload settings after save
            settings = load_raven_settings()
        else:
            failed += 1
    
    print(f"\n   Summary: {added} added, {skipped} skipped, {failed} failed")

def remove_all_our_cameras_from_moonraker(moonraker_url, settings):
    """Remove all our cameras from Moonraker"""
    our_cams = get_our_moonraker_cameras(settings, moonraker_url)
    
    if not our_cams:
        print(f"\n   No Ravens Perch cameras found in Moonraker")
        return
    
    print(f"\n   Removing {len(our_cams)} camera(s)...")
    
    removed = 0
    for webcam, camera_config in our_cams:
        moonraker_uid = webcam.get("uid")
        name = webcam.get("name")
        
        success, error = delete_moonraker_webcam(moonraker_uid, moonraker_url)
        if success:
            print(f"   ✅ Removed: {name}")
            removed += 1
            
            # Clear the moonraker_uid from our config but preserve flip/rotation
            moonraker = camera_config.get("moonraker", {})
            camera_config["moonraker"] = {
                "enabled": False,
                "moonraker_uid": None,
                "flip_horizontal": moonraker.get("flip_horizontal", False),
                "flip_vertical": moonraker.get("flip_vertical", False),
                "rotation": moonraker.get("rotation", 0)
            }
        else:
            print(f"   ❌ Failed to remove {name}: {error}")
    
    # Save updated settings
    save_raven_settings(settings)
    
    print(f"\n   Removed {removed} camera(s)")

# ===== MOONRAKER MENU =====

def moonraker_integration_menu():
    """Main Moonraker integration menu"""
    while True:
        clear_screen()
        print(f"\n{COLOR_CYAN}{'='*70}")
        print("🌙 Moonraker Integration")
        print(f"{'='*70}{COLOR_RESET}")
        
        # Detect Moonraker
        moonraker_url = detect_moonraker_url()
        
        if not moonraker_url:
            print(f"\n{COLOR_YELLOW}⚠️  Moonraker not detected{COLOR_RESET}")
            print("   Moonraker is required for Fluidd/Mainsail camera integration.")
            print("\n   [r] Retry detection")
            print("   [m] Enter URL manually")
            print("   [b] Back to main menu")
            
            choice = input(f"\n{COLOR_CYAN}Select option:{COLOR_RESET} ").strip().lower()
            
            if choice == 'r':
                continue
            elif choice == 'm':
                url = input(f"\n{COLOR_CYAN}Enter Moonraker URL:{COLOR_RESET} ").strip()
                if url:
                    moonraker_url = url.rstrip('/')
                    if not moonraker_api_available(moonraker_url):
                        print(f"   {COLOR_LOW}❌ Cannot connect to {moonraker_url}{COLOR_RESET}")
                        input("\nPress Enter to continue...")
                        continue
            elif choice == 'b':
                return
            else:
                continue
        
        if not moonraker_url:
            continue
        
        # Load settings
        settings = load_raven_settings()
        if settings is None:
            print(f"\n{COLOR_LOW}❌ Failed to load raven_settings.yml{COLOR_RESET}")
            input("\nPress Enter to continue...")
            return
        
        # Display status
        display_moonraker_status(moonraker_url, settings)
        
        # Get our cameras
        cameras = get_all_cameras(settings)
        cameras_in_moonraker = sum(1 for c in cameras if c.get("moonraker", {}).get("enabled"))
        cameras_not_in_moonraker = len(cameras) - cameras_in_moonraker
        
        print(f"\n{COLOR_CYAN}Options:{COLOR_RESET}")
        
        if cameras_not_in_moonraker > 0:
            print(f"\n   [1] Add camera to Moonraker")
            print(f"       {cameras_not_in_moonraker} camera(s) not yet in Moonraker")
        
        if cameras_in_moonraker > 0:
            print(f"\n   [2] Remove camera from Moonraker")
            print(f"       {cameras_in_moonraker} camera(s) currently in Moonraker")
        
        if cameras_not_in_moonraker > 0:
            print(f"\n   [a] Add ALL cameras to Moonraker")
        
        if cameras_in_moonraker > 0:
            print(f"\n   [x] Remove ALL our cameras from Moonraker")
        
        print(f"\n   [r] Refresh status")
        print(f"   [b] Back to main menu")
        
        choice = input(f"\n{COLOR_CYAN}Select option:{COLOR_RESET} ").strip().lower()
        
        if choice == '1' and cameras_not_in_moonraker > 0:
            # Select camera to add
            print(f"\n   Cameras not in Moonraker:")
            not_in_mr = [c for c in cameras if not c.get("moonraker", {}).get("enabled")]
            
            for i, cam in enumerate(not_in_mr, 1):
                uid = cam.get("uid", "?")
                friendly = cam.get("friendly_name", "Unknown")
                print(f"   [{i}] {friendly} ({uid})")
            
            print(f"   [c] Cancel")
            
            sel = input(f"\n{COLOR_CYAN}Select camera:{COLOR_RESET} ").strip().lower()
            
            if sel != 'c':
                try:
                    idx = int(sel) - 1
                    if 0 <= idx < len(not_in_mr):
                        camera_config = deep_copy(not_in_mr[idx])
                        add_camera_to_moonraker(camera_config, moonraker_url, settings)
                        input("\nPress Enter to continue...")
                except ValueError:
                    pass
        
        elif choice == '2' and cameras_in_moonraker > 0:
            # Select camera to remove
            print(f"\n   Cameras in Moonraker:")
            in_mr = [c for c in cameras if c.get("moonraker", {}).get("enabled")]
            
            for i, cam in enumerate(in_mr, 1):
                uid = cam.get("uid", "?")
                friendly = cam.get("friendly_name", "Unknown")
                mr_name = cam.get("moonraker", {}).get("name", "?")
                print(f"   [{i}] {friendly} ({mr_name})")
            
            print(f"   [c] Cancel")
            
            sel = input(f"\n{COLOR_CYAN}Select camera:{COLOR_RESET} ").strip().lower()
            
            if sel != 'c':
                try:
                    idx = int(sel) - 1
                    if 0 <= idx < len(in_mr):
                        camera_config = deep_copy(in_mr[idx])
                        remove_camera_from_moonraker(camera_config, moonraker_url, settings)
                        input("\nPress Enter to continue...")
                except ValueError:
                    pass
        
        elif choice == 'a' and cameras_not_in_moonraker > 0:
            confirm = input(f"\n{COLOR_CYAN}Add all {cameras_not_in_moonraker} cameras? (y/N):{COLOR_RESET} ").strip().lower()
            if confirm == 'y':
                add_all_cameras_to_moonraker(moonraker_url, settings)
                input("\nPress Enter to continue...")
        
        elif choice == 'x' and cameras_in_moonraker > 0:
            confirm = input(f"\n{COLOR_CYAN}Remove all our cameras from Moonraker? (y/N):{COLOR_RESET} ").strip().lower()
            if confirm == 'y':
                remove_all_our_cameras_from_moonraker(moonraker_url, settings)
                input("\nPress Enter to continue...")
        
        elif choice == 'r':
            continue
        
        elif choice == 'b':
            return
