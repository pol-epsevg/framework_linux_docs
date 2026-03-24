"""
Dependency checking and automatic installation of required tools.

Supports:
- Debian/Ubuntu/Mint/Pop!_OS (apt)
- Fedora (dnf)
- Arch/Manjaro/EndeavourOS (pacman)
- openSUSE (zypper)
- NixOS (guidance only)
- Immutable distros like Bluefin/Bazzite (skip)
"""

import os
import subprocess
import shutil
import sys
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from .output import print_info, print_warning, print_success


@dataclass
class DistroPackages:
    """Package names for a specific distro family."""
    install_cmd: list[str]  # Command prefix, e.g., ['sudo', 'apt-get', 'install', '-y']
    packages: dict[str, str]  # tool_name -> package_name mapping


# Required tools and their purposes
REQUIRED_TOOLS = {
    'lspci': 'GPU and hardware detection',
    'lsusb': 'USB device detection',
    'lshw': 'Detailed hardware info (GPU driver)',
    'dmidecode': 'System information (RAM, BIOS)',
    'iw': 'WiFi diagnostics',
    'sensors': 'Temperature monitoring',
    'nvme': 'NVMe drive info',
    'fwupdmgr': 'Firmware updates (LVFS)',
}

# Optional but helpful tools
OPTIONAL_TOOLS = {
    'upower': 'Battery health',
    'smartctl': 'Disk SMART health',
    'pactl': 'Audio configuration',
    'amixer': 'ALSA mixer status',
    'arecord': 'Microphone capture device detection',
    'bluetoothctl': 'Bluetooth diagnostics',
    'nmcli': 'Network connection and VPN detection',
}

# Package mappings per distro family
DISTRO_PACKAGES = {
    'debian': DistroPackages(
        install_cmd=['sudo', 'apt-get', 'install', '-y', '-qq'],
        packages={
            'lspci': 'pciutils',
            'lsusb': 'usbutils',
            'lshw': 'lshw',
            'dmidecode': 'dmidecode',
            'iw': 'iw',
            'sensors': 'lm-sensors',
            'nvme': 'nvme-cli',
            'smartctl': 'smartmontools',
            'upower': 'upower',
            'pactl': 'pulseaudio-utils',
            'amixer': 'alsa-utils',
            'arecord': 'alsa-utils',
            'bc': 'bc',
            'fwupdmgr': 'fwupd',
            'bluetoothctl': 'bluez',
            'nmcli': 'network-manager',
        }
    ),
    'fedora': DistroPackages(
        install_cmd=['sudo', 'dnf', 'install', '-y', '-q'],
        packages={
            'lspci': 'pciutils',
            'lsusb': 'usbutils',
            'lshw': 'lshw',
            'dmidecode': 'dmidecode',
            'iw': 'iw',
            'sensors': 'lm_sensors',
            'nvme': 'nvme-cli',
            'smartctl': 'smartmontools',
            'upower': 'upower',
            'pactl': 'pulseaudio-utils',
            'amixer': 'alsa-utils',
            'arecord': 'alsa-utils',
            'fwupdmgr': 'fwupd',
            'bluetoothctl': 'bluez',
            'nmcli': 'NetworkManager',
        }
    ),
    'arch': DistroPackages(
        install_cmd=['sudo', 'pacman', '-S', '--needed', '--noconfirm'],
        packages={
            'lspci': 'pciutils',
            'lsusb': 'usbutils',
            'lshw': 'lshw',
            'dmidecode': 'dmidecode',
            'iw': 'iw',
            'sensors': 'lm_sensors',
            'nvme': 'nvme-cli',
            'smartctl': 'smartmontools',
            'upower': 'upower',
            'pactl': 'libpulse',
            'amixer': 'alsa-utils',
            'arecord': 'alsa-utils',
            'fwupdmgr': 'fwupd',
            'bluetoothctl': 'bluez-utils',
            'nmcli': 'networkmanager',
        }
    ),
    'opensuse': DistroPackages(
        install_cmd=['sudo', 'zypper', 'install', '-y'],
        packages={
            'lspci': 'pciutils',
            'lsusb': 'usbutils',
            'lshw': 'lshw',
            'dmidecode': 'dmidecode',
            'iw': 'iw',
            'sensors': 'lm_sensors',
            'nvme': 'nvme-cli',
            'smartctl': 'smartmontools',
            'upower': 'upower',
            'pactl': 'pulseaudio-utils',
            'amixer': 'alsa-utils',
            'arecord': 'alsa-utils',
            'fwupdmgr': 'fwupd',
            'bluetoothctl': 'bluez',
            'nmcli': 'NetworkManager',
        }
    ),
}

