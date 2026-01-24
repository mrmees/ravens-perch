#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
advanced_settings.py
--------------------
Advanced settings module for Ravens Perch.
Allows fine-tuning of encoding settings, V4L2 controls, and audio.

Philosophy:
- Reads/writes to raven_settings.yml (source of truth)
- Changes are saved to yml and can be applied via Load Configuration

Last modified: 2026-01-13
"""

import time

from common import (
    COLOR_CYAN, COLOR_HIGH, COLOR_MED, COLOR_LOW, COLOR_YELLOW, COLOR_RESET,
    clear_screen, get_system_ip,
    get_all_video_devices, resolve_device_path,
    get_v4l2_controls, get_audio_devices, apply_v4l2_controls,
    load_raven_settings, save_raven_settings,
    get_all_cameras, save_camera_config, deep_copy
)

# ===== DISPLAY FUNCTIONS =====

def display_camera_settings(camera_config):
    """Display current settings for a camera"""
    friendly = camera_config.get("friendly_name", "Unknown")
    uid = camera_config.get("uid", "?")
    hardware = camera_config.get("hardware_name", "Unknown")
    
    print(f"\n{COLOR_CYAN}{'─'*70}")
    print(f"📹 {friendly} (UID: {uid})")
    print(f"{'─'*70}{COLOR_RESET}")
    print(f"   Hardware: {hardware}")
    
    # Capture settings
    capture = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("capture", {})
    print(f"\n   Capture:")
    print(f"   Format:     {capture.get('format', 'N/A')}")
    print(f"   Resolution: {capture.get('resolution', 'N/A')}")
    print(f"   Framerate:  {capture.get('framerate', 'N/A')} fps")
    
    # Encoding settings
    encoding = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("encoding", {})
    print(f"\n   Encoding:")
    print(f"   Encoder:    {encoding.get('encoder', 'N/A')}")
    print(f"   Bitrate:    {encoding.get('bitrate', 'N/A')}")
    print(f"   Preset:     {encoding.get('preset', 'N/A')}")
    print(f"   Output FPS: {encoding.get('output_fps', 'N/A')}")
    print(f"   Rotation:   {encoding.get('rotation', 0)}°")
    
    # Audio settings
    audio = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("audio", {})
    audio_status = "Enabled" if audio.get('enabled') else "Disabled"
    print(f"\n   Audio: {audio_status}")
    if audio.get('enabled'):
        print(f"   Device: {audio.get('device', 'N/A')}")
        print(f"   Codec:  {audio.get('codec', 'aac')}")
    
    # V4L2 controls
    v4l2 = camera_config.get("v4l2-ctl", {})
    if v4l2:
        print(f"\n   V4L2 Controls:")
        for name, value in v4l2.items():
            print(f"   {name}: {value}")
    
    # Moonraker status
    moonraker = camera_config.get("moonraker", {})
    if moonraker.get("enabled"):
        print(f"\n   Moonraker: Enabled")
        print(f"   Name: {moonraker.get('name', 'N/A')}")
    else:
        print(f"\n   Moonraker: Not configured")

# ===== SETTING EDITORS =====

def edit_bitrate(camera_config):
    """Edit bitrate setting"""
    encoding = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("encoding", {})
    current = encoding.get("bitrate", "4M")
    
    print(f"\n{COLOR_CYAN}Bitrate Setting{COLOR_RESET}")
    print(f"   Current: {current}")
    print(f"\n   Common values:")
    print(f"   - 1M:  Low bandwidth, lower quality")
    print(f"   - 2M:  Balanced")
    print(f"   - 4M:  Good quality (default)")
    print(f"   - 6M:  High quality")
    print(f"   - 8M+: Very high quality (more bandwidth)")
    
    new_value = input(f"\n{COLOR_CYAN}New bitrate (e.g., 4M) or Enter to keep:{COLOR_RESET} ").strip()
    
    if new_value:
        # Validate format
        if not new_value.endswith(('K', 'M', 'k', 'm')):
            new_value += 'M'
        encoding["bitrate"] = new_value.upper()
        print(f"   ✅ Bitrate set to {new_value.upper()}")
        return True
    
    return False

def edit_preset(camera_config):
    """Edit encoder preset"""
    encoding = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("encoding", {})
    current = encoding.get("preset", "ultrafast")
    
    presets = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium"]
    
    print(f"\n{COLOR_CYAN}Encoder Preset{COLOR_RESET}")
    print(f"   Current: {current}")
    print(f"\n   Available presets (faster = lower CPU, lower quality):")
    for i, p in enumerate(presets, 1):
        marker = " ←" if p == current else ""
        print(f"   [{i}] {p}{marker}")
    
    choice = input(f"\n{COLOR_CYAN}Select preset (1-{len(presets)}) or Enter to keep:{COLOR_RESET} ").strip()
    
    if choice:
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(presets):
                encoding["preset"] = presets[idx]
                print(f"   ✅ Preset set to {presets[idx]}")
                return True
        except ValueError:
            pass
    
    return False

def edit_rotation(camera_config):
    """Edit rotation setting"""
    encoding = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("encoding", {})
    current = encoding.get("rotation", 0)
    
    rotations = [0, 90, 180, 270]
    
    print(f"\n{COLOR_CYAN}Rotation Setting{COLOR_RESET}")
    print(f"   Current: {current}°")
    print(f"\n   Options:")
    for r in rotations:
        marker = " ←" if r == current else ""
        print(f"   [{r}] {r}°{marker}")
    
    choice = input(f"\n{COLOR_CYAN}Enter rotation (0/90/180/270) or Enter to keep:{COLOR_RESET} ").strip()
    
    if choice:
        try:
            rot = int(choice)
            if rot in rotations:
                encoding["rotation"] = rot
                print(f"   ✅ Rotation set to {rot}°")
                return True
        except ValueError:
            pass
    
    return False

def edit_output_fps(camera_config):
    """Edit output frame rate"""
    capture = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("capture", {})
    encoding = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("encoding", {})
    
    capture_fps = capture.get("framerate", 30)
    current = encoding.get("output_fps", capture_fps)
    
    print(f"\n{COLOR_CYAN}Output Frame Rate{COLOR_RESET}")
    print(f"   Capture FPS: {capture_fps}")
    print(f"   Current Output FPS: {current}")
    print(f"\n   Lower output FPS reduces CPU/bandwidth.")
    print(f"   Common values: 5, 10, 15, 20, 30")
    
    choice = input(f"\n{COLOR_CYAN}Output FPS (max {capture_fps}) or Enter to keep:{COLOR_RESET} ").strip()
    
    if choice:
        try:
            fps = int(choice)
            if 1 <= fps <= capture_fps:
                encoding["output_fps"] = fps
                print(f"   ✅ Output FPS set to {fps}")
                return True
            else:
                print(f"   ❌ FPS must be between 1 and {capture_fps}")
        except ValueError:
            pass
    
    return False

def edit_audio(camera_config):
    """Edit audio settings"""
    audio = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("audio", {})
    enabled = audio.get("enabled", False)
    
    print(f"\n{COLOR_CYAN}Audio Settings{COLOR_RESET}")
    print(f"   Currently: {'Enabled' if enabled else 'Disabled'}")
    
    if not enabled:
        choice = input(f"\n{COLOR_CYAN}Enable audio? (y/N):{COLOR_RESET} ").strip().lower()
        if choice != 'y':
            return False
    
    # Get available audio devices
    devices = get_audio_devices()
    
    if not devices:
        print(f"\n   {COLOR_YELLOW}⚠️  No audio devices detected{COLOR_RESET}")
        choice = input(f"\n{COLOR_CYAN}Disable audio? (Y/n):{COLOR_RESET} ").strip().lower()
        if choice in ('', 'y'):
            audio["enabled"] = False
            return True
        return False
    
    print(f"\n   Available audio devices:")
    for i, dev in enumerate(devices, 1):
        print(f"   [{i}] {dev['id']} - {dev['name']}")
    print(f"   [d] Disable audio")
    
    choice = input(f"\n{COLOR_CYAN}Select device:{COLOR_RESET} ").strip().lower()
    
    if choice == 'd':
        audio["enabled"] = False
        audio["device"] = None
        print(f"   ✅ Audio disabled")
        return True
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(devices):
            audio["enabled"] = True
            audio["device"] = devices[idx]['id']
            print(f"   ✅ Audio enabled: {devices[idx]['id']}")
            
            # Codec selection
            print(f"\n   Audio codec:")
            print(f"   [1] AAC (default, good compatibility)")
            print(f"   [2] Opus (better quality, less compatible)")
            
            codec_choice = input(f"\n{COLOR_CYAN}Select codec (1/2):{COLOR_RESET} ").strip()
            if codec_choice == '2':
                audio["codec"] = "opus"
            else:
                audio["codec"] = "aac"
            
            return True
    except ValueError:
        pass
    
    return False

def edit_v4l2_controls(camera_config, settings):
    """Edit V4L2 controls for a camera"""
    # Resolve device path
    device_path, warning = resolve_device_path(settings, camera_config)
    
    if not device_path:
        print(f"\n{COLOR_LOW}❌ Cannot edit V4L2 controls: {warning}{COLOR_RESET}")
        input("\nPress Enter to continue...")
        return False
    
    if warning:
        print(f"\n{COLOR_YELLOW}{warning}{COLOR_RESET}")
    
    # Get available controls
    controls = get_v4l2_controls(device_path)
    
    if not controls:
        print(f"\n{COLOR_YELLOW}⚠️  No V4L2 controls available for this camera{COLOR_RESET}")
        input("\nPress Enter to continue...")
        return False
    
    # Get current saved values
    saved_v4l2 = camera_config.get("v4l2-ctl", {}).copy()
    
    changed = False
    
    while True:
        clear_screen()
        print(f"\n{COLOR_CYAN}{'='*80}")
        print(f"🎛️  V4L2 Image Controls: {camera_config.get('friendly_name')}")
        print(f"{'='*80}{COLOR_RESET}")
        print(f"   Device: {device_path}")
        
        # Group controls by section
        user_controls = [(k, v) for k, v in controls.items() if v.get('section') == 'User Controls']
        camera_controls = [(k, v) for k, v in controls.items() if v.get('section') == 'Camera Controls']
        other_controls = [(k, v) for k, v in controls.items() if v.get('section') not in ('User Controls', 'Camera Controls')]
        
        # Check if any controls are inactive
        has_inactive = any(v.get('flags') == 'inactive' for v in controls.values())
        
        ctrl_list = []
        
        # Table header format
        header = f"{'Opt':>3} | {'Control':<20} | {'Type':<5} | {'Range/Options':<14} | {'Def':<6} | {'Cur':<6} | {'Saved'}"
        separator = f"{'-'*3}-+-{'-'*20}-+-{'-'*5}-+-{'-'*14}-+-{'-'*6}-+-{'-'*6}-+-{'-'*6}"
        
        # Display User Controls
        if user_controls:
            print(f"\n{COLOR_HIGH}User Controls:{COLOR_RESET}")
            print(header)
            print(separator)
            
            for name, info in user_controls:
                ctrl_list.append((name, info))
                _print_control_row(len(ctrl_list), name, info, saved_v4l2)
        
        # Display Camera Controls
        if camera_controls:
            print(f"\n{COLOR_HIGH}Camera Controls:{COLOR_RESET}")
            print(header)
            print(separator)
            
            for name, info in camera_controls:
                ctrl_list.append((name, info))
                _print_control_row(len(ctrl_list), name, info, saved_v4l2)
        
        # Display Other Controls (if any)
        if other_controls:
            print(f"\n{COLOR_HIGH}Other Controls:{COLOR_RESET}")
            print(header)
            print(separator)
            
            for name, info in other_controls:
                ctrl_list.append((name, info))
                _print_control_row(len(ctrl_list), name, info, saved_v4l2)
        
        # Note about inactive controls
        if has_inactive:
            print(f"\n   {COLOR_LOW}* Red items are inactive due to a related control (e.g., auto mode enabled){COLOR_RESET}")
        
        print(f"\n   [s] Save and exit")
        print(f"   [c] Clear all saved controls")
        print(f"   [b] Back without saving")
        
        choice = input(f"\n{COLOR_CYAN}Select control to edit:{COLOR_RESET} ").strip().lower()
        
        if choice == 's':
            if changed:
                camera_config["v4l2-ctl"] = saved_v4l2
            return changed
        elif choice == 'b':
            return False
        elif choice == 'c':
            saved_v4l2 = {}
            changed = True
            print("   ✅ Cleared all V4L2 controls")
            time.sleep(0.5)
            continue
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(ctrl_list):
                name, info = ctrl_list[idx]
                
                # Check if control is inactive
                if info.get('flags') == 'inactive':
                    print(f"\n   {COLOR_YELLOW}⚠️  This control is currently inactive.")
                    print(f"   It may be controlled by another setting (e.g., auto mode).{COLOR_RESET}")
                    input("   Press Enter to continue...")
                    continue
                
                result = _edit_single_control(name, info, saved_v4l2, device_path)
                if result:
                    changed = True
                    # Refresh controls - changing one control may enable/disable others
                    controls = get_v4l2_controls(device_path)
        except ValueError:
            pass

def _print_control_row(idx, name, info, saved_v4l2):
    """Print a single control row in the V4L2 controls table"""
    ctrl_type = info.get('type', '?')
    is_inactive = info.get('flags') == 'inactive'
    default_v = info.get('default', '?')
    current_v = info.get('value', '?')
    
    # Format range/options based on type
    if ctrl_type == 'int':
        min_v = info.get('min', '?')
        max_v = info.get('max', '?')
        range_str = f"{min_v} - {max_v}"
    elif ctrl_type == 'bool':
        range_str = "0=Off, 1=On"
    elif ctrl_type == 'menu':
        menu_opts = info.get('menu_options', {})
        if menu_opts:
            # Show abbreviated menu options
            opts = [f"{k}={v[:5]}" for k, v in list(menu_opts.items())[:2]]
            range_str = ", ".join(opts)
            if len(menu_opts) > 2:
                range_str += "..."
        else:
            range_str = "menu"
    else:
        range_str = ctrl_type
    
    # Truncate range string if needed
    if len(range_str) > 14:
        range_str = range_str[:11] + "..."
    
    # Format default value (with label for menu)
    if ctrl_type == 'menu':
        menu_opts = info.get('menu_options', {})
        label = menu_opts.get(str(default_v), '')
        default_str = f"{default_v}" + (f"={label[:3]}" if label else "")
    else:
        default_str = str(default_v) if default_v != '?' else '-'
    
    if len(default_str) > 6:
        default_str = default_str[:6]
    
    # Format current hardware value (with label for menu)
    if ctrl_type == 'menu':
        menu_opts = info.get('menu_options', {})
        label = menu_opts.get(str(current_v), '')
        current_str = f"{current_v}" + (f"={label[:3]}" if label else "")
    else:
        current_str = str(current_v) if current_v != '?' else '-'
    
    if len(current_str) > 6:
        current_str = current_str[:6]
    
    # Current saved value (with label for menu)
    saved = saved_v4l2.get(name)
    if saved is not None:
        if ctrl_type == 'menu':
            menu_opts = info.get('menu_options', {})
            label = menu_opts.get(str(saved), '')
            saved_str = f"{saved}" + (f"={label[:3]}" if label else "")
        else:
            saved_str = str(saved)
    else:
        saved_str = '-'
    
    if len(saved_str) > 6:
        saved_str = saved_str[:6]
    
    # Color inactive controls differently
    if is_inactive:
        color = COLOR_LOW
        name_display = name[:18] + " *" if len(name) > 18 else name + " *"
    else:
        color = ""
        name_display = name[:20] if len(name) > 20 else name
    
    print(f"{color}{idx:>3} | {name_display:<20} | {ctrl_type:<5} | {range_str:<14} | {default_str:<6} | {current_str:<6} | {saved_str}{COLOR_RESET if color else ''}")

def _edit_single_control(name, info, saved_v4l2, device_path):
    """
    Edit a single V4L2 control.
    
    Returns:
        True if value was changed, False otherwise
    """
    ctrl_type = info.get('type', '')
    min_v = info.get('min', 0)
    max_v = info.get('max', 100)
    default_v = info.get('default', '?')
    current_v = info.get('value', '?')  # Current hardware value
    saved_v = saved_v4l2.get(name)  # Our saved value (may be None)
    
    print(f"\n   {COLOR_HIGH}{name}{COLOR_RESET}")
    print(f"   Type: {ctrl_type}")
    print(f"   Default: {default_v}")
    print(f"   Current (hardware): {current_v}")
    if saved_v is not None:
        print(f"   Saved setting: {saved_v}")
    else:
        print(f"   Saved setting: (none)")
    
    if ctrl_type == 'menu':
        # Display menu options
        menu_opts = info.get('menu_options', {})
        if menu_opts:
            print(f"\n   Available options:")
            for val, label in sorted(menu_opts.items(), key=lambda x: int(x[0])):
                marker = " ← current" if str(current_v) == val else ""
                print(f"      [{val}] {label}{marker}")
            
            new_val = input(f"\n{COLOR_CYAN}Enter option number (or Enter to skip):{COLOR_RESET} ").strip()
            
            if new_val:
                if new_val in menu_opts:
                    val = int(new_val)
                    apply_v4l2_controls(device_path, {name: val})
                    saved_v4l2[name] = val
                    print(f"   ✅ Applied {name} = {val} ({menu_opts[new_val]})")
                    time.sleep(0.5)
                    return True
                else:
                    print(f"   ❌ Invalid option")
                    time.sleep(0.5)
        else:
            print(f"   ❌ No menu options available")
            time.sleep(0.5)
    
    elif ctrl_type == 'bool':
        print(f"\n   [0] Off")
        print(f"   [1] On")
        
        new_val = input(f"\n{COLOR_CYAN}Enter 0 or 1 (or Enter to skip):{COLOR_RESET} ").strip()
        
        if new_val in ('0', '1'):
            val = int(new_val)
            apply_v4l2_controls(device_path, {name: val})
            saved_v4l2[name] = val
            print(f"   ✅ Applied {name} = {val}")
            time.sleep(0.5)
            return True
        elif new_val:
            print(f"   ❌ Invalid value (must be 0 or 1)")
            time.sleep(0.5)
    
    else:  # int or other numeric types
        step = info.get('step', 1)
        print(f"   Range: {min_v} - {max_v} (step: {step})")
        
        new_val = input(f"\n{COLOR_CYAN}New value (or Enter to skip):{COLOR_RESET} ").strip()
        
        if new_val:
            try:
                val = int(new_val)
                if int(min_v) <= val <= int(max_v):
                    apply_v4l2_controls(device_path, {name: val})
                    saved_v4l2[name] = val
                    print(f"   ✅ Applied {name} = {val}")
                    time.sleep(0.5)
                    return True
                else:
                    print(f"   ❌ Value out of range ({min_v} - {max_v})")
                    time.sleep(0.5)
            except ValueError:
                print(f"   ❌ Invalid value (must be a number)")
                time.sleep(0.5)
    
    return False

# ===== CAMERA SETTINGS MENU =====

def configure_camera_settings(camera_config, settings):
    """Configure settings for a single camera"""
    changed = False
    
    while True:
        clear_screen()
        display_camera_settings(camera_config)
        
        print(f"\n{COLOR_CYAN}Edit Settings:{COLOR_RESET}")
        print(f"   [1] Bitrate")
        print(f"   [2] Encoder preset")
        print(f"   [3] Output frame rate")
        print(f"   [4] Rotation")
        print(f"   [5] Audio")
        print(f"   [6] V4L2 controls")
        print(f"\n   [s] Save and exit")
        print(f"   [b] Back without saving")
        
        choice = input(f"\n{COLOR_CYAN}Select option:{COLOR_RESET} ").strip().lower()
        
        if choice == '1':
            if edit_bitrate(camera_config):
                changed = True
        elif choice == '2':
            if edit_preset(camera_config):
                changed = True
        elif choice == '3':
            if edit_output_fps(camera_config):
                changed = True
        elif choice == '4':
            if edit_rotation(camera_config):
                changed = True
        elif choice == '5':
            if edit_audio(camera_config):
                changed = True
        elif choice == '6':
            if edit_v4l2_controls(camera_config, settings):
                changed = True
        elif choice == 's':
            return changed, camera_config
        elif choice == 'b':
            return False, None
        
        input("\nPress Enter to continue...")
    
    return changed, camera_config

# ===== MAIN MENU =====

def advanced_settings_menu():
    """Main advanced settings menu"""
    while True:
        clear_screen()
        print(f"\n{COLOR_CYAN}{'='*70}")
        print("⚙️  Advanced Video/Audio Settings")
        print(f"{'='*70}{COLOR_RESET}")
        
        # Load settings
        settings = load_raven_settings()
        if settings is None:
            print(f"\n{COLOR_LOW}❌ Failed to load raven_settings.yml{COLOR_RESET}")
            input("\nPress Enter to continue...")
            return False
        
        cameras = get_all_cameras(settings)
        
        if not cameras:
            print(f"\n⚠️  No cameras configured")
            print("   Use Configure Cameras or Quick Auto-Configure first.")
            input("\nPress Enter to continue...")
            return False
        
        print(f"\n   Select a camera to configure:\n")
        
        for i, cam in enumerate(cameras, 1):
            uid = cam.get("uid", "?")
            friendly = cam.get("friendly_name", "Unknown")
            
            # Get brief settings summary
            capture = cam.get("mediamtx", {}).get("ffmpeg", {}).get("capture", {})
            encoding = cam.get("mediamtx", {}).get("ffmpeg", {}).get("encoding", {})
            
            fmt = capture.get("format", "?")
            res = capture.get("resolution", "?")
            fps = encoding.get("output_fps", capture.get("framerate", "?"))
            bitrate = encoding.get("bitrate", "?")
            
            print(f"   [{i}] {friendly} ({uid})")
            print(f"       {fmt} {res} @ {fps}fps, {bitrate}")
        
        print(f"\n   [b] Back to main menu")
        
        choice = input(f"\n{COLOR_CYAN}Select camera:{COLOR_RESET} ").strip().lower()
        
        if choice == 'b':
            return False
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(cameras):
                # Make a deep copy to edit
                camera_config = deep_copy(cameras[idx])
                
                changed, updated_config = configure_camera_settings(camera_config, settings)
                
                if changed and updated_config:
                    # Save to settings
                    settings = save_camera_config(settings, updated_config)
                    save_raven_settings(settings)
                    print(f"\n✅ Settings saved to raven_settings.yml")
                    print(f"   Use 'Load Configuration' to apply changes to MediaMTX.")
                    input("\nPress Enter to continue...")
        except ValueError:
            pass
