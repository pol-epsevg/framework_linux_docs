"""
Framework Diagnostic Tool - Data Collection Only

A diagnostic data collection tool for Framework laptops and desktops running Linux.
This tool collects hardware facts and system state - it does NOT analyze logs
for issues. Use fw_triage.py for issue detection.

Features:
- Hardware detection (GPU, NVMe, WiFi, RAM, expansion cards)
- Firmware status (fwupd devices, BIOS version, EC version, fingerprint reader)
- Thermal monitoring with AMD/Intel-specific thresholds
- Network connectivity checking
- Sleep/suspend status (sysfs readings)
- Distribution compatibility checking
- System information (kernel, desktop, distro, kernel cmdline, Secure Boot)
- Raw log collection for external analysis
- JSON output for programmatic consumption by fw_triage

Usage:
    python -m framework_diagnostic                    # Interactive menu
    python -m framework_diagnostic --since boot      # Since last boot
    python -m framework_diagnostic --json            # Structured JSON output
    python -m framework_diagnostic --output report.txt
"""

__version__ = '5.3.0'  # Battery detail, bluetooth, power conflict detection, expansion card USB topology
__author__ = 'Framework Diagnostic Contributors'

from .hardware import detect_all_hardware, HardwareInfo
from .thermal import check_current_temperatures, ThermalInfo
from .network import check_network_connectivity, NetworkStatus
from .sleep import check_sleep_status, SleepStatus
from .distro_compat import check_framework_distro_compatibility, CompatibilityResult
from .system_info import detect_system_info, SystemInfo
from .firmware import detect_firmware_info, FirmwareInfo
from .fw12 import detect_fw12_diagnostics, FW12Diagnostics
from .audio import detect_audio, AudioInfo
from .bluetooth import detect_bluetooth, BluetoothInfo

__all__ = [
    'detect_all_hardware',
    'HardwareInfo',
    'check_current_temperatures',
    'ThermalInfo',
    'check_network_connectivity',
    'NetworkStatus',
    'check_sleep_status',
    'SleepStatus',
    'check_framework_distro_compatibility',
    'CompatibilityResult',
    'detect_system_info',
    'SystemInfo',
    'detect_firmware_info',
    'FirmwareInfo',
    'detect_fw12_diagnostics',
    'FW12Diagnostics',
    'detect_audio',
    'AudioInfo',
    'detect_bluetooth',
    'BluetoothInfo',
]