# Map distro IDs to package families
DISTRO_FAMILY_MAP = {
    'ubuntu': 'debian',
    'debian': 'debian',
    'linuxmint': 'debian',
    'pop': 'debian',
    'elementary': 'debian',
    'zorin': 'debian',
    'kali': 'debian',
    'fedora': 'fedora',
    'rhel': 'fedora',
    'centos': 'fedora',
    'rocky': 'fedora',
    'alma': 'fedora',
    'arch': 'arch',
    'manjaro': 'arch',
    'endeavouros': 'arch',
    'garuda': 'arch',
    'cachyos': 'arch',
    'opensuse-tumbleweed': 'opensuse',
    'opensuse-leap': 'opensuse',
    'opensuse': 'opensuse',
}

# Immutable distros that shouldn't have packages installed
IMMUTABLE_DISTROS = ['bluefin', 'bazzite', 'silverblue', 'kinoite', 'aurora']

# Nix attribute names (tool -> nixpkgs attr)
# Covers all external commands the tool calls that aren't in NixOS base
NIX_PACKAGES = {
    # Core diagnostic tools (REQUIRED_TOOLS)
    'lspci': 'pciutils',
    'lsusb': 'usbutils',
    'lshw': 'lshw',
    'dmidecode': 'dmidecode',
    'iw': 'iw',
    'sensors': 'lm_sensors',
    'nvme': 'nvme-cli',
    'fwupdmgr': 'fwupd',
    # Optional diagnostic tools (OPTIONAL_TOOLS)
    'upower': 'upower',
    'smartctl': 'smartmontools',
    'amixer': 'alsa-utils',
    'arecord': 'alsa-utils',
    'bluetoothctl': 'bluez',
    'nmcli': 'networkmanager',
    # System tools not in NixOS minimal base
    'ip': 'iproute2',
    'ping': 'iputils',
    'xrandr': 'xrandr',
    'modetest': 'libdrm',
    'mokutil': 'mokutil',
    'boltctl': 'bolt',
    'fprintd-list': 'fprintd',
    'powerprofilesctl': 'power-profiles-daemon',
    'tlp-stat': 'tlp',
    'tuned-adm': 'tuned',
    'framework_tool': 'framework-tool',
}


def get_distro_id() -> Optional[str]:
    """Read distro ID from /etc/os-release."""
    os_release = Path('/etc/os-release')
    if not os_release.exists():
        return None
    
    try:
        content = os_release.read_text()
        for line in content.split('\n'):
            if line.startswith('ID='):
                return line.split('=')[1].strip('"').lower()
    except Exception:
        pass
    
    return None


def get_distro_version() -> Optional[str]:
    """Read VERSION_ID from /etc/os-release."""
    os_release = Path('/etc/os-release')
    if not os_release.exists():
        return None
    try:
        content = os_release.read_text()
        for line in content.split('\n'):
            if line.startswith('VERSION_ID='):
                return line.split('=')[1].strip('"')
    except Exception:
        pass
    return None


def _get_distro_family(distro_id: Optional[str]) -> Optional[str]:
    """Resolve distro ID to package family, falling back to ID_LIKE.
    
    Checks DISTRO_FAMILY_MAP first. If the ID isn't listed, reads
    ID_LIKE from /etc/os-release and checks each parent distro.
    This catches unlisted derivatives (e.g. CachyOS -> arch).
    """
    if distro_id and distro_id in DISTRO_FAMILY_MAP:
        return DISTRO_FAMILY_MAP[distro_id]
    
    # Fall back to ID_LIKE
    os_release = Path('/etc/os-release')
    if not os_release.exists():
        return None
    
    try:
        content = os_release.read_text()
        for line in content.split('\n'):
            if line.startswith('ID_LIKE='):
                parents = line.split('=')[1].strip('"').lower().split()
                for parent in parents:
                    if parent in DISTRO_FAMILY_MAP:
                        return DISTRO_FAMILY_MAP[parent]
    except Exception:
        pass
    
    return None


def check_tool_available(tool: str) -> bool:
    """Check if a tool is available in PATH."""
    return shutil.which(tool) is not None


def get_missing_tools() -> tuple[list[str], list[str]]:
    """
    Check which required and optional tools are missing.
    
    Returns:
        Tuple of (missing_required, missing_optional)
    """
    missing_required = []
    missing_optional = []
    
    for tool in REQUIRED_TOOLS:
        if not check_tool_available(tool):
            missing_required.append(tool)
    
    for tool in OPTIONAL_TOOLS:
        if not check_tool_available(tool):
            missing_optional.append(tool)
    
    return missing_required, missing_optional


