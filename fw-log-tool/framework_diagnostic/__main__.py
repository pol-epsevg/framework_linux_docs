#!/usr/bin/env python3
"""
Framework Diagnostic Tool - Data Collection Only

Collects hardware facts, thermal status, network status, sleep configuration,
and raw logs. Does NOT analyze logs for issues - use fw_triage.py for that.

Usage:
    python -m framework_diagnostic                    # Interactive menu
    python -m framework_diagnostic --since boot      # Since last boot
    python -m framework_diagnostic --since 24h       # Last 24 hours
    python -m framework_diagnostic -o report.txt    # Custom output file
"""

import argparse
import sys
import os
import json
import subprocess
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from enum import Enum

from .output import (
    print_colored, print_error, print_success, print_info,
    print_warning, show_progress, Color, ReportBuilder
)
from .hardware import detect_all_hardware, format_hardware_report, format_disk_health_report
from .thermal import check_current_temperatures, format_thermal_report
from .network import check_network_connectivity, format_network_report
from .distro_compat import check_framework_distro_compatibility, format_compatibility_report
from .dependencies import ensure_dependencies
from .sleep import check_sleep_status, format_sleep_status_report
from .system_info import detect_system_info, format_system_info_report
from .firmware import detect_firmware_info, format_firmware_report
from .log_summary import extract_activity, format_activity
from .fw12 import detect_fw12_diagnostics, format_fw12_report
from .audio import detect_audio, format_audio_report
from .bluetooth import detect_bluetooth, format_bluetooth_report


