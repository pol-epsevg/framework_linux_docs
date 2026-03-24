"""
Firmware status detection.

Collects:
- fwupd device list (all firmware versions + available updates)
- BIOS version (from dmidecode)
- EC (Embedded Controller) firmware version
- Fingerprint reader detection (Goodix - known Framework trouble source)
- Thunderbolt controller firmware
- Secure Boot status
- Kernel command line (boot parameters / workarounds)
"""

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .utils import run_command, run_sudo_command


# BIOS & Drivers KB page (single source of truth for all models)
BIOS_DRIVERS_URL = 'https://knowledgebase.frame.work/en_us/bios-and-drivers-downloads-rJ3PaCexh'

# Goodix fingerprint reader USB vendor:product IDs
# Framework ships these across multiple models
GOODIX_USB_IDS = [
    '27c6:5395',  # Goodix FingerPrint (FW13 AMD)
    '27c6:530c',  # Goodix FingerPrint (FW13 Intel)
    '27c6:5584',  # Goodix FingerPrint (FW16)
    '27c6:5503',  # Goodix FingerPrint (variant)
    '27c6:538d',  # Goodix FingerPrint (variant)
    '27c6:5301',  # Goodix FingerPrint (variant)
]


@dataclass
class FwupdDevice:
    """A device reported by fwupd."""
    name: str
    device_id: str = ""
    current_version: str = ""
    update_available: bool = False
    update_version: str = ""
    vendor: str = ""
    flags: list[str] = field(default_factory=list)
    guid: str = ""




@dataclass
class FingerprintInfo:
    """Fingerprint reader detection and fprintd status."""
    detected: bool = False
    model: str = ""
    usb_id: str = ""
    driver_loaded: bool = False
    driver_name: str = ""
    # fprintd details
    fprintd_installed: bool = False
    fprintd_version: str = ""
    fprintd_running: bool = False
    fprintd_enabled: bool = False
    enrolled_fingers: list[str] = field(default_factory=list)  # e.g. ["right-index-finger"]
    enrolled_user: str = ""  # user we checked enrollment for
    pam_configured: bool = False  # fingerprint auth in PAM
    warnings: list[str] = field(default_factory=list)


@dataclass
class FirmwareInfo:
    """Complete firmware status."""
    # fwupd devices
    fwupd_available: bool = False
    fwupd_daemon_available: bool = True  # False if fwupdmgr can't reach daemon
    fwupd_service_enabled: bool = True   # False if systemd service not enabled (NixOS D-Bus auto-start hides this)
    fwupd_devices: list[FwupdDevice] = field(default_factory=list)
    updates_available: int = 0
    lvfs_refresh_failed: bool = False

    # BIOS
    bios_version: str = ""

    # EC
    ec_version: str = ""

    # Fingerprint
    fingerprint: FingerprintInfo = field(default_factory=FingerprintInfo)

    # Boot config
    kernel_cmdline: str = ""
    secure_boot: Optional[bool] = None  # None = unknown, True/False = detected

    # Thunderbolt
    thunderbolt_fw_version: str = ""

    # Framework System Tool
    framework_tool_versions: str = ""   # output of framework_tool --versions
    framework_tool_pd_info: str = ""    # output of framework_tool --pd-info


def _find_fwupdtool() -> Optional[str]:
    """Find fwupdtool binary.

    Checks PATH first, then looks relative to fwupdmgr (common on NixOS
    where the binary lives under /nix/store/.../libexec/fwupd/fwupdtool).

    Returns path string or None.
    """
    # PATH lookup works on all distros including NixOS nix-shell
    found = shutil.which('fwupdtool')
    if found:
        return found

    # fwupdtool often lives at ../libexec/fwupd/fwupdtool relative to fwupdmgr
    fwupdmgr = shutil.which('fwupdmgr')
    if fwupdmgr:
        mgr_dir = Path(fwupdmgr).resolve().parent
        candidate = mgr_dir.parent / 'libexec' / 'fwupd' / 'fwupdtool'
        if candidate.is_file():
            return str(candidate)

    return None


