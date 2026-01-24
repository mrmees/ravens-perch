#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
camera_tester.py
----------------
Camera testing module for Ravens Perch.
Tests camera format/resolution/FPS combinations for validity and CPU usage.
"""

import os
import re
import sys
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime

# Import from common utilities
from common import (
    COLOR_CYAN, COLOR_HIGH, COLOR_MED, COLOR_LOW, COLOR_YELLOW, COLOR_RESET,
    clear_screen,
    list_video_devices, get_device_names,
    run_v4l2ctl, parse_formats,
    has_vaapi_encoder, has_v4l2m2m_encoder,
    check_mediamtx_service_running, start_mediamtx_service, stop_mediamtx_service,
    mediamtx_api_available, list_mediamtx_paths, list_active_streams, delete_mediamtx_path,
    load_raven_settings, sync_all_cameras, save_raven_settings
)

# Test results storage path
SCRIPT_DIR = Path(__file__).resolve().parent
TEST_RESULTS_PATH = SCRIPT_DIR.parent / "mediamtx" / "camera_test_results.json"

def reset_terminal():
    """Reset terminal to a clean state after progress display"""
    sys.stdout.write('\n')
    sys.stdout.flush()
    # Reset terminal settings using stty if available
    try:
        subprocess.run(['stty', 'sane'], stderr=subprocess.DEVNULL)
    except:
        pass

# ===== SYSTEM INFO =====

def get_system_info():
    """Gather system information for report"""
    info = {}
    
    # OS info
    try:
        with open('/etc/os-release', 'r') as f:
            for line in f:
                if line.startswith('PRETTY_NAME='):
                    info['os'] = line.split('=')[1].strip().strip('"')
                    break
    except:
        info['os'] = 'Unknown'
    
    # Kernel
    try:
        result = subprocess.run(['uname', '-r'], capture_output=True, text=True)
        info['kernel'] = result.stdout.strip()
    except:
        info['kernel'] = 'Unknown'
    
    # Hardware model
    try:
        with open('/proc/device-tree/model', 'r') as f:
            info['hardware'] = f.read().strip().rstrip('\x00')
    except:
        try:
            result = subprocess.run(['uname', '-m'], capture_output=True, text=True)
            info['hardware'] = result.stdout.strip()
        except:
            info['hardware'] = 'Unknown'
    
    # Encoder
    if has_v4l2m2m_encoder():
        info['encoder'] = 'h264_v4l2m2m'
    elif has_vaapi_encoder():
        info['encoder'] = 'h264_vaapi'
    else:
        info['encoder'] = 'libx264 (software)'
    
    return info

def get_camera_info(device):
    """Get camera information from v4l2-ctl"""
    info = {
        'device': device,
        'name': 'Unknown',
        'driver': 'Unknown',
        'bus': 'Unknown',
        'version': 'Unknown',
        'capabilities': []
    }
    
    try:
        result = subprocess.run(
            ['v4l2-ctl', '-d', device, '--info'],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if 'Card type' in line:
                info['name'] = line.split(':')[-1].strip()
            elif 'Driver name' in line:
                info['driver'] = line.split(':')[-1].strip()
            elif 'Bus info' in line:
                info['bus'] = line.split(':')[-1].strip()
            elif 'Driver version' in line:
                info['version'] = line.split(':')[-1].strip()
            elif 'Device Caps' in line or 'Capabilities' in line:
                # Get capabilities from following lines
                pass
    except:
        pass
    
    # Get supported formats summary
    try:
        result = subprocess.run(
            ['v4l2-ctl', '-d', device, '--list-formats'],
            capture_output=True, text=True
        )
        formats = []
        for line in result.stdout.splitlines():
            if "'" in line:
                # Extract format name like 'MJPG' or 'YUYV'
                match = re.search(r"'(\w+)'", line)
                if match:
                    formats.append(match.group(1))
        info['formats'] = formats
    except:
        info['formats'] = []
    
    return info

def generate_report(device, results, duration, output_fps=None):
    """Generate a text report of test results"""
    system_info = get_system_info()
    camera_info = get_camera_info(device)
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Check if any results have output_fps set
    has_output_fps = any(r.get('output_fps') for r in results)
    
    lines = []
    lines.append("=" * 70)
    lines.append("Ravens Perch Camera Test Report")
    lines.append(f"Generated: {timestamp}")
    lines.append("=" * 70)
    
    lines.append("\n=== SYSTEM INFO ===")
    lines.append(f"OS: {system_info['os']}")
    lines.append(f"Kernel: {system_info['kernel']}")
    lines.append(f"Hardware: {system_info['hardware']}")
    lines.append(f"Encoder: {system_info['encoder']}")
    
    lines.append("\n=== CAMERA ===")
    lines.append(f"Device: {camera_info['device']}")
    lines.append(f"Name: {camera_info['name']}")
    lines.append(f"Driver: {camera_info['driver']}")
    lines.append(f"Bus: {camera_info['bus']}")
    lines.append(f"Driver Version: {camera_info['version']}")
    if camera_info.get('formats'):
        lines.append(f"Supported Formats: {', '.join(camera_info['formats'])}")
    
    lines.append("\n=== TEST PARAMETERS ===")
    lines.append(f"Duration per test: {duration} seconds")
    if output_fps:
        lines.append(f"Output FPS: {output_fps} (frame dropping enabled)")
    else:
        lines.append(f"Output FPS: Same as capture")
    lines.append(f"Total combinations tested: {len(results)}")
    
    valid_results = [r for r in results if r['valid']]
    invalid_results = [r for r in results if not r['valid']]
    lines.append(f"Successful: {len(valid_results)}")
    lines.append(f"Failed: {len(invalid_results)}")
    
    # Recommendation
    recommended = [r for r in valid_results if r.get('speed') and r['speed'] >= 1.0]
    if recommended:
        def sort_key(r):
            w, h = map(int, r['resolution'].split('x'))
            return (-w * h, r.get('cpu_percent') or 999)
        recommended.sort(key=sort_key)
        best = recommended[0]
        lines.append(f"\n=== RECOMMENDED SETTING ===")
        out_fps = best.get('output_fps')
        if out_fps:
            lines.append(f"{best['format']} {best['resolution']} @ {best['fps']}fps capture → {out_fps}fps output")
        else:
            lines.append(f"{best['format']} {best['resolution']} @ {best['fps']}fps")
        cpu = f"{best['cpu_percent']:.1f}%" if best['cpu_percent'] else "N/A"
        speed = f"{best['speed']:.2f}x" if best['speed'] else "N/A"
        lines.append(f"CPU: {cpu} | Speed: {speed}")
    
    lines.append("\n=== TEST RESULTS ===")
    if has_output_fps:
        lines.append(f"{'Format':<10} {'Resolution':<12} {'FPS':<6} {'Out':<6} {'CPU %':<8} {'Speed':<8} {'Status'}")
        lines.append("-" * 66)
    else:
        lines.append(f"{'Format':<10} {'Resolution':<12} {'FPS':<6} {'CPU %':<8} {'Speed':<8} {'Status'}")
        lines.append("-" * 60)
    
    # Sort by CPU
    valid_results.sort(key=lambda x: x.get('cpu_percent') or 999)
    
    for r in valid_results:
        cpu = f"{r['cpu_percent']:.1f}%" if r['cpu_percent'] else "N/A"
        speed = f"{r['speed']:.2f}x" if r['speed'] else "N/A"
        cpu_pct = r.get('cpu_percent') or 0
        speed_val = r.get('speed') or 0
        
        if speed_val < 1.0 or cpu_pct > 100:
            status = "Too slow" if speed_val < 1.0 else "CPU overload"
        elif cpu_pct >= 90:
            status = "Straining"
        else:
            status = "Capable"
        
        if has_output_fps:
            out_fps = r.get('output_fps')
            out_str = str(out_fps) if out_fps else "="
            lines.append(f"{r['format']:<10} {r['resolution']:<12} {r['fps']:<6} {out_str:<6} {cpu:<8} {speed:<8} {status}")
        else:
            lines.append(f"{r['format']:<10} {r['resolution']:<12} {r['fps']:<6} {cpu:<8} {speed:<8} {status}")
    
    if invalid_results:
        lines.append("\n=== FAILED COMBINATIONS ===")
        for r in invalid_results:
            out_fps = r.get('output_fps')
            if out_fps:
                lines.append(f"{r['format']} {r['resolution']} @ {r['fps']}fps → {out_fps}fps output - Failed")
            else:
                lines.append(f"{r['format']} {r['resolution']} @ {r['fps']}fps - Failed")
    
    lines.append("\n=== FFMPEG COMMANDS ===")
    for r in results:
        status = "OK" if r['valid'] else "FAILED"
        out_fps = r.get('output_fps')
        if out_fps:
            lines.append(f"\n[{status}] {r['format']} {r['resolution']} @ {r['fps']}fps → {out_fps}fps output:")
        else:
            lines.append(f"\n[{status}] {r['format']} {r['resolution']} @ {r['fps']}fps:")
        lines.append(f"  {r.get('cmd', 'N/A')}")
    
    lines.append("\n" + "=" * 70)
    lines.append("End of Report")
    lines.append("=" * 70)
    
    return "\n".join(lines)

def save_report(device, results, duration, output_fps=None):
    """Prompt user to save report and write file"""
    save = input(f"\n{COLOR_CYAN}Save test report? (y/n):{COLOR_RESET} ").strip().lower()
    if save != 'y':
        return
    
    timestamp = datetime.now().strftime('%y%m%d_%H%M')
    default_name = f"ravens_results_{timestamp}.txt"
    filename = input(f"{COLOR_CYAN}Report filename [{default_name}]:{COLOR_RESET} ").strip()
    if not filename:
        filename = default_name
    
    # Add .txt if no extension
    if '.' not in filename:
        filename += '.txt'
    
    report = generate_report(device, results, duration, output_fps)
    
    try:
        with open(filename, 'w') as f:
            f.write(report)
        print(f"\n✅ Report saved to: {filename}")
    except Exception as e:
        print(f"\n❌ Failed to save report: {e}")

# ===== CPU MEASUREMENT =====

def get_process_cpu(pid, samples=3, interval=1.0):
    """
    Get average CPU usage for a process over multiple samples.
    
    Args:
        pid: Process ID to monitor
        samples: Number of samples to take
        interval: Seconds between samples
    
    Returns:
        Average CPU percentage (float) or None if process not found
    """
    cpu_readings = []
    
    for _ in range(samples):
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "%cpu", "--no-headers"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                cpu = float(result.stdout.strip())
                cpu_readings.append(cpu)
        except (subprocess.TimeoutExpired, ValueError):
            pass
        
        if _ < samples - 1:  # Don't sleep after last sample
            time.sleep(interval)
    
    if cpu_readings:
        return sum(cpu_readings) / len(cpu_readings)
    return None

def get_ffmpeg_stats(stderr_output):
    """
    Parse FFmpeg stderr output to get encoding stats.
    
    Returns:
        dict with 'frames', 'fps', 'time' or None if parsing failed
    """
    # Look for the last status line like:
    # frame=  150 fps= 25 q=-0.0 Lsize=N/A time=00:00:06.00 bitrate=N/A speed=1x
    lines = stderr_output.strip().split('\n')
    
    for line in reversed(lines):
        if 'frame=' in line and 'fps=' in line:
            stats = {}
            
            # Extract frame count
            frame_match = re.search(r'frame=\s*(\d+)', line)
            if frame_match:
                stats['frames'] = int(frame_match.group(1))
            
            # Extract fps
            fps_match = re.search(r'fps=\s*([\d.]+)', line)
            if fps_match:
                stats['fps'] = float(fps_match.group(1))
            
            # Extract time
            time_match = re.search(r'time=(\d+:\d+:[\d.]+)', line)
            if time_match:
                stats['time'] = time_match.group(1)
            
            # Extract speed - handles formats like: speed=1.2x, speed=0.95x, speed=1x, speed=N/A
            speed_match = re.search(r'speed=\s*([\d.]+)x', line)
            if speed_match:
                stats['speed'] = float(speed_match.group(1))
            else:
                # Try alternative format without decimal
                speed_match = re.search(r'speed=\s*(\d+)x', line)
                if speed_match:
                    stats['speed'] = float(speed_match.group(1))
                else:
                    # Check for N/A which means too slow to measure
                    if 'speed=N/A' in line or 'speed= N/A' in line:
                        stats['speed'] = 0.0  # Mark as 0 (too slow)
            
            if stats:
                return stats
    
    return None

# ===== COMBINATION TESTING =====

def extract_ffmpeg_error(stderr_text):
    """
    Extract the meaningful error message from FFmpeg stderr output.
    
    FFmpeg outputs a lot of info to stderr, so we need to find the actual error.
    """
    lines = stderr_text.splitlines()
    
    # Look for common error patterns
    error_patterns = [
        "Error",
        "error",
        "Invalid",
        "Cannot",
        "cannot",
        "No such",
        "not found",
        "Permission denied",
        "Device or resource busy",
        "Input/output error",
        "No space left",
        "failed",
        "Failed",
        "Discarded",
    ]
    
    error_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Skip common info lines
        if line.startswith("frame=") or line.startswith("size="):
            continue
        if "Stream #" in line and "Error" not in line:
            continue
        if line.startswith("Input #") or line.startswith("Output #"):
            continue
        if line.startswith("Duration:") or line.startswith("Metadata:"):
            continue
        
        # Check for error patterns
        for pattern in error_patterns:
            if pattern in line:
                error_lines.append(line)
                break
    
    if error_lines:
        # Return first few error lines
        return " | ".join(error_lines[:3])[:200]
    
    # Fallback: look for last non-empty, non-progress line
    for line in reversed(lines):
        line = line.strip()
        if line and not line.startswith("frame=") and not line.startswith("size="):
            return line[:200]
    
    return "Unknown error"

def test_combination(device, fmt, resolution, fps, duration=10, encoder=None, output_fps=None):
    """
    Test a single camera format/resolution/FPS combination.
    
    Args:
        device: Camera device path (e.g., /dev/video0)
        fmt: Format name (e.g., mjpeg, yuyv422)
        resolution: Resolution string (e.g., 1280x720)
        fps: Frame rate (int) - capture frame rate
        duration: Test duration in seconds
        encoder: Force specific encoder (None = auto-detect)
        output_fps: Output frame rate (None = same as capture, or int to drop frames)
    
    Returns:
        dict with test results:
        {
            'valid': bool,
            'cpu_percent': float or None,
            'actual_fps': float or None,
            'frames_encoded': int or None,
            'speed': float or None,
            'error': str or None,
            'encoder': str,
            'output_fps': int or None
        }
    """
    result = {
        'valid': False,
        'cpu_percent': None,
        'actual_fps': None,
        'frames_encoded': None,
        'speed': None,
        'error': None,
        'encoder': None,
        'cmd': None,  # Store the full command for display
        'output_fps': output_fps  # Store output fps setting
    }
    
    # Determine encoder
    if encoder is None:
        if has_v4l2m2m_encoder():
            encoder = 'h264_v4l2m2m'
        elif has_vaapi_encoder():
            encoder = 'h264_vaapi'
        else:
            encoder = 'libx264'
    
    result['encoder'] = encoder
    
    # Build FFmpeg command
    cmd = [
        "ffmpeg", "-y",
        "-f", "v4l2",
        "-input_format", fmt,
        "-video_size", resolution,
        "-framerate", str(fps),
        "-i", device,
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        "-c:v", encoder,
    ]
    
    # Add encoder-specific options (must come after -c:v)
    if encoder == 'libx264':
        cmd.extend(["-preset", "ultrafast"])
    
    # Add bitrate
    cmd.extend(["-b:v", "2M"])
    
    # Add output frame rate if specified (frame dropping)
    if output_fps is not None and output_fps < fps:
        cmd.extend(["-r", str(output_fps)])
    
    # Add output format
    cmd.extend(["-f", "null", "-"])
    
    # Store command string for display
    result['cmd'] = " ".join(cmd)
    
    try:
        # Start FFmpeg process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait a moment for process to start
        time.sleep(1)
        
        # Check if process is still running
        if process.poll() is not None:
            # Process already exited - likely an error
            _, stderr = process.communicate(timeout=5)
            result['error'] = extract_ffmpeg_error(stderr.decode())
            return result
        
        # Sample CPU usage while FFmpeg is running
        cpu_samples = []
        sample_interval = 0.3  # Sample more frequently
        # Ensure at least 2 samples, scale with duration
        samples_to_take = max(2, int(duration / sample_interval) - 1)
        
        for _ in range(samples_to_take):
            if process.poll() is not None:
                break
            
            try:
                ps_result = subprocess.run(
                    ["ps", "-p", str(process.pid), "-o", "%cpu", "--no-headers"],
                    capture_output=True, text=True, timeout=2
                )
                if ps_result.returncode == 0 and ps_result.stdout.strip():
                    cpu = float(ps_result.stdout.strip())
                    cpu_samples.append(cpu)
            except:
                pass
            
            time.sleep(sample_interval)
        
        # Wait for process to finish
        _, stderr = process.communicate(timeout=duration + 10)
        stderr_text = stderr.decode()
        
        # Check return code
        if process.returncode == 0:
            result['valid'] = True
            
            # Parse FFmpeg stats
            stats = get_ffmpeg_stats(stderr_text)
            if stats:
                result['frames_encoded'] = stats.get('frames')
                result['actual_fps'] = stats.get('fps')
                result['speed'] = stats.get('speed')
            
            # Calculate average CPU
            if cpu_samples:
                result['cpu_percent'] = sum(cpu_samples) / len(cpu_samples)
        else:
            result['error'] = extract_ffmpeg_error(stderr_text)
    
    except subprocess.TimeoutExpired:
        process.kill()
        result['error'] = "Test timed out"
    except Exception as e:
        result['error'] = str(e)
    
    return result

def test_all_combinations(device, formats_dict, progress_callback=None, duration=10, output_fps=None):
    """
    Test all format/resolution/FPS combinations for a device.
    
    Args:
        device: Camera device path
        formats_dict: Dict from parse_formats() {format: {resolution: [fps, ...]}}
        progress_callback: Optional function(current, total, status, cmd) for progress updates
        duration: Test duration in seconds per combination
        output_fps: Output frame rate (None = same as capture)
    
    Returns:
        List of test results with combination info
    """
    results = []
    
    # Quick check if device is accessible
    try:
        # Try to query the device - this will fail if it's busy
        check_result = subprocess.run(
            ["v4l2-ctl", "--device=" + device, "--get-fmt-video"],
            capture_output=True,
            timeout=5
        )
        if check_result.returncode != 0:
            error_msg = check_result.stderr.decode().strip()
            print(f"\n{COLOR_LOW}❌ Cannot access camera {device}{COLOR_RESET}")
            print(f"   Error: {error_msg[:100]}")
            
            # Try to find what's using the device
            try:
                fuser_result = subprocess.run(
                    ["fuser", "-v", device],
                    capture_output=True,
                    timeout=5
                )
                if fuser_result.stdout or fuser_result.stderr:
                    output = (fuser_result.stdout.decode() + fuser_result.stderr.decode()).strip()
                    if output:
                        print(f"\n   Processes using {device}:")
                        for line in output.splitlines()[:5]:
                            print(f"   {line}")
            except FileNotFoundError:
                # fuser not available, try lsof
                try:
                    lsof_result = subprocess.run(
                        ["lsof", device],
                        capture_output=True,
                        timeout=5
                    )
                    if lsof_result.stdout:
                        print(f"\n   Processes using {device}:")
                        for line in lsof_result.stdout.decode().splitlines()[:5]:
                            print(f"   {line}")
                except:
                    pass
            except Exception as e:
                pass
            
            print(f"\n   Try: sudo fuser -k {device}  (to force-kill processes using the camera)")
            input("\nPress Enter to continue...")
            return results
    except Exception as e:
        print(f"\n{COLOR_YELLOW}⚠️  Could not verify device availability: {e}{COLOR_RESET}")
    
    # Count total combinations
    total = sum(
        len(fps_list) 
        for res_dict in formats_dict.values() 
        for fps_list in res_dict.values()
    )
    
    # Determine encoder once (same for all tests)
    if has_v4l2m2m_encoder():
        encoder = 'h264_v4l2m2m'
    elif has_vaapi_encoder():
        encoder = 'h264_vaapi'
    else:
        encoder = 'libx264'
    
    current = 0
    
    for fmt, resolutions in formats_dict.items():
        for resolution, fps_list in resolutions.items():
            for fps in fps_list:
                current += 1
                
                # Build command string for display
                out_fps_str = f" -r {output_fps}" if output_fps and output_fps < fps else ""
                preset_str = " -preset ultrafast" if encoder == 'libx264' else ""
                cmd_preview = f"ffmpeg -y -f v4l2 -input_format {fmt} -video_size {resolution} -framerate {fps} -i {device} -t {duration} -pix_fmt yuv420p -c:v {encoder}{preset_str} -b:v 2M{out_fps_str} -f null -"
                
                if progress_callback:
                    progress_callback(current, total, f"{fmt} {resolution} @ {fps}fps", cmd_preview)
                
                test_result = test_combination(device, fmt, resolution, fps, duration=duration, output_fps=output_fps)
                test_result['format'] = fmt
                test_result['resolution'] = resolution
                test_result['fps'] = fps
                
                results.append(test_result)
    
    return results

# ===== RESULTS MANAGEMENT =====

def save_test_results(device, results):
    """Save test results to JSON file"""
    try:
        # Load existing results
        all_results = {}
        if TEST_RESULTS_PATH.exists():
            with open(TEST_RESULTS_PATH, 'r') as f:
                all_results = json.load(f)
        
        # Update with new results
        all_results[device] = {
            'timestamp': datetime.now().isoformat(),
            'results': results
        }
        
        # Save
        with open(TEST_RESULTS_PATH, 'w') as f:
            json.dump(all_results, f, indent=2)
        
        return True
    except Exception as e:
        print(f"⚠️  Could not save results: {e}")
        return False

def load_test_results(device=None):
    """Load test results from JSON file"""
    try:
        if TEST_RESULTS_PATH.exists():
            with open(TEST_RESULTS_PATH, 'r') as f:
                all_results = json.load(f)
            
            if device:
                return all_results.get(device, {})
            return all_results
    except:
        pass
    return {}

# ===== MENU INTERFACE =====

def display_test_results(results, device_name=None):
    """Display test results in a formatted table"""
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*80}")
    if device_name:
        print(f"📊 Test Results: {device_name}")
    else:
        print("📊 Camera Test Results")
    print(f"{'='*80}{COLOR_RESET}\n")
    
    # Check if any results have output_fps set (frame dropping enabled)
    has_output_fps = any(r.get('output_fps') for r in results)
    
    # Separate valid and invalid
    valid_results = [r for r in results if r['valid']]
    invalid_results = [r for r in results if not r['valid']]
    
    # Find recommended settings (speed >= 1.0, sorted by resolution then lowest CPU)
    recommended = [r for r in valid_results if r.get('speed') and r['speed'] >= 1.0]
    
    if recommended:
        # Sort by resolution (highest first), then by CPU (lowest first)
        def sort_key(r):
            w, h = map(int, r['resolution'].split('x'))
            return (-w * h, r.get('cpu_percent') or 999)
        
        recommended.sort(key=sort_key)
        best = recommended[0]
        
        print(f"{COLOR_HIGH}⭐ RECOMMENDED SETTING:{COLOR_RESET}")
        out_fps = best.get('output_fps')
        if out_fps:
            print(f"   {best['format']} {best['resolution']} @ {best['fps']}fps → {out_fps}fps output")
        else:
            print(f"   {best['format']} {best['resolution']} @ {best['fps']}fps")
        cpu = f"{best['cpu_percent']:.1f}%" if best['cpu_percent'] else "N/A"
        speed = f"{best['speed']:.2f}x" if best['speed'] else "N/A"
        print(f"   CPU: {cpu} | Speed: {speed} | Encoder: {best['encoder']}")
        print()
        
        # Show other good options if available
        other_good = [r for r in recommended[1:] if r.get('speed', 0) >= 1.0][:3]
        if other_good:
            print(f"{COLOR_CYAN}Other good options:{COLOR_RESET}")
            for r in other_good:
                cpu = f"{r['cpu_percent']:.1f}%" if r['cpu_percent'] else "N/A"
                out_fps = r.get('output_fps')
                if out_fps:
                    print(f"   • {r['format']} {r['resolution']} @ {r['fps']}→{out_fps}fps (CPU: {cpu})")
                else:
                    print(f"   • {r['format']} {r['resolution']} @ {r['fps']}fps (CPU: {cpu})")
            print()
    
    if valid_results:
        print(f"{COLOR_HIGH}✅ Working Combinations ({len(valid_results)}):{COLOR_RESET}\n")
        
        if has_output_fps:
            print(f"{'Format':<10} {'Resolution':<12} {'FPS':<6} {'Out':<6} {'CPU %':<8} {'Speed':<8} {'Status'}")
            print("-" * 76)
        else:
            print(f"{'Format':<10} {'Resolution':<12} {'FPS':<6} {'CPU %':<8} {'Speed':<8} {'Status'}")
            print("-" * 70)
        
        # Sort by CPU usage (lowest first)
        valid_results.sort(key=lambda x: x.get('cpu_percent') or 999)
        
        for r in valid_results:
            cpu = f"{r['cpu_percent']:.1f}%" if r['cpu_percent'] else "N/A"
            speed = f"{r['speed']:.2f}x" if r['speed'] else "N/A"
            
            # Determine status and color
            # Green: Speed ≥ 1.0x AND CPU < 90%
            # Yellow: Speed ≥ 1.0x AND CPU 90-100%
            # Red: Speed < 1.0x OR CPU > 100%
            cpu_pct = r.get('cpu_percent') or 0
            speed_val = r.get('speed') or 0
            
            if speed_val < 1.0 or cpu_pct > 100:
                color = COLOR_LOW
                status = "✗ Too slow" if speed_val < 1.0 else "✗ CPU overload"
            elif cpu_pct >= 90:
                color = COLOR_YELLOW
                status = "✓ Straining"
            else:
                color = COLOR_HIGH
                status = "✓ Capable"
            
            if has_output_fps:
                out_fps = r.get('output_fps')
                out_str = str(out_fps) if out_fps else "="
                print(f"{color}{r['format']:<10} {r['resolution']:<12} {r['fps']:<6} {out_str:<6} {cpu:<8} {speed:<8} {status}{COLOR_RESET}")
            else:
                print(f"{color}{r['format']:<10} {r['resolution']:<12} {r['fps']:<6} {cpu:<8} {speed:<8} {status}{COLOR_RESET}")
    
    if invalid_results:
        print(f"\n{COLOR_LOW}❌ Failed Combinations ({len(invalid_results)}):{COLOR_RESET}\n")
        print(f"{'Format':<10} {'Resolution':<12} {'FPS':<6} {'Error'}")
        print("-" * 70)
        
        for r in invalid_results:
            error = r.get('error', 'Unknown error')[:40] if r.get('error') else 'Unknown'
            print(f"{r['format']:<10} {r['resolution']:<12} {r['fps']:<6} {error}")
    
    print()

def clear_mediamtx_paths_for_testing():
    """
    Remove all MediaMTX paths to free cameras for testing.
    Waits for FFmpeg processes to terminate.
    
    Returns:
        List of path names that were removed (for potential restoration)
    """
    removed_paths = []
    
    # Get current paths
    paths = list_mediamtx_paths()
    if not paths:
        return removed_paths
    
    print(f"   Removing {len(paths)} stream path(s)...")
    
    for path_name in paths:
        success, error = delete_mediamtx_path(path_name)
        if success:
            removed_paths.append(path_name)
        else:
            print(f"   ⚠️  Failed to remove {path_name}: {error}")
    
    if removed_paths:
        # Wait for FFmpeg processes to terminate
        print(f"   Waiting for FFmpeg processes to stop...")
        max_wait = 15  # seconds
        
        for i in range(max_wait):
            # Check for any ffmpeg processes (broader search)
            try:
                result = subprocess.run(
                    ["pgrep", "-a", "ffmpeg"],
                    capture_output=True,
                    timeout=2
                )
                if result.returncode != 0:
                    # No matching processes found
                    print(f"   ✓ FFmpeg processes stopped")
                    break
                else:
                    # Show remaining processes
                    procs = result.stdout.decode().strip().split('\n')
                    procs = [p for p in procs if p and 'v4l2' in p.lower()]
                    if not procs:
                        print(f"   ✓ No v4l2 FFmpeg processes found")
                        break
                    if i > 0 and i % 3 == 0:
                        print(f"   ... still waiting ({len(procs)} FFmpeg process(es) running)")
                        for p in procs[:2]:
                            # Show truncated process info
                            print(f"       {p[:70]}...")
            except Exception as e:
                break
            time.sleep(1)
        else:
            # Timeout reached - try to kill remaining processes
            print(f"   ⚠️  Timeout waiting for FFmpeg - attempting to kill...")
            try:
                # Kill any ffmpeg using v4l2
                subprocess.run(["pkill", "-9", "-f", "ffmpeg.*v4l2"], timeout=5)
                time.sleep(2)
                print(f"   Killed remaining FFmpeg processes")
            except:
                pass
        
        # Extra delay to ensure device handles are released by kernel
        print(f"   Waiting for kernel to release device handles...")
        time.sleep(3)
    
    return removed_paths

def offer_reload_configuration():
    """Offer to reload camera configuration after testing"""
    print(f"\n{COLOR_CYAN}Testing complete.{COLOR_RESET}")
    print("\nWould you like to reload your camera configuration?")
    print("This will restart your camera streams in MediaMTX.")
    print("\n  [y] Yes, reload configuration")
    print("  [n] No, return to menu")
    
    choice = input(f"\n{COLOR_CYAN}Select option:{COLOR_RESET} ").strip().lower()
    
    if choice == 'y':
        settings = load_raven_settings()
        if settings:
            print("\n🔄 Reloading camera configuration...")
            results = sync_all_cameras(settings)
            
            # Save settings in case moonraker_uids were updated
            if results.get('settings_modified'):
                save_raven_settings(settings)
            
            mtx_ok = len(results['mediamtx_success'])
            mtx_fail = len(results['mediamtx_failed'])
            print(f"\n✅ Reloaded {mtx_ok} camera(s)" + (f", {mtx_fail} failed" if mtx_fail else ""))
        else:
            print(f"\n{COLOR_LOW}❌ Failed to load settings{COLOR_RESET}")

def camera_test_menu():
    """Main menu for camera testing"""
    
    # Check if MediaMTX API is available and has active streams
    api_available = mediamtx_api_available()
    active_streams = list_active_streams() if api_available else {}
    config_paths = list_mediamtx_paths() if api_available else {}
    
    # Use whichever has entries (active streams are what we care about, but config paths need deleting)
    path_names = list(config_paths.keys()) if config_paths else list(active_streams.keys())
    
    if path_names:
        clear_screen()
        print(f"\n{COLOR_YELLOW}{'='*70}")
        print("⚠️  Active Camera Streams Detected")
        print(f"{'='*70}{COLOR_RESET}")
        print(f"\nFound {len(path_names)} configured stream(s) in MediaMTX:")
        for path in path_names[:5]:  # Show first 5
            print(f"   - {path}")
        if len(path_names) > 5:
            print(f"   ... and {len(path_names) - 5} more")
        
        print("\nCamera testing requires exclusive access to the cameras.")
        print("The streams will be temporarily removed and can be restored after testing.")
        print("\n  [c] Clear streams and continue testing")
        print("  [b] Back to main menu (keep streams running)")
        
        choice = input(f"\n{COLOR_CYAN}Select option:{COLOR_RESET} ").strip().lower()
        
        if choice == 'c':
            print("\n🛑 Clearing MediaMTX streams...")
            removed = clear_mediamtx_paths_for_testing()
            if removed:
                print(f"   ✅ Removed {len(removed)} stream(s)")
            else:
                print(f"   ⚠️  No streams were removed")
            time.sleep(1)
        else:
            return
    
    while True:
        clear_screen()
        print(f"\n{COLOR_CYAN}{'='*70}")
        print("🧪 Camera Combination Tester")
        print(f"{'='*70}{COLOR_RESET}")
        print("\nThis tool tests which camera settings actually work and measures CPU usage.")
        print(f"{COLOR_YELLOW}⚠️  Testing can take several minutes per camera.{COLOR_RESET}")
        
        # List available cameras
        devices = list_video_devices()
        device_names = get_device_names()
        
        if not devices:
            print("\n❌ No video devices found!")
            input("\nPress Enter to continue...")
            return
        
        print("\n📹 Available Cameras:\n")
        valid_devices = []
        
        for dev in devices:
            name = device_names.get(dev, "Unknown")
            # Check if device has valid formats
            raw = run_v4l2ctl(dev, ["--list-formats-ext"])
            if raw:
                formats = parse_formats(raw)
                if formats:
                    valid_devices.append((dev, name, formats))
                    combo_count = sum(len(fps) for res in formats.values() for fps in res.values())
                    print(f"  [{len(valid_devices)}] {dev} - {name}")
                    print(f"      {len(formats)} format(s), {combo_count} combination(s)")
        
        if not valid_devices:
            print("\n❌ No cameras with valid formats found!")
            input("\nPress Enter to continue...")
            return
        
        print(f"\n  [v] View previous test results")
        print(f"  [b] Back to main menu")
        
        choice = input(f"\n{COLOR_CYAN}Select camera to test:{COLOR_RESET} ").strip().lower()
        
        if choice == 'b':
            # Offer to reload camera configuration
            offer_reload_configuration()
            return
        
        if choice == 'v':
            view_saved_results()
            continue
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(valid_devices):
                device, name, formats = valid_devices[idx]
                test_camera_submenu(device, name, formats)
        except ValueError:
            print("❌ Invalid option")
            input("\nPress Enter to continue...")

def test_camera_submenu(device, name, formats):
    """Submenu for testing a specific camera"""
    while True:
        clear_screen()
        print(f"\n{COLOR_CYAN}{'='*70}")
        print(f"🧪 Test Camera: {name}")
        print(f"   Device: {device}")
        print(f"{'='*70}{COLOR_RESET}")
        
        # Count combinations
        combo_count = sum(len(fps) for res in formats.values() for fps in res.values())
        est_time = combo_count * 12  # ~12 seconds per test (default 10s + overhead)
        
        print(f"\n📊 Available: {len(formats)} format(s), {combo_count} combination(s)")
        print(f"⏱️  Estimated time for full test: {est_time // 60}m {est_time % 60}s (at 10s/test)")
        
        print(f"\n  [1] Test ALL combinations")
        print(f"  [2] Test specific format only")
        print(f"  [3] Quick test (best resolution per format)")
        print(f"  [4] View previous results for this camera")
        print(f"  [b] Back")
        
        choice = input(f"\n{COLOR_CYAN}Select option:{COLOR_RESET} ").strip().lower()
        
        if choice == 'b':
            return
        
        elif choice == '1':
            run_full_test(device, name, formats)
        
        elif choice == '2':
            select_format_to_test(device, name, formats)
        
        elif choice == '3':
            run_quick_test(device, name, formats)
        
        elif choice == '4':
            results = load_test_results(device)
            if results and 'results' in results:
                display_test_results(results['results'], name)
                print(f"📅 Tested: {results.get('timestamp', 'Unknown')}")
            else:
                print("\n⚠️  No previous results found for this camera.")
            input("\nPress Enter to continue...")

def get_test_parameters():
    """Prompt user for test duration and output FPS"""
    
    # Get duration
    print(f"\n⏱️  How many seconds should each test run?")
    print(f"   • Longer = more accurate CPU readings")
    print(f"   • Shorter = faster overall testing")
    print(f"   • Recommended: 5-15 seconds")
    
    while True:
        duration_input = input(f"\n{COLOR_CYAN}Duration in seconds [10]:{COLOR_RESET} ").strip()
        
        if duration_input == '':
            duration = 10  # Default
            break
        
        try:
            duration = int(duration_input)
            if duration < 1:
                print(f"{COLOR_YELLOW}⚠️  Minimum duration is 1 second{COLOR_RESET}")
                continue
            if duration > 60:
                print(f"{COLOR_YELLOW}⚠️  Maximum duration is 60 seconds{COLOR_RESET}")
                continue
            break
        except ValueError:
            print(f"{COLOR_LOW}❌ Please enter a number{COLOR_RESET}")
    
    # Get output FPS
    print(f"\n📽️  Output frame rate for testing:")
    print(f"   • Camera captures at its native rate")
    print(f"   • Encoder outputs at the rate you choose")
    print(f"   • Lower = less CPU usage (drops frames)")
    print(f"\n  [1] Same as capture (no frame dropping)")
    print(f"  [2] 15 fps")
    print(f"  [3] 10 fps")
    print(f"  [4] 5 fps")
    print(f"  [c] Custom")
    
    while True:
        fps_input = input(f"\n{COLOR_CYAN}Output FPS [1]:{COLOR_RESET} ").strip().lower()
        
        if fps_input == '' or fps_input == '1':
            output_fps = None  # Same as capture
            break
        elif fps_input == '2':
            output_fps = 15
            break
        elif fps_input == '3':
            output_fps = 10
            break
        elif fps_input == '4':
            output_fps = 5
            break
        elif fps_input == 'c':
            custom = input(f"{COLOR_CYAN}Enter custom output FPS:{COLOR_RESET} ").strip()
            try:
                output_fps = int(custom)
                if output_fps < 1:
                    print(f"{COLOR_YELLOW}⚠️  Minimum is 1 fps{COLOR_RESET}")
                    continue
                if output_fps > 120:
                    print(f"{COLOR_YELLOW}⚠️  Maximum is 120 fps{COLOR_RESET}")
                    continue
                break
            except ValueError:
                print(f"{COLOR_LOW}❌ Please enter a number{COLOR_RESET}")
        else:
            print(f"{COLOR_LOW}❌ Invalid option{COLOR_RESET}")
    
    if output_fps:
        print(f"\n✅ Test settings: {duration}s duration, {output_fps} fps output")
    else:
        print(f"\n✅ Test settings: {duration}s duration, native fps output")
    
    return duration, output_fps

def run_full_test(device, name, formats):
    """Run full test on all combinations"""
    combo_count = sum(len(fps) for res in formats.values() for fps in res.values())
    
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print(f"🧪 Full Camera Test: {name}")
    print(f"{'='*70}{COLOR_RESET}")
    
    print(f"\n📋 What this test does:")
    print(f"   1. For each format/resolution/FPS combination:")
    print(f"      • Runs FFmpeg to capture and encode video")
    print(f"      • Measures CPU usage during encoding")
    print(f"      • Checks if encoding keeps up with real-time (speed ≥ 1.0x)")
    print(f"   2. Identifies which combinations actually work")
    print(f"   3. Recommends the best settings based on results")
    
    # Get test parameters from user
    duration, output_fps = get_test_parameters()
    
    est_time = combo_count * (duration + 2)  # duration + overhead
    print(f"\n📊 Test scope: {combo_count} combinations @ {duration}s each")
    print(f"⏱️  Estimated time: {est_time // 60}m {est_time % 60}s")
    
    confirm = input(f"\n{COLOR_CYAN}Continue? (y/n):{COLOR_RESET} ").strip().lower()
    if confirm != 'y':
        return
    
    print(f"\n{COLOR_CYAN}{'='*70}{COLOR_RESET}")
    print(f"🔄 Running tests... (Ctrl+C to cancel)\n")
    
    def progress(current, total, status, cmd):
        pct = (current / total) * 100
        bar_len = 30
        filled = int(bar_len * current / total)
        bar = '█' * filled + '░' * (bar_len - filled)
        # Simple single-line progress (overwrites itself)
        print(f"\r[{bar}] {pct:.0f}% ({current}/{total}) - {status:<30}", end='', flush=True)
    
    results = test_all_combinations(device, formats, progress, duration=duration, output_fps=output_fps)
    reset_terminal()
    
    # Save results
    save_test_results(device, results)
    
    # Display results
    display_test_results(results, name)
    
    # Offer to show all commands
    show_cmds = input(f"\n{COLOR_CYAN}Show all FFmpeg commands that were run? (y/n):{COLOR_RESET} ").strip().lower()
    if show_cmds == 'y':
        print(f"\n{COLOR_CYAN}{'='*70}")
        print("📋 FFmpeg Commands Run During Test")
        print(f"{'='*70}{COLOR_RESET}\n")
        for r in results:
            status = "✓" if r['valid'] else "✗"
            out_fps = r.get('output_fps')
            if out_fps:
                print(f"{status} {r['format']} {r['resolution']} @ {r['fps']}fps → {out_fps}fps output:")
            else:
                print(f"{status} {r['format']} {r['resolution']} @ {r['fps']}fps:")
            print(f"  {COLOR_YELLOW}{r.get('cmd', 'N/A')}{COLOR_RESET}\n")
    
    # Offer to save report
    save_report(device, results, duration, output_fps)
    
    input("\nPress Enter to continue...")

def select_format_to_test(device, name, formats):
    """Let user select a specific format to test"""
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print(f"🧪 Select Format to Test: {name}")
    print(f"{'='*70}{COLOR_RESET}\n")
    
    format_list = list(formats.keys())
    for i, fmt in enumerate(format_list, 1):
        combo_count = sum(len(fps) for fps in formats[fmt].values())
        print(f"  [{i}] {fmt} ({combo_count} combinations)")
    
    print(f"\n  [b] Back")
    
    choice = input(f"\n{COLOR_CYAN}Select format:{COLOR_RESET} ").strip().lower()
    
    if choice == 'b':
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(format_list):
            fmt = format_list[idx]
            subset = {fmt: formats[fmt]}
            
            combo_count = sum(len(fps) for fps in formats[fmt].values())
            
            # Get test parameters from user
            duration, output_fps = get_test_parameters()
            
            est_time = combo_count * (duration + 2)
            print(f"\n🧪 Testing {combo_count} combinations for {fmt} @ {duration}s each")
            print(f"⏱️  Estimated time: {est_time // 60}m {est_time % 60}s")
            
            confirm = input(f"\n{COLOR_CYAN}Continue? (y/n):{COLOR_RESET} ").strip().lower()
            if confirm != 'y':
                return
            
            def progress(current, total, status, cmd):
                pct = (current / total) * 100
                print(f"\r[{pct:.0f}%] ({current}/{total}) - {status:<30}", end='', flush=True)
            
            print()
            results = test_all_combinations(device, subset, progress, duration=duration, output_fps=output_fps)
            reset_terminal()
            
            display_test_results(results, f"{name} ({fmt})")
            
            # Offer to show all commands
            show_cmds = input(f"\n{COLOR_CYAN}Show all FFmpeg commands that were run? (y/n):{COLOR_RESET} ").strip().lower()
            if show_cmds == 'y':
                print(f"\n{COLOR_CYAN}{'='*70}")
                print("📋 FFmpeg Commands Run During Test")
                print(f"{'='*70}{COLOR_RESET}\n")
                for r in results:
                    status = "✓" if r['valid'] else "✗"
                    out_fps = r.get('output_fps')
                    if out_fps:
                        print(f"{status} {r['format']} {r['resolution']} @ {r['fps']}fps → {out_fps}fps output:")
                    else:
                        print(f"{status} {r['format']} {r['resolution']} @ {r['fps']}fps:")
                    print(f"  {COLOR_YELLOW}{r.get('cmd', 'N/A')}{COLOR_RESET}\n")
            
            # Offer to save report
            save_report(device, results, duration, output_fps)
            
            input("\nPress Enter to continue...")
    except ValueError:
        pass

def run_quick_test(device, name, formats):
    """Quick test - just test the highest resolution for each format"""
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print(f"🧪 Quick Camera Test: {name}")
    print(f"{'='*70}{COLOR_RESET}")
    
    print(f"\n📋 What this test does:")
    print(f"   • Tests only the highest resolution for each format")
    print(f"   • Runs FFmpeg to capture and encode video")
    print(f"   • Measures CPU usage and encoding speed")
    print(f"   • Faster than full test, but may miss optimal settings")
    
    # Get test parameters from user
    duration, output_fps = get_test_parameters()
    
    est_time = len(formats) * (duration + 2)
    print(f"\n📊 Formats to test: {len(formats)} @ {duration}s each")
    print(f"⏱️  Estimated time: ~{est_time} seconds")
    
    confirm = input(f"\n{COLOR_CYAN}Continue? (y/n):{COLOR_RESET} ").strip().lower()
    if confirm != 'y':
        return
    
    print(f"\n{COLOR_CYAN}{'='*70}{COLOR_RESET}")
    print(f"🔄 Running tests...\n")
    
    results = []
    
    for fmt, resolutions in formats.items():
        # Get highest resolution
        best_res = sorted(
            resolutions.keys(),
            key=lambda r: tuple(map(int, r.split('x'))),
            reverse=True
        )[0]
        
        # Get highest FPS for that resolution
        best_fps = max(resolutions[best_res])
        
        out_info = f" → {output_fps}fps" if output_fps else ""
        print(f"  {COLOR_YELLOW}Testing: {fmt} {best_res} @ {best_fps}fps{out_info}{COLOR_RESET}", end='', flush=True)
        
        result = test_combination(device, fmt, best_res, best_fps, duration=duration, output_fps=output_fps)
        result['format'] = fmt
        result['resolution'] = best_res
        result['fps'] = best_fps
        results.append(result)
        
        if result['valid']:
            cpu = f"{result['cpu_percent']:.1f}%" if result['cpu_percent'] else "N/A"
            speed = f"{result['speed']:.2f}x" if result['speed'] else "N/A"
            print(f" → ✅ CPU: {cpu}, Speed: {speed}")
        else:
            print(f" → ❌ Failed")
    
    reset_terminal()
    display_test_results(results, name)
    
    # Offer to show all commands
    show_cmds = input(f"\n{COLOR_CYAN}Show all FFmpeg commands that were run? (y/n):{COLOR_RESET} ").strip().lower()
    if show_cmds == 'y':
        print(f"\n{COLOR_CYAN}{'='*70}")
        print("📋 FFmpeg Commands Run During Test")
        print(f"{'='*70}{COLOR_RESET}\n")
        for r in results:
            status = "✓" if r['valid'] else "✗"
            out_fps = r.get('output_fps')
            if out_fps:
                print(f"{status} {r['format']} {r['resolution']} @ {r['fps']}fps → {out_fps}fps output:")
            else:
                print(f"{status} {r['format']} {r['resolution']} @ {r['fps']}fps:")
            print(f"  {COLOR_YELLOW}{r.get('cmd', 'N/A')}{COLOR_RESET}\n")
    
    # Offer to save report
    save_report(device, results, duration, output_fps)
    
    input("\nPress Enter to continue...")

def view_saved_results():
    """View all saved test results"""
    clear_screen()
    print(f"\n{COLOR_CYAN}{'='*70}")
    print("📊 Saved Test Results")
    print(f"{'='*70}{COLOR_RESET}\n")
    
    all_results = load_test_results()
    
    if not all_results:
        print("⚠️  No saved test results found.")
        input("\nPress Enter to continue...")
        return
    
    device_names = get_device_names()
    
    devices = list(all_results.keys())
    for i, device in enumerate(devices, 1):
        data = all_results[device]
        name = device_names.get(device, "Unknown")
        timestamp = data.get('timestamp', 'Unknown')
        results = data.get('results', [])
        valid_count = sum(1 for r in results if r['valid'])
        
        print(f"  [{i}] {device} - {name}")
        print(f"      {valid_count}/{len(results)} working | Tested: {timestamp[:16]}")
    
    print(f"\n  [b] Back")
    
    choice = input(f"\n{COLOR_CYAN}Select to view details:{COLOR_RESET} ").strip().lower()
    
    if choice == 'b':
        return
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(devices):
            device = devices[idx]
            data = all_results[device]
            name = device_names.get(device, device)
            display_test_results(data['results'], name)
            print(f"📅 Tested: {data.get('timestamp', 'Unknown')}")
            input("\nPress Enter to continue...")
    except ValueError:
        pass

# ===== MODULE EXPORT =====
# Main function to import: camera_test_menu()
