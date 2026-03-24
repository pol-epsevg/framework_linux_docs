"""
System information detection.

Detects:
- Kernel version
- Desktop environment (GNOME, KDE, XFCE, etc.)
- Session type (Wayland, X11)
- Distribution info
- Power management daemon (ppd, tuned-ppd, tuned, TLP)
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .utils import run_command


@dataclass
class SystemInfo:
    """System information."""
    kernel_version: str = ""
    
    # Desktop environment
    desktop_environment: str = ""  # GNOME, KDE, XFCE, etc.
    session_type: str = ""  # wayland, x11
    
    # Distribution
    distro_name: str = ""
    distro_id: str = ""
    distro_version: str = ""
    
    # Power management
    power_daemon: str = ""  # power-profiles-daemon, tuned-ppd, tuned, tlp
    power_profile: str = ""  # balanced, power-saver, performance, or tuned profile name
    power_conflicts: list[str] = field(default_factory=list)  # e.g. ["ppd + tlp both active"]
    
    # Framework recommended configs
    io_uring_disabled: Optional[bool] = None  # True = sysctl conf exists


def get_kernel_version() -> str:
    """Get the running kernel version."""
    rc, stdout, _ = run_command(['uname', '-r'])
    if rc == 0:
        return stdout.strip()
    return "Unknown"


def get_desktop_environment() -> tuple[str, str]:
    """
    Detect desktop environment and session type.
    
    Uses multiple methods:
    1. Environment variables (XDG_CURRENT_DESKTOP, XDG_SESSION_TYPE)
    2. loginctl session info
    3. Process detection fallback
    
    Returns:
        Tuple of (desktop_environment, session_type)
    """
    desktop = ""
    session = ""
    
    # Method 1: Check environment variables
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '')
    session = os.environ.get('XDG_SESSION_TYPE', '')
    
    # "tty" means we're in a root shell (su - or sudo), not the actual session
    if session == 'tty':
        session = ''
    
    # Method 2: If running as root/sudo, try loginctl
    if not desktop:
        sudo_user = os.environ.get('SUDO_USER', '')
        if sudo_user:
            # Get all sessions — user may have graphical + tty from sudo.
            # We want the graphical one.
            rc, stdout, _ = run_command(['loginctl', 'list-sessions', '--no-legend'])
            if rc == 0:
                best_desktop = ''
                best_session = ''
                for line in stdout.strip().split('\n'):
                    if sudo_user in line:
                        parts = line.split()
                        if not parts:
                            continue
                        session_id = parts[0]
                        
                        rc3, stdout3, _ = run_command([
                            'loginctl', 'show-session', session_id,
                            '-p', 'Type', '--value'
                        ])
                        sess_type = stdout3.strip() if rc3 == 0 else ''
                        
                        rc2, stdout2, _ = run_command([
                            'loginctl', 'show-session', session_id,
                            '-p', 'Desktop', '--value'
                        ])
                        sess_desktop = stdout2.strip() if rc2 == 0 else ''
                        
                        # Prefer wayland/x11 over tty
                        if sess_type in ('wayland', 'x11'):
                            best_session = sess_type
                            if sess_desktop:
                                best_desktop = sess_desktop
                            break  # Found graphical session, done
                        elif not best_session:
                            best_session = sess_type
                            best_desktop = sess_desktop
                
                if best_desktop:
                    desktop = best_desktop
                if best_session:
                    session = best_session
    
    # Method 3: Process detection fallback
    if not desktop:
        # Check for common desktop environment processes
        desktop_processes = {
            'gnome-shell': 'GNOME',
            'gnome-session': 'GNOME',
            'plasmashell': 'KDE',
            'kwin': 'KDE',
            'xfce4-session': 'XFCE',
            'xfce4-panel': 'XFCE',
            'cinnamon': 'Cinnamon',
            'mate-session': 'MATE',
            'mate-panel': 'MATE',
            'budgie-panel': 'Budgie',
            'lxqt-session': 'LXQt',
            'lxsession': 'LXDE',
            'sway': 'Sway',
            'hyprland': 'Hyprland',
            'i3': 'i3',
            'openbox': 'Openbox',
        }
        
        rc, stdout, _ = run_command(['ps', '-e', '-o', 'comm='])
        if rc == 0:
            running = set(stdout.strip().split('\n'))
            for proc, de_name in desktop_processes.items():
                if proc in running:
                    desktop = de_name
                    break
    
    # Detect session type if not found
    if not session:
        # Check for Wayland
        if os.environ.get('WAYLAND_DISPLAY'):
            session = 'wayland'
        elif os.environ.get('DISPLAY'):
            # DISPLAY survives sudo, but Xwayland also sets DISPLAY on
            # Wayland sessions. Check for Xwayland before concluding x11.
            rc, stdout, _ = run_command(['pgrep', '-x', 'Xwayland'])
            if rc == 0 and stdout.strip():
                session = 'wayland'
            else:
                session = 'x11'
        else:
            # Env vars stripped (su -). Try loginctl for any graphical session.
            rc, stdout, _ = run_command(['loginctl', 'list-sessions', '--no-legend'])
            if rc == 0:
                for line in stdout.strip().split('\n'):
                    parts = line.split()
                    if not parts:
                        continue
                    rc2, stdout2, _ = run_command([
                        'loginctl', 'show-session', parts[0],
                        '-p', 'Type', '--value'
                    ])
                    sess_type = stdout2.strip() if rc2 == 0 else ''
                    if sess_type in ('wayland', 'x11'):
                        session = sess_type
                        break
            
            # Last resort: process detection
            if not session:
                rc, stdout, _ = run_command(['pgrep', '-x', 'Xorg'])
                if rc == 0 and stdout.strip():
                    session = 'x11'
                else:
                    rc, stdout, _ = run_command(['pgrep', '-x', 'Xwayland'])
                    if rc == 0 and stdout.strip():
                        session = 'wayland'
    
    return desktop, session


def get_distro_info() -> tuple[str, str, str]:
    """
    Get distribution information from /etc/os-release.
    
    Returns:
        Tuple of (pretty_name, id, version_id)
    """
    pretty_name = ""
    distro_id = ""
    version_id = ""
    
    os_release = Path('/etc/os-release')
    if os_release.exists():
        try:
            content = os_release.read_text()
            for line in content.split('\n'):
                if line.startswith('PRETTY_NAME='):
                    pretty_name = line.split('=', 1)[1].strip('"')
                elif line.startswith('ID='):
                    distro_id = line.split('=', 1)[1].strip('"')
                elif line.startswith('VERSION_ID='):
                    version_id = line.split('=', 1)[1].strip('"')
        except Exception:
            pass
    
    return pretty_name, distro_id, version_id


def check_service_active(service: str) -> bool:
    """Check if a systemd service is active."""
    rc, stdout, _ = run_command(['systemctl', 'is-active', service])
    return rc == 0 and stdout.strip() == 'active'


def check_service_enabled(service: str) -> bool:
    """Check if a systemd service is enabled."""
    rc, stdout, _ = run_command(['systemctl', 'is-enabled', service])
    return rc == 0 and stdout.strip() in ('enabled', 'enabled-runtime')


def get_power_profile() -> tuple[str, str]:
    """
    Detect power profile daemon and current profile.
    
    Detection order:
    1. Check which services are actually running
    2. Query the appropriate tool for current profile
    
    Supports:
    - power-profiles-daemon (ppd)
    - tuned-ppd (tuned with ppd compatibility)
    - tuned (standalone)
    - TLP
    
    Returns:
        Tuple of (daemon_name, current_profile)
    """
    # Check service states first
    ppd_active = check_service_active('power-profiles-daemon')
    tuned_ppd_active = check_service_active('tuned-ppd')
    tuned_active = check_service_active('tuned')
    tlp_active = check_service_active('tlp')
    
    # Priority 1: tuned-ppd (provides ppd interface but uses tuned backend)
    if tuned_ppd_active:
        rc, stdout, _ = run_command(['powerprofilesctl', 'get'])
        if rc == 0:
            profile = stdout.strip()
            # Also get the underlying tuned profile for extra info
            rc2, stdout2, _ = run_command(['tuned-adm', 'active'])
            if rc2 == 0:
                # Parse "Current active profile: <profile>"
                for line in stdout2.strip().split('\n'):
                    if 'Current active profile:' in line:
                        tuned_profile = line.split(':', 1)[1].strip()
                        return 'tuned-ppd', f"{profile} (tuned: {tuned_profile})"
            return 'tuned-ppd', profile
    
    # Priority 2: power-profiles-daemon (standalone ppd)
    if ppd_active:
        rc, stdout, _ = run_command(['powerprofilesctl', 'get'])
        if rc == 0:
            return 'power-profiles-daemon', stdout.strip()
    
    # Priority 3: standalone tuned (no ppd interface)
    if tuned_active and not tuned_ppd_active:
        rc, stdout, _ = run_command(['tuned-adm', 'active'])
        if rc == 0:
            for line in stdout.strip().split('\n'):
                if 'Current active profile:' in line:
                    profile = line.split(':', 1)[1].strip()
                    return 'tuned', profile
        # Fallback: just report tuned is active
        return 'tuned', 'active (profile unknown)'
    
    # Priority 4: TLP
    if tlp_active:
        rc, stdout, _ = run_command(['tlp-stat', '-s'])
        if rc == 0:
            mode = 'active'
            # Parse TLP status output for mode
            for line in stdout.split('\n'):
                if 'Mode' in line and '=' in line:
                    mode = line.split('=', 1)[1].strip()
                    break
            return 'tlp', mode
        return 'tlp', 'active'
    
    # Fallback: Try commands even if service check failed
    # (some systems might not have systemd or service names differ)
    
    # Try powerprofilesctl
    rc, stdout, _ = run_command(['powerprofilesctl', 'get'])
    if rc == 0:
        profile = stdout.strip()
        # Determine which backend
        if check_service_enabled('tuned-ppd'):
            return 'tuned-ppd', profile
        return 'power-profiles-daemon', profile
    
    # Try tuned-adm
    rc, stdout, _ = run_command(['tuned-adm', 'active'])
    if rc == 0:
        for line in stdout.strip().split('\n'):
            if 'Current active profile:' in line:
                profile = line.split(':', 1)[1].strip()
                return 'tuned', profile
    
    # Try tlp-stat
    rc, stdout, _ = run_command(['tlp-stat', '-s'])
    if rc == 0:
        return 'tlp', 'active'
    
    return '', ''


def detect_power_conflicts() -> list[str]:
    """Detect conflicting power management daemons.
    
    Multiple active power managers fight over CPU frequency, turbo boost,
    and platform profile, causing erratic performance, battery drain,
    and thermal issues. This checks for actual conflicts, not just
    multiple installed packages.
    
    Returns:
        List of human-readable conflict descriptions
    """
    conflicts = []
    
    # Check which services are active (running right now)
    ppd_active = check_service_active('power-profiles-daemon')
    tuned_ppd_active = check_service_active('tuned-ppd')
    tuned_active = check_service_active('tuned')
    tlp_active = check_service_active('tlp')
    
    # Check which are enabled (start on boot)
    ppd_enabled = check_service_enabled('power-profiles-daemon')
    tuned_ppd_enabled = check_service_enabled('tuned-ppd')
    tuned_enabled = check_service_enabled('tuned')
    tlp_enabled = check_service_enabled('tlp')
    
    # tuned-ppd is designed to coexist with tuned — that's not a conflict.
    # But ppd + tlp or ppd + tuned (standalone) are conflicts.
    
    # Active conflicts (running simultaneously right now)
    if ppd_active and tlp_active:
        conflicts.append("⚠️ power-profiles-daemon AND tlp both active — they will fight over power policy")
    
    if ppd_active and tuned_active and not tuned_ppd_active:
        conflicts.append("⚠️ power-profiles-daemon AND tuned both active — they will fight over power policy")
    
    if tlp_active and tuned_active:
        conflicts.append("⚠️ tlp AND tuned both active — they will fight over power policy")
    
    # Enabled-but-not-running (will conflict on next boot)
    if ppd_enabled and tlp_enabled and not (ppd_active and tlp_active):
        if not ppd_active or not tlp_active:
            # One might have lost the race this boot, but both will try next boot
            conflicts.append("ℹ️ power-profiles-daemon AND tlp both enabled — potential conflict on next boot")
    
    if ppd_enabled and tuned_enabled and not tuned_ppd_enabled:
        if not (ppd_active and tuned_active):
            conflicts.append("ℹ️ power-profiles-daemon AND tuned both enabled — potential conflict on next boot")
    
    if tlp_enabled and tuned_enabled:
        if not (tlp_active and tuned_active):
            conflicts.append("ℹ️ tlp AND tuned both enabled — potential conflict on next boot")
    
    return conflicts


def detect_system_info() -> SystemInfo:
    """Detect all system information."""
    info = SystemInfo()
    
    info.kernel_version = get_kernel_version()
    info.desktop_environment, info.session_type = get_desktop_environment()
    info.distro_name, info.distro_id, info.distro_version = get_distro_info()
    info.power_daemon, info.power_profile = get_power_profile()
    info.power_conflicts = detect_power_conflicts()
    info.io_uring_disabled = Path('/etc/sysctl.d/10-disable-io_uring.conf').exists()
    
    return info


def format_system_info_report(info: SystemInfo) -> list[str]:
    """Format system info for the diagnostic report."""
    lines = []
    
    lines.append("System Information:")
    lines.append(f"  Kernel: {info.kernel_version}")
    
    # Desktop environment with session type
    if info.desktop_environment:
        if info.session_type:
            lines.append(f"  Desktop: {info.desktop_environment} ({info.session_type})")
        else:
            lines.append(f"  Desktop: {info.desktop_environment}")
    elif info.session_type:
        lines.append(f"  Session: {info.session_type}")
    else:
        lines.append("  Desktop: Unknown")
    
    # Distribution
    if info.distro_name:
        lines.append(f"  Distribution: {info.distro_name}")
    
    # Power profile
    if info.power_daemon:
        lines.append(f"  Power Management: {info.power_daemon}")
        lines.append(f"  Power Profile: {info.power_profile}")
    else:
        lines.append("  Power Management: None detected (no ppd/tuned/tlp)")
    
    # Power management conflicts
    for conflict in info.power_conflicts:
        lines.append(f"  {conflict}")
    
    # Framework recommended configs
    if info.io_uring_disabled:
        lines.append("  io_uring disabled: ✅ sysctl config installed")
    
    return lines
