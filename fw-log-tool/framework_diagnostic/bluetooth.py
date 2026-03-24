"""
Bluetooth adapter and device detection.

Reads adapter status and connected/paired devices from bluetoothctl.
Also checks rfkill for Bluetooth soft/hard block state.
"""

import re
from dataclasses import dataclass, field

from .utils import run_command


@dataclass
class BluetoothDevice:
    """A paired or connected Bluetooth device."""
    address: str  # MAC address
    name: str = ""
    connected: bool = False


@dataclass
class BluetoothInfo:
    """Bluetooth subsystem status."""
    adapter_present: bool = False
    adapter_name: str = ""       # e.g. "hci0"
    adapter_powered: bool = False
    adapter_address: str = ""    # MAC address
    
    # Devices
    connected_devices: list[BluetoothDevice] = field(default_factory=list)
    paired_device_count: int = 0
    
    # Block state (from rfkill, already detected in hardware.py)
    soft_blocked: bool = False
    hard_blocked: bool = False


def detect_bluetooth() -> BluetoothInfo:
    """Detect Bluetooth adapter status and connected devices.
    
    Checks sysfs first for adapter presence (reliable across all distros),
    then uses bluetoothctl for device details if available.
    """
    info = BluetoothInfo()
    
    # Check sysfs for adapter — this is the ground truth
    rc, stdout, _ = run_command(['bash', '-c',
        'ls /sys/class/bluetooth/ 2>/dev/null'])
    if rc == 0 and stdout.strip():
        adapter_name = stdout.strip().split('\n')[0]  # e.g. "hci0"
        info.adapter_present = True
        info.adapter_name = adapter_name
        
        # Read adapter address from sysfs
        rc2, addr, _ = run_command(['bash', '-c',
            f'cat /sys/class/bluetooth/{adapter_name}/address 2>/dev/null'])
        if rc2 == 0 and addr.strip():
            info.adapter_address = addr.strip()
        
        # Fallback: D-Bus for address
        if not info.adapter_address:
            rc2, addr, _ = run_command(['busctl', 'get-property', 'org.bluez',
                f'/org/bluez/{adapter_name}', 'org.bluez.Adapter1', 'Address'],
                timeout=5)
            if rc2 == 0 and '"' in addr:
                info.adapter_address = addr.split('"')[1]
    
    # Check rfkill for block state
    rc, stdout, _ = run_command(['bash', '-c',
        'rfkill -J 2>/dev/null || rfkill list bluetooth 2>/dev/null'])
    if rc == 0 and stdout.strip():
        lower = stdout.lower()
        if 'soft blocked: yes' in lower or '"soft": "blocked"' in lower:
            info.soft_blocked = True
        if 'hard blocked: yes' in lower or '"hard": "blocked"' in lower:
            info.hard_blocked = True
    
    if not info.adapter_present:
        return info
    
    # Check powered state via D-Bus (BlueZ exposes this reliably)
    rc, stdout, _ = run_command(['busctl', 'get-property', 'org.bluez',
        f'/org/bluez/{info.adapter_name}', 'org.bluez.Adapter1', 'Powered'],
        timeout=5)
    if rc == 0 and 'true' in stdout.lower():
        info.adapter_powered = True
    
    # Fallback: bluetoothctl for powered
    if not info.adapter_powered:
        rc, stdout, _ = run_command(['bluetoothctl', 'show'], timeout=5)
        if rc == 0:
            if 'Powered: yes' in stdout:
                info.adapter_powered = True
    
    # Connected devices (bluetoothctl)
    rc, stdout, _ = run_command(['bluetoothctl', 'devices', 'Connected'], timeout=5)
    if rc == 0:
        for line in stdout.strip().split('\n'):
            if not line.strip():
                continue
            match = re.match(r'Device\s+([0-9A-Fa-f:]{17})\s+(.*)', line.strip())
            if match:
                dev = BluetoothDevice(
                    address=match.group(1),
                    name=match.group(2).strip(),
                    connected=True,
                )
                info.connected_devices.append(dev)
    
    # Paired device count
    rc, stdout, _ = run_command(['bluetoothctl', 'devices', 'Paired'], timeout=5)
    if rc == 0:
        count = 0
        for line in stdout.strip().split('\n'):
            if line.strip().startswith('Device '):
                count += 1
        info.paired_device_count = count
    
    return info


def format_bluetooth_report(info: BluetoothInfo) -> list[str]:
    """Format Bluetooth info for the diagnostic report."""
    lines = []
    
    lines.append("Bluetooth:")
    
    if not info.adapter_present:
        lines.append("  Adapter: ❌ Not detected")
        return lines
    
    # Adapter status
    power_str = "✅ Powered on" if info.adapter_powered else "❌ Powered off"
    lines.append(f"  Adapter: {info.adapter_address} ({power_str})")
    
    # Block state
    if info.hard_blocked:
        lines.append("  ❌ Hardware blocked (rfkill)")
    if info.soft_blocked:
        lines.append("  ⚠️ Software blocked (rfkill)")
    
    # Connected devices
    if info.connected_devices:
        for dev in info.connected_devices:
            lines.append(f"  Connected: {dev.name} ({dev.address})")
    else:
        lines.append("  Connected: None")
    
    # Paired count
    if info.paired_device_count > 0:
        lines.append(f"  Paired devices: {info.paired_device_count}")
    
    return lines