def install_packages(distro_id: str, packages: list[str], quiet: bool = True) -> bool:
    """
    Install packages using the appropriate package manager.
    
    Args:
        distro_id: The distro ID from os-release
        packages: List of package names to install
        quiet: Suppress output
    
    Returns:
        True if installation succeeded
    """
    family = _get_distro_family(distro_id)
    if not family or family not in DISTRO_PACKAGES:
        return False
    
    pkg_info = DISTRO_PACKAGES[family]
    
    # If already root (e.g. Debian's su -c), strip 'sudo' from install command
    install_cmd = pkg_info.install_cmd
    if os.getuid() == 0 and install_cmd and install_cmd[0] == 'sudo':
        install_cmd = install_cmd[1:]
    
    # Map tool names to package names
    pkg_names = []
    for pkg in packages:
        if pkg in pkg_info.packages:
            pkg_names.append(pkg_info.packages[pkg])
        else:
            pkg_names.append(pkg)
    
    if not pkg_names:
        return True
    
    # Special handling for apt - need to update first
    if family == 'debian':
        update_cmd = ['apt-get', 'update', '-qq'] if os.getuid() == 0 else ['sudo', 'apt-get', 'update', '-qq']
        try:
            subprocess.run(
                update_cmd,
                capture_output=True,
                timeout=60
            )
        except Exception:
            pass
    
    # Install packages
    cmd = install_cmd + pkg_names
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=quiet,
            timeout=120
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def ensure_dependencies(auto_install: bool = True, quiet: bool = False) -> bool:
    """
    Check and optionally install missing dependencies.
    
    Args:
        auto_install: Whether to automatically install missing packages
        quiet: Suppress output
    
    Returns:
        True if all required dependencies are available
    """
    distro_id = get_distro_id()
    
    # Check for immutable distros
    if distro_id in IMMUTABLE_DISTROS:
        if not quiet:
            print_info(f"Running on {distro_id} (immutable) - skipping package installation")
        missing_req, _ = get_missing_tools()
        return len(missing_req) == 0
    
    missing_required, missing_optional = get_missing_tools()
    
    if not missing_required and not missing_optional:
        return True
    
    # NixOS special handling
    if distro_id == 'nixos':
        # Already inside a nix-shell reexec — don't loop
        if os.environ.get('FW_DIAG_NIX_REEXEC'):
            if missing_required and not quiet:
                print_warning(f"Still missing after nix-shell: {', '.join(missing_required)}")
                print_info("Continuing with available tools...")
            return len(missing_required) == 0

        # Auto-reexec inside nix-shell with all dependencies
        nix_pkgs = sorted(set(NIX_PACKAGES.values()))
        script = os.path.abspath(sys.argv[0])
        args = sys.argv[1:]
        inner_cmd = f"python3 {script}" + (f" {' '.join(args)}" if args else "")

        if not quiet:
            print_info("NixOS: fetching missing tools via nix-shell...")

        env = os.environ.copy()
        env['FW_DIAG_NIX_REEXEC'] = '1'
        try:
            os.execvpe(
                'nix-shell',
                ['nix-shell', '-p'] + nix_pkgs + ['--run', inner_cmd],
                env,
            )
        except FileNotFoundError:
            if not quiet:
                print_warning("nix-shell not found — cannot auto-install")
                print_info("Install missing tools manually:")
                for pkg in nix_pkgs:
                    print_info(f"  pkgs.{pkg}")
            return len(missing_required) == 0

        return len(missing_required) == 0
    
    # Other distros - try to install
    if auto_install and _get_distro_family(distro_id):
        all_missing = missing_required + missing_optional
        
        if not quiet:
            print_info(f"Installing missing tools: {', '.join(all_missing)}")
        
        success = install_packages(distro_id, all_missing, quiet=True)
        
        if success:
            if not quiet:
                print_success("Dependencies installed successfully")
            return True
        else:
            if not quiet:
                print_warning("Some packages failed to install - continuing with available tools")
            # Re-check what's actually missing now
            missing_required, _ = get_missing_tools()
            return len(missing_required) == 0
    
    # Unknown distro or no auto-install
    if missing_required and not quiet:
        print_warning(f"Missing required tools: {', '.join(missing_required)}")
        print_info("Install these packages manually:")
        for tool in missing_required:
            print_info(f"  {tool}: {REQUIRED_TOOLS[tool]}")
    
    return len(missing_required) == 0