def _serialize(obj):
    """Serialize dataclasses/enums to JSON-safe dicts."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _serialize(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, Path):
        return str(obj)
    return obj


def check_root():
    """Check if running with root privileges (needed for some operations)."""
    if os.geteuid() != 0:
        print_info("Some diagnostics require root privileges.")
        print_info("Consider running with: sudo python -m framework_diagnostic")
        return False
    return True


def get_boot_time() -> Optional[datetime]:
    """Get actual system boot time."""
    try:
        # Method 1: Parse /proc/uptime
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.read().split()[0])
            return datetime.now() - timedelta(seconds=uptime_seconds)
    except Exception:
        pass
    
    try:
        # Method 2: Use 'who -b' command
        result = subprocess.run(['who', '-b'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Output like: "         system boot  2024-01-15 08:30"
            parts = result.stdout.strip().split()
            if len(parts) >= 4:
                date_str = f"{parts[-2]} {parts[-1]}"
                return datetime.strptime(date_str, '%Y-%m-%d %H:%M')
    except Exception:
        pass
    
    return None


def get_time_range(choice: int) -> tuple[str, str]:
    """Get start and end time based on menu choice."""
    now = datetime.now()
    
    if choice == 1:
        # Last boot - get actual boot time
        boot_time = get_boot_time()
        if boot_time:
            start = boot_time
        else:
            # Fallback: use journalctl -b behavior (will be handled by journalctl itself)
            start = now - timedelta(days=1)  # Safe fallback
        end = now
    elif choice == 2:
        # Last 24 hours
        start = now - timedelta(hours=24)
        end = now
    else:
        # Default to last boot
        boot_time = get_boot_time()
        if boot_time:
            start = boot_time
        else:
            start = now - timedelta(days=1)
        end = now
    
    return start.strftime('%Y-%m-%d %H:%M'), end.strftime('%Y-%m-%d %H:%M')


def interactive_menu() -> tuple[int, Optional[str], Optional[str], Optional[int]]:
    """Display interactive menu and get user choice."""
    print()
    print_colored("Framework Diagnostic Tool", Color.CYAN, bold=True)
    print_colored("=" * 40, Color.CYAN)
    print()
    print("Choose analysis time range:")
    print("  1. Last X minutes")
    print("  2. Last 24 hours")
    print("  3. Custom time range")
    print()
    
    try:
        choice = int(input("Enter choice (1-3): "))
    except (ValueError, KeyboardInterrupt):
        print()
        return 0, None, None, None
    
    if choice == 1:
        print()
        try:
            minutes = int(input("Enter number of minutes: "))
            now = datetime.now()
            start_time = (now - timedelta(minutes=minutes)).strftime('%Y-%m-%d %H:%M')
            end_time = now.strftime('%Y-%m-%d %H:%M')
            return choice, start_time, end_time, minutes
        except (ValueError, KeyboardInterrupt):
            print()
            return 0, None, None, None
    elif choice == 2:
        start_time, end_time = get_time_range(2)
        return choice, start_time, end_time, None
    elif choice == 3:
        print()
        start_time = input("Enter start time (YYYY-MM-DD HH:MM): ")
        end_time = input("Enter end time (YYYY-MM-DD HH:MM): ")
        return choice, start_time, end_time, None
    else:
        return choice, None, None, None


def get_dmesg_output() -> str:
    """Get kernel ring buffer via dmesg.
    
    This is a different data source from journalctl — the ring buffer can
    have early boot messages that journald missed because it wasn't running yet.
    Timestamps stripped: kernel timestamps are unreliable after suspend cycles.
    Returns the entire ring buffer (no time filtering available).
    """
    try:
        result = subprocess.run(
            ['sudo', 'dmesg', '--notime'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def get_journalctl_output(since: str, until: str) -> str:
    """Get journalctl output for a time range."""
    try:
        result = subprocess.run(
            ['sudo', 'journalctl', '--no-pager', f'--since={since}', f'--until={until}'],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def run_diagnostics(
    start_time: str,
    end_time: str,
    output_file: str = "diagnostic_output.txt",
) -> int:
    """
    Run diagnostic data collection.
    
    Collects hardware facts, thermal status, network, sleep configuration,
    and logs with key event callouts. Does NOT analyze logs for issues.
    
    Returns:
        Exit code (0 = success)
    """
    report = ReportBuilder()
    
    # Header
    report.add_line("=" * 60)
    report.add_line("FRAMEWORK DIAGNOSTIC REPORT")
    report.add_line(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.add_line(f"Time Range: {start_time} to {end_time}")
    report.add_line("=" * 60)
    report.add_line()
    report.add_line("NOTE: This report contains diagnostic DATA only.")
    report.add_line()
    
    # System information
    print_info("Detecting system information...")
    sys_info = detect_system_info()
    for line in format_system_info_report(sys_info):
        report.add_line(line)
    report.add_line()
    
    # Hardware detection
    print_info("Detecting hardware...")
    hw = detect_all_hardware()
    
    # Framework info
    if hw.framework.is_framework:
        report.add_line("Framework Device:")
        report.add_line(f"  Product: {hw.framework.product_name}")
        report.add_line(f"  Model: {hw.framework.model_version}")
        if hw.framework.model_type:
            report.add_line(f"  Type: {hw.framework.model_type}")
        report.add_line(f"  BIOS: {hw.framework.bios_version}")
        
        # Power status
        report.add_line("  Power Status:")
        ac_str = "Connected" if hw.framework.ac_connected else "Disconnected"
        report.add_line(f"    AC Power: {ac_str}")
        
        if hw.framework.battery_level is not None:
            bat_str = f"{hw.framework.battery_level}% ({hw.framework.battery_status})"
            report.add_line(f"    Battery: {bat_str}")
            if hw.framework.battery_health_pct is not None:
                health_icon = "✅" if hw.framework.battery_health_pct >= 80 else "⚠️" if hw.framework.battery_health_pct >= 60 else "❌"
                cap_str = ""
                if hw.framework.battery_full_wh and hw.framework.battery_design_wh:
                    cap_str = f" ({hw.framework.battery_full_wh} / {hw.framework.battery_design_wh} Wh)"
                report.add_line(f"    Battery Health: {health_icon} {hw.framework.battery_health_pct}% of design capacity{cap_str}")
            if hw.framework.battery_cycle_count is not None:
                report.add_line(f"    Cycle Count: {hw.framework.battery_cycle_count}")
            if hw.framework.battery_charge_rate_w is not None and hw.framework.battery_charge_rate_w > 0:
                direction = "charging" if hw.framework.battery_status in ("Charging", "Full") else "discharging"
                report.add_line(f"    Power Draw: {hw.framework.battery_charge_rate_w} W ({direction})")
            if hw.framework.battery_charge_limit_pct is not None:
                report.add_line(f"    Charge Limit: {hw.framework.battery_charge_limit_pct}%")
        
        if hw.framework.expansion_cards:
            if hw.framework.expansion_card_ports:
                report.add_line("  Expansion Cards:")
                for card_name, port_path in hw.framework.expansion_card_ports:
                    report.add_line(f"    {card_name} (USB port: {port_path})")
                # List any cards that didn't get a port mapping
                mapped_names = {name for name, _ in hw.framework.expansion_card_ports}
                for card in hw.framework.expansion_cards:
                    if card not in mapped_names:
                        report.add_line(f"    {card}")
            else:
                report.add_line(f"  Expansion Cards: {', '.join(hw.framework.expansion_cards)}")
        else:
            report.add_line("  Expansion Cards: None detected (USB-A/USB-C cards are passive and invisible to software)")
        
        report.add_line()
    
    # Hardware context
    for line in format_hardware_report(hw):
        report.add_line(line)
    report.add_line()
    
    # Disk health
    disk_health_lines = format_disk_health_report(hw)
    if disk_health_lines:
        for line in disk_health_lines:
            report.add_line(line)
        report.add_line()
    
    # Thermal check
    print_info("Checking temperatures...")
    thermal = check_current_temperatures(
        hw.cpu_vendor, hw.amd_generation, hw.framework.is_framework
    )
    for line in format_thermal_report(thermal, hw.cpu_vendor, hw.amd_generation, hw.framework.is_framework):
        report.add_line(line)
    report.add_line()
    
    # Network check
    print_info("Checking network connectivity...")
    network = check_network_connectivity()
    for line in format_network_report(network):
        report.add_line(line)
    report.add_line()
    
    # Audio check
    print_info("Checking audio configuration...")
    audio = detect_audio()
    audio_lines = format_audio_report(audio)
    for line in audio_lines:
        report.add_line(line)
    report.add_line()
    
    # Bluetooth check
    print_info("Checking bluetooth...")
    bt = detect_bluetooth()
    # Cross-reference rfkill state from hardware detection
    for rfdev in hw.rfkill_devices:
        if rfdev.device_type.lower() == 'bluetooth':
            bt.soft_blocked = rfdev.soft_blocked
            bt.hard_blocked = rfdev.hard_blocked
            break
    for line in format_bluetooth_report(bt):
        report.add_line(line)
    report.add_line()
    
    # Sleep status
    print_info("Checking sleep/suspend status...")
    sleep_status = check_sleep_status()
    for line in format_sleep_status_report(sleep_status):
        report.add_line(line)
    report.add_line()
    
    # Firmware status
    print_info("Checking firmware status...")
    firmware = detect_firmware_info(
        bios_version=hw.framework.bios_version,
        is_framework=hw.framework.is_framework
    )
    for line in format_firmware_report(firmware):
        report.add_line(line)
    report.add_line()
    
    # Distro compatibility
    if hw.framework.is_framework:
        print_info("Checking distribution compatibility...")
        compat = check_framework_distro_compatibility(
            hw.framework.product_name,
            hw.framework.model_version,
            hw.cpu_model
        )
        if compat:
            for line in format_compatibility_report(compat):
                report.add_line(line)
            report.add_line()
    
    # Framework 12 specific diagnostics
    if hw.framework.is_framework:
        fw12_diag = detect_fw12_diagnostics(hw.framework.model_type, sys_info.desktop_environment)
        if fw12_diag.is_fw12:
            print_info("Running Framework 12 diagnostics...")
            fw12_lines = format_fw12_report(fw12_diag)
            for line in fw12_lines:
                report.add_line(line)
            report.add_line()
    
    # Collect logs
    print_info("Collecting system logs...")
    dmesg_output = get_dmesg_output()
    journal_output = get_journalctl_output(start_time, end_time)
    
    # Scan logs for activity summary.
    # Journalctl is primary (has systemd + kernel, proper timestamps).
    # Dmesg is secondary — the ring buffer covers the entire boot, so it
    # catches critical errors and suspend cycles that fall outside the
    # journalctl time window.  We merge dmesg findings into the journalctl
    # results to avoid the double-counting bug while still detecting
    # everything in the ring buffer.
    if journal_output or dmesg_output:
        print_info("Scanning logs for activity summary...")
        
        # Primary scan: journalctl (lifecycle events, service failures)
        activity = extract_activity(journal_output) if journal_output else extract_activity('')
        
        # Secondary scan: dmesg (critical checks, suspend cycles)
        if dmesg_output:
            dmesg_activity = extract_activity(dmesg_output)
            
            # Merge critical findings from dmesg that journalctl missed
            journal_found_labels = {label for label, _ in activity.critical_found}
            for label, conf in dmesg_activity.critical_found:
                if label not in journal_found_labels:
                    activity.critical_found.append((label, conf))
                    activity.critical_clear = [
                        (l, c) for l, c in activity.critical_clear if l != label
                    ]
            
            # Use dmesg suspend cycles if journalctl had fewer
            # (dmesg ring buffer covers entire boot, journalctl is time-windowed)
            if len(dmesg_activity.suspend_cycles) > len(activity.suspend_cycles):
                activity.suspend_cycles = dmesg_activity.suspend_cycles
            
            # Merge xHCI workaround status from dmesg
            if dmesg_activity.xhci_hc_died:
                activity.xhci_hc_died = True
                activity.xhci_fix_installed = dmesg_activity.xhci_fix_installed
                activity.xhci_fix_service_enabled = dmesg_activity.xhci_fix_service_enabled
        
        show_progress(100, "Log scan complete")
        
        for line in format_activity(activity, time_range=f'{start_time} to {end_time}'):
            report.add_line(line)
    
    # Output each log source as its own section, unmodified
    if journal_output:
        report.add_line("=" * 60)
        report.add_line(f"JOURNALCTL ({start_time} to {end_time})")
        report.add_line("=" * 60)
        report.add_line()
        report.add_line(journal_output)
        report.add_line()
    
    if dmesg_output:
        report.add_line("=" * 60)
        report.add_line("DMESG (kernel ring buffer — ENTIRE buffer, no time filtering)")
        report.add_line("NOTE: Timestamps stripped due to kernel bug causing incorrect")
        report.add_line("dates after suspend/resume cycles. Use journalctl above for")
        report.add_line("time-accurate logs.")
        report.add_line("=" * 60)
        report.add_line()
        report.add_line(dmesg_output)
        report.add_line()
    
    if not journal_output and not dmesg_output:
        report.add_line("=" * 60)
        report.add_line("SYSTEM LOGS")
        report.add_line("=" * 60)
        report.add_line()
        report.add_line("  (no log content available — are you running with sudo?)")
        report.add_line()
    
    # Write report
    output_path = Path(output_file)
    with open(str(output_path), 'w') as f:
        f.write(report.get_content())
    
    # Print summary
    print()
    print_success("Diagnostic collection complete!")
    print_colored(f"📋 Report saved to: {output_path.absolute()}", Color.CYAN, bold=True)
    
    # Quick summary
    print()
    print_colored("Quick Summary:", Color.CYAN, bold=True)
    
    if hw.framework.is_framework:
        print_colored(f"  🖥️  {hw.framework.product_name}", Color.GREEN)
    
    print_colored(f"  🐧 Kernel: {sys_info.kernel_version}", Color.BLUE)
    
    if sys_info.desktop_environment:
        print_colored(f"  🖥️  Desktop: {sys_info.desktop_environment} ({sys_info.session_type})", Color.BLUE)
    
    if thermal.cpu_temp:
        print_colored(f"  🌡️  CPU Temp: {thermal.cpu_temp}°C", Color.BLUE)
    
    if sleep_status.current_mode.value == 's2idle':
        print_colored("  😴 Sleep: s2idle (modern standby) ✅", Color.GREEN)
    else:
        print_colored(f"  😴 Sleep: {sleep_status.current_mode.value}", Color.YELLOW)
    
    if network.internet_working:
        print_colored("  🌐 Internet: Connected ✅", Color.GREEN)
    else:
        print_colored("  🌐 Internet: Not connected ❌", Color.RED)
    
    # BIOS version
    if firmware.bios_version:
        print_colored(f"  💾 BIOS: {firmware.bios_version}", Color.BLUE)
    
    # Firmware updates
    if firmware.updates_available > 0:
        print_colored(f"  📦 {firmware.updates_available} firmware update(s) available", Color.YELLOW)
    
    # Fingerprint
    if firmware.fingerprint.detected:
        print_colored(f"  👆 Fingerprint: {firmware.fingerprint.model}", Color.BLUE)
    
    # Next steps
    print()
    print_colored("Next Steps:", Color.CYAN, bold=True)
    print(f"  Locate \"{output_path}\" in the directory you downloaded this script into,")
    print(f"  send the {output_path.name} to support for your support ticket.")
    
    if hw.framework.is_framework:
        print()
        print_colored("Framework Resources:", Color.CYAN, bold=True)
        print(f"  Support: https://frame.work/support")
        print(f"  Community: https://community.frame.work/")
        print(f"  Linux Docs: https://github.com/FrameworkComputer/linux-docs")
    
    return 0


def run_json_diagnostics(
    start_time: str,
    end_time: str,
    output_file: str = "diagnostic_output.json"
) -> int:
    """
    Run diagnostic collection and output structured JSON.
    
    Same data collection as run_diagnostics but outputs a JSON file
    that fw_triage or other tools can consume programmatically without
    re-running hardware detection commands.
    
    Returns:
        Exit code (0 = success)
    """
    data = {
        'meta': {
            'generated': datetime.now().isoformat(),
            'time_range': {'start': start_time, 'end': end_time},
            'tool_version': '5.3.0',
        }
    }
    
    # System info
    print_info("Detecting system information...")
    sys_info = detect_system_info()
    data['system_info'] = _serialize(sys_info)
    
    # Hardware
    print_info("Detecting hardware...")
    hw = detect_all_hardware()
    data['hardware'] = _serialize(hw)
    
    # Thermal
    print_info("Checking temperatures...")
    thermal = check_current_temperatures(
        hw.cpu_vendor, hw.amd_generation, hw.framework.is_framework
    )
    data['thermal'] = _serialize(thermal)
    
    # Network
    print_info("Checking network...")
    network = check_network_connectivity()
    data['network'] = _serialize(network)
    
    # Audio
    print_info("Checking audio...")
    audio = detect_audio()
    data['audio'] = _serialize(audio)
    
    # Bluetooth
    print_info("Checking bluetooth...")
    bt = detect_bluetooth()
    for rfdev in hw.rfkill_devices:
        if rfdev.device_type.lower() == 'bluetooth':
            bt.soft_blocked = rfdev.soft_blocked
            bt.hard_blocked = rfdev.hard_blocked
            break
    data['bluetooth'] = _serialize(bt)
    
    # Sleep
    print_info("Checking sleep status...")
    sleep_status = check_sleep_status()
    data['sleep'] = _serialize(sleep_status)
    
    # Firmware
    print_info("Checking firmware...")
    firmware = detect_firmware_info(
        bios_version=hw.framework.bios_version,
        is_framework=hw.framework.is_framework
    )
    data['firmware'] = _serialize(firmware)
    
    # Distro compat
    if hw.framework.is_framework:
        compat = check_framework_distro_compatibility(
            hw.framework.product_name,
            hw.framework.model_version,
            hw.cpu_model
        )
        if compat:
            data['distro_compatibility'] = _serialize(compat)
    
    # FW12 diagnostics
    if hw.framework.is_framework:
        fw12_diag = detect_fw12_diagnostics(hw.framework.model_type, sys_info.desktop_environment)
        if fw12_diag.is_fw12:
            data['fw12_diagnostics'] = _serialize(fw12_diag)
    
    # Raw logs
    print_info("Collecting logs...")
    dmesg_output = get_dmesg_output()
    journal_output = get_journalctl_output(start_time, end_time)
    data['logs'] = {
        'dmesg': dmesg_output if dmesg_output else None,
        'journalctl': journal_output if journal_output else None,
    }
    
    # Write JSON
    output_path = Path(output_file)
    with open(str(output_path), 'w') as f:
        json.dump(data, f, indent=2, default=str)
    
    print()
    print_success("Diagnostic collection complete!")
    print_colored(f"📋 JSON report saved to: {output_path.absolute()}", Color.CYAN, bold=True)
    print_info("This file can be consumed by fw_triage.py or other analysis tools.")
    
    return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Framework Diagnostic Tool - Data Collection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This tool collects diagnostic data. It does NOT analyze logs for issues.
For issue detection, use fw_triage.py on the generated output.

Examples:
  python -m framework_diagnostic                    Interactive menu
  python -m framework_diagnostic --since boot      Since last boot
  python -m framework_diagnostic --since 24h       Last 24 hours
  python -m framework_diagnostic -o report.txt    Custom output file
        """
    )
    
    parser.add_argument(
        '--since',
        choices=['boot', '24h'],
        help='Time range for log collection'
    )
    
    parser.add_argument(
        '--output', '-o',
        default='diagnostic_output.txt',
        help='Output file path (default: diagnostic_output.txt)'
    )
    
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Minimal output'
    )
    
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output structured JSON (for consumption by fw_triage or other tools)'
    )
    
    args = parser.parse_args()
    
    # Check root
    check_root()
    
    # Check and install dependencies
    if not args.quiet:
        print_info("Checking dependencies...")
    if not ensure_dependencies(auto_install=True, quiet=args.quiet):
        print_warning("Some required tools are missing - results may be incomplete")
    
    # Pick the runner function based on --json flag
    extra_kwargs = {}
    if args.json:
        runner = run_json_diagnostics
        # Default JSON extension if user didn't override output
        if args.output == 'diagnostic_output.txt':
            args.output = 'diagnostic_output.json'
    else:
        runner = run_diagnostics
    
    # Determine mode
    if args.since:
        if args.since == 'boot':
            start_time, end_time = get_time_range(1)
        else:
            start_time, end_time = get_time_range(2)
        
        exit_code = runner(
            start_time, end_time,
            output_file=args.output,
            **extra_kwargs
        )
    else:
        # Interactive menu
        choice, start_time, end_time, minutes = interactive_menu()
        
        if choice == 0:
            print()
            print_info("Cancelled.")
            sys.exit(0)
        elif start_time and end_time:
            exit_code = runner(
                start_time, end_time,
                output_file=args.output,
                **extra_kwargs
            )
        else:
            print_error("Invalid choice")
            sys.exit(1)
    
    sys.exit(exit_code)


if __name__ == '__main__':
    main()