def _run_fwupd_command(args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    """Run an fwupd command, falling back to fwupdtool if daemon unavailable.

    Tries fwupdmgr first. If it fails (e.g. daemon not running on NixOS),
    falls back to fwupdtool which works standalone without the D-Bus daemon.

    Note: fwupdtool does NOT support --no-unreported-check (fwupdmgr-only,
    for LVFS reporting prompts). This is stripped automatically when falling back.
    fwupdtool DOES support --force (per man page).

    Returns (returncode, stdout, stderr).
    """
    rc, stdout, stderr = run_command(args, timeout=timeout)
    if rc == 0:
        return rc, stdout, stderr

    # Check if failure is due to daemon unavailability
    daemon_errors = ['could not connect', 'failed to connect', 'no such file or directory',
                     'org.freedesktop.fwupd', 'service is not running', 'timed out',
                     'command not found']
    combined = (stdout + stderr).lower()
    is_daemon_issue = any(err in combined for err in daemon_errors)

    if not is_daemon_issue:
        return rc, stdout, stderr

    # Try fwupdtool fallback
    tool_path = _find_fwupdtool()
    if not tool_path:
        return rc, stdout, stderr

    # Build fwupdtool command: replace fwupdmgr with fwupdtool,
    # strip flags it doesn't support (--no-unreported-check is fwupdmgr-only)
    unsupported_flags = {'--no-unreported-check'}
    new_args = [tool_path] + [a for a in args[1:] if a not in unsupported_flags]

    return run_command(new_args, timeout=timeout)


def get_fwupd_devices() -> tuple[bool, list[FwupdDevice]]:
    """
    Get device firmware status from fwupd.

    Uses 'fwupdmgr get-devices --json' for structured output,
    falls back to text parsing.

    Returns:
        Tuple of (fwupd_available, device_list)
    """
    devices = []

    # Try JSON output first (fwupd >= 1.7.0)
    rc, stdout, _ = _run_fwupd_command(['fwupdmgr', 'get-devices', '--json', '--no-unreported-check'], timeout=15)
    if rc == 0 and stdout.strip():
        try:
            import json
            data = json.loads(stdout)
            # fwupd JSON has {"Devices": [...]}
            for dev in data.get('Devices', []):
                device = FwupdDevice(
                    name=dev.get('Name', 'Unknown'),
                    device_id=dev.get('DeviceId', ''),
                    current_version=dev.get('Version', ''),
                    vendor=dev.get('Vendor', ''),
                    guid=dev.get('Guid', [''])[0] if dev.get('Guid') else '',
                )
                # Check flags for updatable
                flags = dev.get('Flags', [])
                device.flags = flags if isinstance(flags, list) else []

                devices.append(device)
            return True, devices
        except (ImportError, ValueError, KeyError):
            pass

    # Fallback: text output
    rc, stdout, _ = _run_fwupd_command(['fwupdmgr', 'get-devices', '--no-unreported-check'], timeout=15)
    if rc == 0 and stdout.strip():
        current_device = None
        for line in stdout.split('\n'):
            # Device header lines are indented with the device name
            # Followed by properties like "Device ID: ..."
            stripped = line.strip()

            if not stripped:
                if current_device:
                    devices.append(current_device)
                    current_device = None
                continue

            if not line.startswith('  ') and not line.startswith('\t') and ':' not in stripped:
                # This is likely a device name line
                if current_device:
                    devices.append(current_device)
                current_device = FwupdDevice(name=stripped)
            elif current_device and ':' in stripped:
                key, _, value = stripped.partition(':')
                key = key.strip()
                value = value.strip()
                if key == 'Device ID':
                    current_device.device_id = value
                elif key == 'Current version':
                    current_device.current_version = value
                elif key == 'Vendor':
                    current_device.vendor = value
                elif key == 'Update Version':
                    current_device.update_available = True
                    current_device.update_version = value

        if current_device:
            devices.append(current_device)

        return True, devices

    # fwupd not available
    return False, []


def check_fwupd_updates() -> tuple[int, bool]:
    """
    Check how many firmware updates are available.

    Refreshes LVFS metadata first to ensure results are current.
    On NixOS, uses fwupdtool directly (daemon may auto-start via D-Bus
    but won't have LVFS remotes configured without services.fwupd.enable).

    Returns:
        Tuple of (count of available updates, whether refresh failed)
    """
    from .dependencies import get_distro_id

    # NixOS without services.fwupd.enable: use fwupdtool directly —
    # daemon auto-starts via D-Bus but has no LVFS remotes configured.
    # When service IS enabled ("linked"), use normal fwupdmgr path below.
    if get_distro_id() == 'nixos':
        rc_svc, stdout_svc, _ = run_command(['systemctl', 'is-enabled', 'fwupd.service'], timeout=5)
        nixos_service_enabled = (rc_svc == 0 or stdout_svc.strip() in ('linked', 'linked-runtime'))
        if not nixos_service_enabled:
            tool_path = _find_fwupdtool()
            if tool_path:
                rc, _, _ = run_command([tool_path, 'refresh', '--force'], timeout=30)
                refresh_failed = rc != 0

                rc, stdout, _ = run_command([tool_path, 'get-updates', '--json'], timeout=15)
                if rc == 0 and stdout.strip():
                    try:
                        import json
                        data = json.loads(stdout)
                        return len(data.get('Devices', [])), refresh_failed
                    except (ImportError, ValueError):
                        pass

                rc, stdout, _ = run_command([tool_path, 'get-updates'], timeout=15)
                if rc == 0:
                    count = 0
                    for line in stdout.split('\n'):
                        if 'Update Version' in line or 'New version' in line:
                            count += 1
                    return count, refresh_failed

                return 0, refresh_failed

    # All other distros: use fwupdmgr (with fwupdtool fallback if daemon unavailable)
    # Refresh metadata from LVFS first — stale/missing metadata means no results
    # --force ensures metadata is actually downloaded, not skipped due to cache age
    rc, _, _ = _run_fwupd_command(['fwupdmgr', 'refresh', '--force', '--no-unreported-check'], timeout=30)
    refresh_failed = rc != 0

    rc, stdout, _ = _run_fwupd_command(['fwupdmgr', 'get-updates', '--json', '--no-unreported-check'], timeout=15)
    if rc == 0 and stdout.strip():
        try:
            import json
            data = json.loads(stdout)
            return len(data.get('Devices', [])), refresh_failed
        except (ImportError, ValueError):
            pass

    # Fallback: text output, count device sections
    rc, stdout, _ = _run_fwupd_command(['fwupdmgr', 'get-updates', '--no-unreported-check'], timeout=15)
    if rc == 0:
        # Count lines that look like device update headers
        count = 0
        for line in stdout.split('\n'):
            if 'Update Version' in line or 'New version' in line:
                count += 1
        return count, refresh_failed

    return 0, refresh_failed




def detect_fingerprint_reader() -> FingerprintInfo:
    """
    Detect Goodix fingerprint reader and fprintd status.

    Framework laptops use Goodix readers (vendor ID 27c6) which are a
    known source of suspend issues and driver problems.
    
    Collects:
    - Hardware presence (lsusb)
    - Kernel driver (lsmod)
    - fprintd service status and version
    - Enrolled fingers (fprintd-list)
    - PAM configuration
    - Known issue warnings
    """
    info = FingerprintInfo()

    rc, stdout, _ = run_command(['lsusb'])
    if rc != 0:
        return info

    for line in stdout.split('\n'):
        match = re.search(r'ID\s+([0-9a-f]{4}:[0-9a-f]{4})', line, re.IGNORECASE)
        if match:
            usb_id = match.group(1).lower()
            if usb_id in GOODIX_USB_IDS or usb_id.startswith('27c6:'):
                info.detected = True
                info.usb_id = usb_id
                # Extract name from lsusb line
                name_match = re.search(r'ID\s+[0-9a-f:]+\s+(.+)$', line)
                if name_match:
                    info.model = name_match.group(1).strip()
                else:
                    info.model = 'Goodix Fingerprint Reader'
                break

    # Check kernel driver
    if info.detected:
        rc, stdout, _ = run_command(['lsmod'])
        if rc == 0:
            for mod in ['goodix_ts', 'goodix', 'usbhid']:
                if mod in stdout:
                    info.driver_loaded = True
                    info.driver_name = mod
                    break

    # fprintd service status
    rc, stdout, _ = run_command(['systemctl', 'is-active', 'fprintd.service'])
    if rc == 0 and stdout.strip() == 'active':
        info.fprintd_running = True
        if info.detected:
            info.driver_loaded = True
            if not info.driver_name:
                info.driver_name = 'fprintd/libfprint'
    
    rc, stdout, _ = run_command(['systemctl', 'is-enabled', 'fprintd.service'])
    if rc == 0 and stdout.strip() in ('enabled', 'static'):
        info.fprintd_enabled = True
        info.fprintd_installed = True
    
    # fprintd version
    rc, stdout, _ = run_command(['fprintd-list', '--version'])
    if rc != 0:
        # Try package manager queries
        rc, stdout, _ = run_command(['bash', '-c',
            'rpm -q fprintd 2>/dev/null || '
            'dpkg -l fprintd 2>/dev/null | grep ^ii | awk \'{print $3}\' || '
            'pacman -Q fprintd 2>/dev/null'])
    if rc == 0 and stdout.strip():
        ver_match = re.search(r'(\d+\.\d+[\.\d]*)', stdout)
        if ver_match:
            info.fprintd_version = ver_match.group(1)
            info.fprintd_installed = True
    
    # Enrolled fingers - try current $SUDO_USER or $USER
    login_user = os.environ.get('SUDO_USER', os.environ.get('USER', ''))
    if login_user and login_user != 'root':
        info.enrolled_user = login_user
        rc, stdout, _ = run_command(['fprintd-list', login_user])
        if rc == 0:
            info.fprintd_installed = True
            # Output format:
            # "Using device /dev/XX"
            # "Fingerprints for user matt on ... :"
            # " - #0: right-index-finger"
            for line in stdout.split('\n'):
                line = line.strip()
                if line.startswith('- #') or line.startswith('-#'):
                    # Extract finger name
                    finger_match = re.search(r':\s*(.+)$', line)
                    if finger_match:
                        info.enrolled_fingers.append(finger_match.group(1).strip())
    
    # PAM configuration - check if pam_fprintd.so is active
    pam_paths = [
        '/etc/pam.d/system-auth',        # Fedora/RHEL
        '/etc/pam.d/common-auth',         # Debian/Ubuntu
        '/etc/pam.d/login',               # Generic
        '/etc/pam.d/sudo',                # Sudo fingerprint
        '/etc/pam.d/gdm-fingerprint',     # GNOME
        '/etc/pam.d/sddm',               # KDE
    ]
    for pam_path in pam_paths:
        try:
            pam_content = Path(pam_path).read_text()
            for pam_line in pam_content.split('\n'):
                stripped = pam_line.strip()
                # Active (not commented out) pam_fprintd.so line
                if 'pam_fprintd.so' in stripped and not stripped.startswith('#'):
                    info.pam_configured = True
                    break
        except (OSError, PermissionError):
            pass
        if info.pam_configured:
            break
    
    # Warnings for known issues
    if info.detected:
        if not info.fprintd_installed:
            info.warnings.append("fprintd not installed — fingerprint auth unavailable")
        elif not info.fprintd_running and info.fprintd_enabled:
            # fprintd is socket-activated, so not running is normal until first use
            pass
        
        if info.fprintd_installed and not info.enrolled_fingers and info.enrolled_user:
            info.warnings.append(f"No fingers enrolled for {info.enrolled_user}")
        
        if info.fprintd_installed and not info.pam_configured:
            info.warnings.append("pam_fprintd.so not found in PAM config — "
                               "fingerprint login/sudo won't work")
    
    return info


def get_kernel_cmdline() -> str:
    """Read kernel boot command line from /proc/cmdline."""
    cmdline_path = Path('/proc/cmdline')
    try:
        if cmdline_path.exists():
            return cmdline_path.read_text().strip()
    except PermissionError:
        pass
    return ""


def get_secure_boot_status() -> Optional[bool]:
    """
    Check Secure Boot status.

    Returns:
        True = enabled, False = disabled, None = unknown
    """
    # Method 1: mokutil
    rc, stdout, _ = run_command(['mokutil', '--sb-state'])
    if rc == 0:
        if 'SecureBoot enabled' in stdout:
            return True
        elif 'SecureBoot disabled' in stdout:
            return False

    # Method 2: Check EFI variable directly
    sb_path = Path('/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c')
    if sb_path.exists():
        try:
            data = sb_path.read_bytes()
            # Last byte: 1 = enabled, 0 = disabled
            if len(data) >= 5:
                return data[4] == 1
        except PermissionError:
            pass

    # Method 3: bootctl (systemd-boot)
    rc, stdout, _ = run_command(['bootctl', 'status'])
    if rc == 0:
        for line in stdout.split('\n'):
            if 'Secure Boot' in line:
                if 'enabled' in line.lower():
                    return True
                elif 'disabled' in line.lower():
                    return False

    return None


def get_ec_version() -> str:
    """
    Get EC (Embedded Controller) firmware version.

    Tries multiple methods:
    1. ectool (Framework's tool)
    2. fwupd device list (EC shows up as updatable device)
    3. dmidecode
    """
    # Method 1: ectool
    rc, stdout, _ = run_command(['ectool', 'version'])
    if rc == 0:
        for line in stdout.split('\n'):
            if 'RO version' in line or 'RW version' in line:
                parts = line.split(':')
                if len(parts) >= 2:
                    return parts[1].strip()

    # Method 2: dmidecode BIOS information (sometimes has EC version)
    rc, stdout, _ = run_sudo_command(['dmidecode', '-t', 'bios'])
    if rc == 0:
        for line in stdout.split('\n'):
            if 'EC' in line and 'Version' in line:
                parts = line.split(':')
                if len(parts) >= 2:
                    return parts[1].strip()
            # Framework-specific: "Firmware Revision" in BIOS info
            if 'Firmware Revision' in line:
                parts = line.split(':')
                if len(parts) >= 2:
                    version = parts[1].strip()
                    if version and version != '0.0':
                        return version

    return ""


def get_thunderbolt_fw_version() -> str:
    """Get Thunderbolt controller firmware version if present."""
    # Try boltctl
    rc, stdout, _ = run_command(['boltctl', 'list'])
    if rc == 0:
        for line in stdout.split('\n'):
            if 'nvm-version' in line.lower() or 'firmware' in line.lower():
                parts = line.split(':')
                if len(parts) >= 2:
                    return parts[1].strip()

    return ""


_FRAMEWORK_TOOL_URL = (
    'https://github.com/FrameworkComputer/framework-system'
    '/releases/latest/download/framework_tool'
)
_FRAMEWORK_TOOL_PATH = Path('/tmp/framework_tool')


def _download_framework_tool() -> bool:
    """Download framework_tool binary from GitHub.

    Returns True if download succeeded and binary is executable.
    """
    import socket
    import urllib.request
    import urllib.error

    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(15)
        urllib.request.urlretrieve(
            _FRAMEWORK_TOOL_URL, str(_FRAMEWORK_TOOL_PATH),
        )
        _FRAMEWORK_TOOL_PATH.chmod(0o755)
        return _FRAMEWORK_TOOL_PATH.is_file()
    except (urllib.error.URLError, OSError, socket.timeout):
        return False
    finally:
        socket.setdefaulttimeout(old_timeout)


def _run_framework_tool() -> tuple[str, str]:
    """Download and run framework_tool --versions and --pd-info.

    Returns (versions_output, pd_info_output). Empty strings on failure.
    """
    # Check PATH first (works on all distros including NixOS nix-shell)
    tool_path = shutil.which('framework_tool')

    # Fallback: check common hardcoded locations
    if not tool_path:
        for candidate in ['/usr/bin/framework_tool', '/usr/local/bin/framework_tool']:
            if Path(candidate).is_file():
                tool_path = candidate
                break

    # Download if not installed
    if not tool_path:
        if _download_framework_tool():
            tool_path = str(_FRAMEWORK_TOOL_PATH)
        else:
            return "", ""

    versions = ""
    pd_info = ""

    rc, stdout, _ = run_sudo_command([tool_path, '--versions'], timeout=10)
    if rc == 0 and stdout.strip():
        versions = stdout.strip()

    rc, stdout, _ = run_sudo_command([tool_path, '--pd-info'], timeout=10)
    if rc == 0 and stdout.strip():
        pd_info = stdout.strip()

    # Clean up downloaded binary
    if tool_path == str(_FRAMEWORK_TOOL_PATH):
        try:
            _FRAMEWORK_TOOL_PATH.unlink()
        except OSError:
            pass

    return versions, pd_info


def detect_firmware_info(
    bios_version: str = "",
    is_framework: bool = False
) -> FirmwareInfo:
    """
    Detect all firmware-related information.

    Args:
        bios_version: Current BIOS version from hardware detection
        is_framework: Whether this is a Framework device

    Returns:
        FirmwareInfo with all collected data
    """
    info = FirmwareInfo()

    # Test if fwupd daemon is reachable (required for update checks)
    rc, _, stderr = run_command(['fwupdmgr', 'get-devices', '--json', '--no-unreported-check'], timeout=10)
    daemon_errors = ['could not connect', 'failed to connect', 'org.freedesktop.fwupd',
                     'service is not running', 'timed out', 'command not found']
    if rc != 0 and any(err in (stderr or '').lower() for err in daemon_errors):
        info.fwupd_daemon_available = False

    # Check if fwupd service is actually enabled (not just D-Bus auto-started)
    # On NixOS without services.fwupd.enable, the daemon auto-starts via D-Bus
    # but has no LVFS remotes configured — so it can list devices but can't check updates
    # NixOS with services.fwupd.enable reports "linked" (exit 1), not "enabled" (exit 0)
    rc_enabled, stdout_enabled, _ = run_command(['systemctl', 'is-enabled', 'fwupd.service'], timeout=5)
    if rc_enabled != 0 and stdout_enabled.strip() not in ('linked', 'linked-runtime'):
        info.fwupd_service_enabled = False

    # fwupd devices (uses fwupdtool fallback if daemon unavailable)
    info.fwupd_available, info.fwupd_devices = get_fwupd_devices()

    # Check for updates (uses fwupdtool fallback if daemon unavailable)
    if info.fwupd_available:
        info.updates_available, info.lvfs_refresh_failed = check_fwupd_updates()

    # BIOS version (from dmidecode, passed in by caller)
    info.bios_version = bios_version

    # EC version (Framework only)
    if is_framework:
        info.ec_version = get_ec_version()

    # Fingerprint reader
    info.fingerprint = detect_fingerprint_reader()

    # Boot config
    info.kernel_cmdline = get_kernel_cmdline()
    info.secure_boot = get_secure_boot_status()

    # Thunderbolt
    info.thunderbolt_fw_version = get_thunderbolt_fw_version()

    # Framework System Tool (Framework only, needs internet)
    if is_framework:
        info.framework_tool_versions, info.framework_tool_pd_info = _run_framework_tool()

    return info


def format_firmware_report(info: FirmwareInfo) -> list[str]:
    """Format firmware info for the diagnostic report."""
    lines = []

    lines.append("Firmware Status:")

    # BIOS version
    if info.bios_version:
        lines.append(f"  BIOS: {info.bios_version}")
        lines.append(f"    BIOS & Drivers: {BIOS_DRIVERS_URL}")

    # EC version
    if info.ec_version:
        lines.append(f"  EC Firmware: {info.ec_version}")

    # Secure Boot
    if info.secure_boot is True:
        lines.append("  Secure Boot: Enabled")
    elif info.secure_boot is False:
        lines.append("  Secure Boot: Disabled")

    # Thunderbolt firmware
    if info.thunderbolt_fw_version:
        lines.append(f"  Thunderbolt FW: {info.thunderbolt_fw_version}")

    # Kernel command line
    if info.kernel_cmdline:
        lines.append(f"  Kernel cmdline: {info.kernel_cmdline}")
        # Surface non-standard parameters
        interesting_params = _extract_interesting_boot_params(info.kernel_cmdline)
        if interesting_params:
            lines.append("  Non-default kernel parameters:")
            for param, note in interesting_params:
                if note:
                    lines.append(f"    {param} — {note}")
                else:
                    lines.append(f"    {param}")

    # Fingerprint reader
    if info.fingerprint.detected:
        fp = info.fingerprint
        driver_str = f", driver: {fp.driver_name}" if fp.driver_loaded else ", no driver"
        lines.append(f"  Fingerprint: {fp.model} ({fp.usb_id}{driver_str})")
        
        # fprintd status
        if fp.fprintd_installed:
            ver_str = f" v{fp.fprintd_version}" if fp.fprintd_version else ""
            svc_icon = "✅" if fp.fprintd_running or fp.fprintd_enabled else "❌"
            # fprintd is socket-activated, so "inactive" is normal
            if fp.fprintd_enabled and not fp.fprintd_running:
                svc_status = "enabled (socket-activated)"
                svc_icon = "✅"
            elif fp.fprintd_running:
                svc_status = "running"
            else:
                svc_status = "not enabled"
            lines.append(f"    fprintd{ver_str}: {svc_icon} {svc_status}")
            
            # Enrollment
            if fp.enrolled_fingers:
                fingers = ', '.join(fp.enrolled_fingers)
                lines.append(f"    Enrolled ({fp.enrolled_user}): {fingers}")
            elif fp.enrolled_user:
                lines.append(f"    Enrolled ({fp.enrolled_user}): ❌ No fingers enrolled")
            
            # PAM
            pam_icon = "✅" if fp.pam_configured else "❌"
            pam_str = "configured" if fp.pam_configured else "not configured"
            lines.append(f"    PAM auth: {pam_icon} {pam_str}")
        else:
            lines.append("    fprintd: ❌ Not installed")
        
        # Warnings
        for warning in fp.warnings:
            lines.append(f"    ⚠️  {warning}")
    else:
        lines.append("  Fingerprint: Not detected")

    # fwupd devices
    if info.fwupd_available:
        lines.append(f"  fwupd: {len(info.fwupd_devices)} device(s) managed")
        if not info.fwupd_daemon_available:
            lines.append("    ⚠️  fwupd daemon not running")
            # Distro-specific fix
            from .dependencies import get_distro_id
            distro_id = get_distro_id()
            if distro_id == 'nixos':
                lines.append("    Fix: add 'services.fwupd.enable = true;' to configuration.nix")
                lines.append("    Then run: sudo nixos-rebuild switch")
            else:
                lines.append("    Fix: sudo systemctl enable --now fwupd")
        elif not info.fwupd_service_enabled:
            # NixOS: daemon auto-started via D-Bus but service not enabled — no LVFS remotes
            from .dependencies import get_distro_id
            if get_distro_id() == 'nixos':
                lines.append("    ⚠️  fwupd service not enabled — cannot check LVFS for firmware updates")
                lines.append("    Fix: add 'services.fwupd.enable = true;' to configuration.nix")
                lines.append("    Then run: sudo nixos-rebuild switch")
        if info.updates_available > 0:
            lines.append(f"    ⚠️  {info.updates_available} firmware update(s) available")
            lines.append("    Run: fwupdmgr get-updates && fwupdmgr update")
        elif info.lvfs_refresh_failed:
            lines.append("    ⚠️  Could not check LVFS for firmware updates (metadata refresh failed)")
            from .dependencies import get_distro_id
            if get_distro_id() == 'nixos':
                lines.append("    Fix: add 'services.fwupd.enable = true;' to configuration.nix")
                lines.append("    Then run: sudo nixos-rebuild switch")

        # List devices with versions (only those with versions, skip empty)
        versioned = [d for d in info.fwupd_devices if d.current_version]
        if versioned:
            lines.append("  fwupd device versions:")
            for dev in versioned:
                update_str = f" → {dev.update_version}" if dev.update_available else ""
                lines.append(f"    {dev.name}: {dev.current_version}{update_str}")
    else:
        from .dependencies import get_distro_id
        if get_distro_id() == 'nixos':
            lines.append("  fwupd: ⚠️  Not available")
            lines.append("    Fix: add 'services.fwupd.enable = true;' to configuration.nix")
            lines.append("    Then run: sudo nixos-rebuild switch")
        else:
            lines.append("  fwupd: Not available (install fwupd for firmware management)")

    # Framework System Tool
    if info.framework_tool_versions:
        lines.append("")
        lines.append("  Framework System Tool (framework_tool --versions):")
        for line in info.framework_tool_versions.split('\n'):
            if line.strip():
                lines.append(f"    {line.strip()}")

    if info.framework_tool_pd_info:
        lines.append("")
        lines.append("  Framework System Tool (framework_tool --pd-info):")
        for line in info.framework_tool_pd_info.split('\n'):
            if line.strip():
                lines.append(f"    {line.strip()}")

    return lines


def _extract_interesting_boot_params(cmdline: str) -> list[tuple[str, str]]:
    """
    Surface non-standard kernel parameters by stripping known boot
    infrastructure. Everything left is either user-added or a distro
    hardware workaround — either way, support needs to see it.

    Returns list of (parameter, note) tuples for display.
    Note is empty string for unknown params, filled in for recognized ones.
    """
    # ── Standard boot infrastructure (never interesting) ─────────
    # These are set by bootloaders, initramfs, and default distro configs
    # across Fedora, Ubuntu, Arch, openSUSE, etc.
    _STANDARD_EXACT = frozenset({
        'ro', 'rw', 'quiet', 'splash', 'rhgb', 'noresume', 'noplymouth',
    })

    _STANDARD_PREFIXES = (
        'BOOT_IMAGE=', 'root=', 'rootflags=', 'rootfstype=', 'initrd=',
        'resume=',
        'rd.',              # dracut/initramfs (rd.luks, rd.lvm, rd.md, etc.)
        'loglevel=', 'audit=', 'crashkernel=',
        'systemd.',         # systemd boot params
        'plymouth.', 'vt.handoff=',
        'lang=', 'console=',
        'apparmor=', 'security=',
    )

    # ── Known workarounds (annotate when recognized) ─────────────
    _KNOWN_NOTES = {
        'amdgpu.runpm=0': 'AMD GPU runtime PM disabled (power/suspend workaround)',
        'amdgpu.dcdebugmask=0x10': 'PSR disabled via AMD debug mask',
        'amdgpu.ppfeaturemask=': 'AMD GPU power features overridden',
        'i915.enable_psr=0': 'Intel Panel Self Refresh disabled',
        'i915.enable_dc=0': 'Intel display C-states disabled',
        'nvme_core.default_ps_max_latency_us=': 'NVMe power state latency override',
        'mem_sleep_default=deep': 'Forced S3 deep sleep (not s2idle)',
        'mem_sleep_default=s2idle': 'Forced s2idle sleep mode',
        'acpi_osi=': 'ACPI OS identification override',
        'acpi=off': 'ACPI completely disabled (unusual)',
        'nomodeset': 'Kernel modesetting disabled (GPU issues)',
        'iommu=pt': 'IOMMU passthrough mode',
        'amd_iommu=off': 'AMD IOMMU disabled',
        'intel_iommu=on': 'Intel IOMMU enabled',
        'snd_hda_intel.power_save=': 'HDA audio power save setting',
        'iwlwifi.power_save=0': 'Intel WiFi power saving disabled',
        'usbcore.autosuspend=-1': 'USB autosuspend disabled globally',
        'ec_intr=0': 'EC interrupt mode disabled (workaround)',
        'tpm_tis.interrupts=0': 'TPM interrupts disabled (boot speed workaround)',
        'pcie_aspm=off': 'PCIe Active State Power Management disabled',
        'pcie_aspm.policy=': 'PCIe ASPM policy override',
        'mitigations=off': 'CPU vulnerability mitigations disabled',
    }

    interesting = []
    for param in cmdline.split():
        # Skip standard boot infrastructure
        if param in _STANDARD_EXACT:
            continue
        if any(param.startswith(p) for p in _STANDARD_PREFIXES):
            continue

        # Look up annotation for known params
        note = ''
        for key, desc in _KNOWN_NOTES.items():
            if key.endswith('='):
                if param.startswith(key):
                    note = desc
                    break
            else:
                if param == key:
                    note = desc
                    break

        interesting.append((param, note))

    return interesting

