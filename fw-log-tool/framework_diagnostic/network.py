"""
Network connectivity checking.

Detects: internet connectivity, WiFi/Ethernet status, IP addresses,
DNS servers, VPN connections (OpenVPN, WireGuard, NetworkManager VPNs),
and WiFi power save state.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .utils import run_command


def _get_wifi_interface() -> str:
    """Get the primary wireless interface name from iw dev.
    
    Returns interface name (e.g. 'wlan0', 'wlp1s0') or empty string.
    """
    rc, stdout, _ = run_command(['iw', 'dev'])
    if rc != 0:
        return ""
    
    # iw dev output:
    #   phy#0
    #     Interface wlan0
    for line in stdout.split('\n'):
        stripped = line.strip()
        if stripped.startswith('Interface '):
            return stripped.split()[1]
    return ""


def _check_wifi_power_save(interface: str) -> Optional[bool]:
    """Check WiFi power save state via iw.
    
    Returns True if on, False if off, None if unknown.
    """
    if not interface:
        return None
    
    rc, stdout, _ = run_command(['iw', 'dev', interface, 'get', 'power_save'])
    if rc != 0:
        return None
    
    # Output: "Power save: on" or "Power save: off"
    output = stdout.strip().lower()
    if 'power save: on' in output:
        return True
    elif 'power save: off' in output:
        return False
    return None


# ── IP address detection ─────────────────────────────────────────────

@dataclass
class InterfaceAddress:
    """One IP address on one interface."""
    interface: str      # e.g. "wlan0", "enp1s0"
    address: str        # e.g. "192.168.1.100/24", "fe80::1/64"
    family: str         # "ipv4" or "ipv6"


def _detect_ip_addresses() -> list:
    """Get all IP addresses on all interfaces via 'ip addr show'.

    Uses 'ip' from iproute2 (present on every modern Linux distro).
    Skips loopback (lo).
    Returns list of InterfaceAddress.
    """
    results = []

    rc, stdout, _ = run_command(['ip', '-o', 'addr', 'show'])
    if rc != 0:
        return results

    # -o gives one-line-per-address output:
    # 2: enp1s0    inet 192.168.1.100/24 brd 192.168.1.255 scope global enp1s0
    # 2: enp1s0    inet6 fe80::1/64 scope link
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue

        iface = parts[1].rstrip(':')
        if iface == 'lo':
            continue

        proto = parts[2]  # "inet" or "inet6"
        addr = parts[3]   # e.g. "192.168.1.100/24"

        if proto == 'inet':
            results.append(InterfaceAddress(iface, addr, 'ipv4'))
        elif proto == 'inet6':
            # Skip link-local (fe80::) — noise for diagnostics
            if addr.startswith('fe80:'):
                continue
            results.append(InterfaceAddress(iface, addr, 'ipv6'))

    return results


# ── DNS detection ────────────────────────────────────────────────────

def _detect_dns_servers() -> list:
    """Detect configured DNS servers.

    Strategy (in order):
    1. resolvectl status — systemd-resolved (Ubuntu, Fedora, Arch default)
    2. nmcli dev show — NetworkManager (fallback)
    3. /etc/resolv.conf — universal last resort

    Returns list of DNS server address strings (deduped, order preserved).
    """
    servers = []
    seen = set()

    def _add(addr: str):
        addr = addr.strip()
        if addr and addr not in seen:
            seen.add(addr)
            servers.append(addr)

    # Method 1: resolvectl (systemd-resolved)
    rc, stdout, _ = run_command(['resolvectl', 'status'], timeout=5)
    if rc == 0:
        # Lines like: "DNS Servers: 8.8.8.8 1.1.1.1"
        # or:         "DNS Servers: 8.8.8.8"
        # or:         "Current DNS Server: 8.8.8.8"
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith('DNS Servers:') or stripped.startswith('Current DNS Server:'):
                parts = stripped.split(':', 1)[1].strip().split()
                for p in parts:
                    _add(p)
        if servers:
            return servers

    # Method 2: nmcli (NetworkManager)
    rc, stdout, _ = run_command(
        ['nmcli', '-t', '-f', 'IP4.DNS,IP6.DNS', 'dev', 'show'], timeout=5
    )
    if rc == 0:
        for line in stdout.splitlines():
            # Format: IP4.DNS[1]:8.8.8.8
            if line.startswith('IP4.DNS') or line.startswith('IP6.DNS'):
                parts = line.split(':', 1)
                if len(parts) == 2:
                    _add(parts[1])
        if servers:
            return servers

    # Method 3: /etc/resolv.conf (universal)
    resolv = Path('/etc/resolv.conf')
    if resolv.exists():
        try:
            for line in resolv.read_text().splitlines():
                line = line.strip()
                if line.startswith('nameserver '):
                    parts = line.split()
                    if len(parts) >= 2:
                        _add(parts[1])
        except Exception:
            pass

    return servers


# ── VPN detection ────────────────────────────────────────────────────

@dataclass
class VPNConnection:
    """A detected VPN connection."""
    name: str       # connection name or interface name
    vpn_type: str   # "wireguard", "openvpn", "vpn" (generic NM VPN)
    interface: str   # e.g. "wg0", "tun0", ""
    active: bool     # True if currently up


def _detect_vpn_connections() -> list:
    """Detect active VPN connections.

    Detection methods (all run, results deduped by interface):
    1. WireGuard: 'ip link show type wireguard' (kernel-native, no extra tools)
    2. OpenVPN: tun/tap interfaces + process detection via 'pgrep -a openvpn'
    3. NetworkManager: 'nmcli -t connection show --active' filtered by type

    Does NOT require wireguard-tools or openvpn packages — detection only.
    Returns list of VPNConnection.
    """
    vpns = []
    seen_interfaces = set()

    # ── WireGuard (kernel interface type) ────────────────────────
    rc, stdout, _ = run_command(['ip', 'link', 'show', 'type', 'wireguard'])
    if rc == 0 and stdout.strip():
        # Output like: "4: wg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1420 ..."
        for line in stdout.splitlines():
            m = re.match(r'^\d+:\s+(\S+):', line)
            if m:
                iface = m.group(1)
                if iface not in seen_interfaces:
                    seen_interfaces.add(iface)
                    vpns.append(VPNConnection(
                        name=iface,
                        vpn_type='wireguard',
                        interface=iface,
                        active=True,
                    ))

    # ── OpenVPN (tun/tap + process) ──────────────────────────────
    # First check if openvpn is running at all (avoids false positives
    # from tun/tap interfaces used by other software)
    rc_pgrep, pgrep_out, _ = run_command(['pgrep', '-a', 'openvpn'], timeout=3)
    openvpn_running = (rc_pgrep == 0 and 'openvpn' in pgrep_out)

    if openvpn_running:
        rc, stdout, _ = run_command(['ip', '-o', 'link', 'show'])
        if rc == 0:
            for line in stdout.splitlines():
                parts = line.split()
                if len(parts) < 2:
                    continue
                iface = parts[1].rstrip(':')
                # tun0, tun1, tap0, tap1 etc — common OpenVPN interfaces
                if re.match(r'^(tun|tap)\d+$', iface) and iface not in seen_interfaces:
                    seen_interfaces.add(iface)
                    vpns.append(VPNConnection(
                        name=f'OpenVPN ({iface})',
                        vpn_type='openvpn',
                        interface=iface,
                        active=True,
                    ))

    # ── NetworkManager VPNs ──────────────────────────────────────
    # Catches NM-managed VPNs: OpenConnect, VPNC, PPTP, L2TP,
    # and NM-managed WireGuard/OpenVPN that might not show via
    # the kernel-level checks above.
    rc, stdout, _ = run_command(
        ['nmcli', '-t', '-f', 'NAME,TYPE,DEVICE', 'connection', 'show', '--active'],
        timeout=5,
    )
    if rc == 0:
        for line in stdout.splitlines():
            # Format: "My VPN:vpn:tun0" or "wg-tunnel:wireguard:wg0"
            parts = line.split(':')
            if len(parts) >= 2:
                conn_name = parts[0]
                conn_type = parts[1]
                conn_dev = parts[2] if len(parts) >= 3 else ''

                if conn_type in ('vpn', 'wireguard'):
                    if conn_dev and conn_dev in seen_interfaces:
                        continue  # already found via kernel detection
                    if conn_dev:
                        seen_interfaces.add(conn_dev)
                    # Map NM type to our type
                    if conn_type == 'wireguard':
                        vtype = 'wireguard'
                    else:
                        vtype = 'vpn'
                    vpns.append(VPNConnection(
                        name=conn_name,
                        vpn_type=vtype,
                        interface=conn_dev,
                        active=True,
                    ))

    return vpns


# ── Main dataclass and orchestration ─────────────────────────────────

@dataclass
class NetworkStatus:
    """Network connectivity status."""
    internet_working: bool = False
    wifi_connected: bool = False
    wifi_ssid: Optional[str] = None
    ethernet_connected: bool = False
    
    interfaces_up: int = 0
    
    # WiFi power save
    wifi_interface: str = ""         # e.g. "wlan0", "wlp1s0"
    wifi_power_save: Optional[bool] = None  # True=on, False=off, None=unknown
    wifi_power_save_service: Optional[bool] = None  # True=service exists

    # IP addresses
    ip_addresses: list = field(default_factory=list)   # list[InterfaceAddress]

    # DNS
    dns_servers: list = field(default_factory=list)     # list[str]

    # VPN
    vpn_connections: list = field(default_factory=list)  # list[VPNConnection]


def ping_test(host: str, timeout: int = 2) -> bool:
    """Test connectivity by pinging a host."""
    rc, _, _ = run_command(['ping', '-c', '1', '-W', str(timeout), host], timeout=timeout+1)
    return rc == 0


def check_internet_connectivity() -> bool:
    """Check if internet is working by pinging known reliable hosts."""
    # Try Google DNS
    if ping_test('8.8.8.8'):
        return True
    
    # Try Cloudflare DNS
    if ping_test('1.1.1.1'):
        return True
    
    return False


def check_wifi_status() -> tuple[bool, Optional[str]]:
    """
    Check WiFi connection status.
    
    Returns:
        Tuple of (connected, ssid)
    """
    # Use nmcli for WiFi status
    rc, stdout, _ = run_command(['nmcli', '-t', '-f', 'ACTIVE,SSID', 'dev', 'wifi'])
    if rc == 0:
        for line in stdout.split('\n'):
            if line.startswith('yes:'):
                ssid = line.split(':', 1)[1] if ':' in line else None
                return True, ssid
    
    return False, None


def check_ethernet_status() -> bool:
    """Check if any Ethernet interface is up."""
    rc, stdout, _ = run_command(['ip', 'link', 'show'])
    if rc != 0:
        return False
    
    # Look for eth*, enp*, eno* interfaces in UP state
    for line in stdout.split('\n'):
        if re.search(r'(eth|enp|eno)\d+.*state UP', line):
            return True
    
    return False


def count_interfaces_up() -> int:
    """Count number of network interfaces in UP state."""
    rc, stdout, _ = run_command(['ip', 'link', 'show'])
    if rc != 0:
        return 0
    
    count = 0
    for line in stdout.split('\n'):
        if 'state UP' in line:
            count += 1
    
    return count


def check_network_connectivity() -> NetworkStatus:
    """
    Check complete network connectivity status.
    
    Returns:
        NetworkStatus object
    """
    status = NetworkStatus()
    
    # Check interfaces
    status.interfaces_up = count_interfaces_up()
    
    # Check internet
    status.internet_working = check_internet_connectivity()
    
    # Check WiFi
    status.wifi_connected, status.wifi_ssid = check_wifi_status()
    
    # Check Ethernet
    status.ethernet_connected = check_ethernet_status()
    
    # WiFi power save
    status.wifi_interface = _get_wifi_interface()
    status.wifi_power_save = _check_wifi_power_save(status.wifi_interface)
    status.wifi_power_save_service = Path('/etc/systemd/system/wifi-power-save.service').exists()

    # IP addresses
    status.ip_addresses = _detect_ip_addresses()

    # DNS servers
    status.dns_servers = _detect_dns_servers()

    # VPN connections
    status.vpn_connections = _detect_vpn_connections()
    
    return status


def format_network_report(status: NetworkStatus) -> list[str]:
    """Format network status for the diagnostic report."""
    lines = []
    
    lines.append("Network Connectivity:")
    
    # Internet
    if status.internet_working:
        lines.append("  Internet: ✅ Connected")
    else:
        lines.append("  Internet: ❌ Not connected")
    
    # WiFi
    if status.wifi_connected:
        lines.append(f'  WiFi: ✅ Connected to "{status.wifi_ssid}"')
    else:
        lines.append("  WiFi: ❌ Not connected")
    
    # Ethernet
    if status.ethernet_connected:
        lines.append("  Ethernet: ✅ Connected")
    else:
        lines.append("  Ethernet: ❌ Not connected")

    # VPN
    if status.vpn_connections:
        for vpn in status.vpn_connections:
            iface_str = f' ({vpn.interface})' if vpn.interface else ''
            lines.append(f'  VPN: ✅ {vpn.name} [{vpn.vpn_type}]{iface_str}')
    else:
        lines.append("  VPN: none detected")

    # IP addresses
    if status.ip_addresses:
        lines.append("")
        lines.append("  IP Addresses:")
        for addr in status.ip_addresses:
            lines.append(f'    {addr.interface}: {addr.address} ({addr.family})')
    else:
        lines.append("  IP Addresses: none detected")

    # DNS servers
    if status.dns_servers:
        lines.append(f'  DNS: {", ".join(status.dns_servers)}')
    else:
        lines.append("  DNS: none detected")
    
    # WiFi power save
    if status.wifi_interface:
        if status.wifi_power_save is True:
            lines.append(f"  WiFi Power Save: on ({status.wifi_interface})")
        elif status.wifi_power_save is False:
            lines.append(f"  WiFi Power Save: off ({status.wifi_interface})")
    
    if status.wifi_power_save_service:
        lines.append("  wifi-power-save.service: ✅ installed")
    
    return lines
