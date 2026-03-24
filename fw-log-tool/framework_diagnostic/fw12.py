"""
Framework Laptop 12 specific diagnostics.

The FW12 is a 2-in-1 convertible with features not present on other models:
- Tablet mode (lid folds 360°, disables keyboard/trackpad)
- Screen rotation (accelerometer-based via cros_ec)
- Touchscreen + stylus support

These features require specific kernel modules and services.
Reference: Framework 12 Debugging Guide
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import re
from .utils import run_command
from .dependencies import get_distro_id, _get_distro_family, get_distro_version
import re


@dataclass
class TabletModeStatus:
    """Tablet mode hardware/driver status."""
    # Kernel modules
    pinctrl_loaded: bool = False
    pinctrl_builtin: bool = False  # =y in kernel config (not a loadable module)
    soc_button_loaded: bool = False
    # GPIO detection
    gpio_keys_detected: bool = False
    # Overall
    working: bool = False
    issue: str = ""


@dataclass
class ScreenRotationStatus:
    """Screen rotation (accelerometer) status."""
    # EC driver
    cros_ec_detected: bool = False
    # Sensor driver
    cros_ec_sensors_loaded: bool = False
    # IIO device present
    iio_accel_present: bool = False
    # iio-sensor-proxy service
    sensor_proxy_running: bool = False
    sensor_proxy_version: str = ""
    # iio-buffer-accel udev rule (3.7 bug: active = broken)
    iio_buffer_accel_rule_active: Optional[bool] = None  # None = couldn't check
    # Functional test: does SensorProxy actually report a working accelerometer?
    accel_functional: Optional[bool] = None  # None = couldn't check
    # Overall
    working: bool = False
    issue: str = ""


@dataclass
class FW12Diagnostics:
    """All Framework 12 specific diagnostic results."""
    is_fw12: bool = False
    tablet_mode: Optional[TabletModeStatus] = None
    screen_rotation: Optional[ScreenRotationStatus] = None
    is_kde: bool = False
    plasma_major: int = 0  # 5 or 6
    plasma_minor: int = 0  # e.g. 6 for 6.6
    virtual_keyboard_installed: bool = False
    virtual_keyboard_pkg: str = ""  # which package was found


def check_tablet_mode() -> TabletModeStatus:
    """Check tablet mode functionality.
    
    Requires:
    1. pinctrl_tigerlake kernel module loaded (must load before soc_button_array)
    2. soc_button_array kernel module loaded
    3. gpio-keys input device present in /proc/bus/input/devices
    """
    status = TabletModeStatus()
    
    # Check kernel modules
    rc, stdout, _ = run_command(['lsmod'])
    if rc == 0:
        status.pinctrl_loaded = 'pinctrl_tigerlake' in stdout
        status.soc_button_loaded = 'soc_button_array' in stdout
    
    # Fallback: pinctrl_tigerlake may be built-in (=y) rather than a loadable module (=m).
    # Debian kernels do this. lsmod won't show built-in modules.
    if not status.pinctrl_loaded:
        rc, stdout, _ = run_command(['bash', '-c',
            'cat /boot/config-$(uname -r) 2>/dev/null | grep CONFIG_PINCTRL_TIGERLAKE'])
        if rc == 0 and 'CONFIG_PINCTRL_TIGERLAKE=y' in stdout:
            status.pinctrl_loaded = True
            status.pinctrl_builtin = True
    
    # Check gpio-keys input device actually exists (not dmesg — ring buffer is unreliable)
    rc, stdout, _ = run_command(['bash', '-c',
        'cat /proc/bus/input/devices 2>/dev/null'])
    if rc == 0 and 'gpio-keys' in stdout.lower():
        status.gpio_keys_detected = True
    else:
        # Fallback: check sysfs input device names
        rc, stdout, _ = run_command(['bash', '-c',
            'cat /sys/class/input/*/name 2>/dev/null'])
        if rc == 0:
            status.gpio_keys_detected = 'gpio-keys' in stdout.lower()
    
    # Determine status
    if status.pinctrl_loaded and status.soc_button_loaded and status.gpio_keys_detected:
        status.working = True
    elif not status.pinctrl_loaded and not status.soc_button_loaded:
        status.issue = "Kernel modules not loaded: pinctrl_tigerlake, soc_button_array"
    elif not status.pinctrl_loaded:
        status.issue = "pinctrl_tigerlake not loaded (must load before soc_button_array)"
    elif not status.soc_button_loaded:
        status.issue = "soc_button_array not loaded"
    elif not status.gpio_keys_detected:
        status.issue = ("gpio-keys not detected — pinctrl_tigerlake may have loaded "
                       "after soc_button_array.")
    
    return status


def check_screen_rotation() -> ScreenRotationStatus:
    """Check screen rotation (accelerometer) functionality.
    
    Requires:
    1. cros_ec driver recognized the system (kernel 6.12+)
    2. cros_ec_sensors module for accelerometer data
    3. IIO accelerometer device exposed in sysfs
    4. iio-sensor-proxy daemon running
    
    Known issue: iio-sensor-proxy 3.7 has a regression that breaks
    accelerometer. Fixed in 3.8. Workaround: downgrade to 3.6 or
    edit udev rules.
    """
    status = ScreenRotationStatus()
    
    # Check cros_ec device exists in sysfs (not dmesg — ring buffer is unreliable)
    rc, stdout, _ = run_command(['bash', '-c',
        'ls /sys/bus/platform/devices/cros_ec* 2>/dev/null || '
        'ls /sys/class/chromeos/cros_ec 2>/dev/null'])
    if rc == 0 and stdout.strip():
        status.cros_ec_detected = True
    else:
        # Fallback: check if cros_ec module is loaded
        rc, stdout, _ = run_command(['lsmod'])
        if rc == 0 and 'cros_ec' in stdout:
            status.cros_ec_detected = True
    
    # Check cros_ec_sensors module
    rc, stdout, _ = run_command(['lsmod'])
    if rc == 0:
        status.cros_ec_sensors_loaded = 'cros_ec_sensors' in stdout
    
    # Check IIO accelerometer device
    rc, stdout, _ = run_command(['bash', '-c',
        'for d in /sys/bus/iio/devices/iio:device*/name; do '
        'cat "$d" 2>/dev/null; done'])
    if rc == 0:
        status.iio_accel_present = 'cros-ec-accel' in stdout
    
    # Check iio-sensor-proxy service
    rc, stdout, _ = run_command(['systemctl', 'is-active', 'iio-sensor-proxy.service'])
    if rc == 0:
        status.sensor_proxy_running = stdout.strip() == 'active'
    
    # Get iio-sensor-proxy version (important: 3.7 is broken)
    rc, stdout, _ = run_command(['bash', '-c',
        'iio-sensor-proxy --version 2>/dev/null || '
        'rpm -q iio-sensor-proxy 2>/dev/null || '
        'dpkg -l iio-sensor-proxy 2>/dev/null | grep ^ii | awk \'{print $3}\' || '
        'pacman -Q iio-sensor-proxy 2>/dev/null'])
    if rc == 0 and stdout.strip():
        # Extract version number
        ver_match = re.search(r'(\d+\.\d+)', stdout)
        if ver_match:
            status.sensor_proxy_version = ver_match.group(1)

    # NixOS fallback: check the binary in the nix store for version info
    if not status.sensor_proxy_version:
        rc, stdout, _ = run_command(['bash', '-c',
            'readlink -f $(which iio-sensor-proxy 2>/dev/null) 2>/dev/null || '
            'find /nix/store -maxdepth 2 -name iio-sensor-proxy -type f 2>/dev/null | head -1'])
        if rc == 0 and '/nix/store/' in (stdout or ''):
            # Extract version from nix store path (e.g. /nix/store/xxx-iio-sensor-proxy-3.7/...)
            ver_match = re.search(r'iio-sensor-proxy-(\d+\.\d+)', stdout)
            if ver_match:
                status.sensor_proxy_version = ver_match.group(1)
    
    # Check if the iio-buffer-accel udev rule is active (the actual 3.7 bug trigger).
    # If this rule is uncommented, iio-sensor-proxy grabs the accel as a buffer device
    # and desktop environments can't get orientation events.
    # Check override first (/etc), then system rule locations.
    rc, stdout, _ = run_command(['bash', '-c',
        'grep -l "iio-buffer-accel" '
        '/etc/udev/rules.d/80-iio-sensor-proxy.rules '
        '/usr/lib/udev/rules.d/80-iio-sensor-proxy.rules '
        '/nix/store/*/lib/udev/rules.d/80-iio-sensor-proxy.rules '
        '2>/dev/null | head -1'])
    if rc == 0 and stdout.strip():
        rules_file = stdout.strip()
        rc2, content, _ = run_command(['bash', '-c', f'grep "iio-buffer-accel" "{rules_file}"'])
        if rc2 == 0 and content.strip():
            # Check if the line is commented out (fix applied) or active (bug present)
            active_lines = [l for l in content.strip().split('\n')
                           if 'iio-buffer-accel' in l and not l.strip().startswith('#')]
            status.iio_buffer_accel_rule_active = len(active_lines) > 0

    # Functional test: ask SensorProxy if accelerometer actually works.
    # This catches cases where 3.7 + active udev rule is NOT broken (e.g. Debian Trixie).
    if status.sensor_proxy_running:
        rc, stdout, _ = run_command(['busctl', 'get-property',
            'net.hadess.SensorProxy', '/net/hadess/SensorProxy',
            'net.hadess.SensorProxy', 'HasAccelerometer'])
        if rc == 0 and stdout.strip():
            status.accel_functional = 'true' in stdout.lower()

    # Determine status
    # If the IIO accel device exists in sysfs, cros_ec worked regardless
    # of whether we found the dmesg message (ring buffer may have rolled)
    if not status.iio_accel_present and not status.cros_ec_detected:
        status.issue = "cros_ec not detected — kernel 6.12+ required for FW12 accelerometer"
    elif not status.iio_accel_present:
        status.issue = "cros_ec detected but IIO accelerometer device not found — cros_ec_sensors module may not be loaded"
    elif not status.sensor_proxy_running:
        status.issue = "iio-sensor-proxy service not running"
    elif status.sensor_proxy_version == '3.7' and status.iio_buffer_accel_rule_active is True:
        if status.accel_functional is True:
            # 3.7 + active rule but accelerometer works — not actually broken
            status.working = True
        else:
            status.issue = ("iio-sensor-proxy 3.7 iio-buffer-accel udev rule is active — "
                           "this breaks accelerometer on Framework 12")
            # Hardware side works, just the udev rule is wrong
            status.working = True
    elif status.sensor_proxy_version == '3.7' and status.iio_buffer_accel_rule_active is False:
        # 3.7 with patched rule — bug is fixed, no warning needed
        status.working = True
    elif status.sensor_proxy_version == '3.7':
        # 3.7 but couldn't determine rule status — warn to be safe
        status.issue = ("iio-sensor-proxy 3.7 detected — check that iio-buffer-accel "
                       "udev rule is patched (commented out)")
        status.working = True
    else:
        status.working = True
    
    return status


def detect_fw12_diagnostics(model_type: str, desktop_environment: str = "") -> FW12Diagnostics:
    """Run Framework 12 specific diagnostics.
    
    Only runs on FW12 hardware.
    """
    diag = FW12Diagnostics()
    
    if model_type != 'Laptop 12':
        return diag
    
    diag.is_fw12 = True
    diag.tablet_mode = check_tablet_mode()
    diag.screen_rotation = check_screen_rotation()
    
    # KDE virtual keyboard check
    if 'kde' in desktop_environment.lower() or 'plasma' in desktop_environment.lower():
        diag.is_kde = True
        
        # Detect Plasma version
        rc, stdout, _ = run_command(['plasmashell', '--version'], timeout=5)
        if rc == 0:
            # Output: "plasmashell 6.6.0" or "plasmashell 5.27.11"
            m = re.search(r'(\d+)\.(\d+)', stdout)
            if m:
                diag.plasma_major = int(m.group(1))
                diag.plasma_minor = int(m.group(2))
        
        if diag.plasma_major >= 6:
            # Plasma 6: plasma-keyboard (6.6+) or maliit-keyboard (6.0-6.5)
            # Check both — distro packaging varies
            for pkg in ('plasma-keyboard', 'maliit-keyboard'):
                rc, _, _ = run_command(['pacman', '-Q', pkg])
                if rc == 0:
                    diag.virtual_keyboard_installed = True
                    diag.virtual_keyboard_pkg = pkg
                    break
                rc, _, _ = run_command(['bash', '-c',
                    f'dpkg -l {pkg} 2>/dev/null | grep -q ^ii || '
                    f'rpm -q {pkg} 2>/dev/null'])
                if rc == 0:
                    diag.virtual_keyboard_installed = True
                    diag.virtual_keyboard_pkg = pkg
                    break
        else:
            # Plasma 5: maliit-keyboard (Qt5)
            rc, _, _ = run_command(['which', 'maliit-keyboard'])
            if rc == 0:
                diag.virtual_keyboard_installed = True
                diag.virtual_keyboard_pkg = 'maliit-keyboard'
            else:
                rc, _, _ = run_command(['bash', '-c',
                    'dpkg -l maliit-keyboard 2>/dev/null | grep -q ^ii || '
                    'rpm -q maliit-keyboard 2>/dev/null'])
                if rc == 0:
                    diag.virtual_keyboard_installed = True
                    diag.virtual_keyboard_pkg = 'maliit-keyboard'
    
    return diag


def format_fw12_report(diag: FW12Diagnostics) -> list[str]:
    """Format FW12 diagnostic results for the report."""
    if not diag.is_fw12:
        return []
    
    distro_id = get_distro_id()
    distro_family = _get_distro_family(distro_id)
    distro_version = get_distro_version()
    
    # Linux Mint (based on Ubuntu 24.04) and Ubuntu 24.x — no FW12 tablet support
    if distro_id == 'linuxmint' or (distro_id == 'ubuntu' and distro_version and distro_version.startswith('24.')):
        return ["Framework 12 Features:",
                "  Framework 12 tablet mode requires Ubuntu 25.10 or later."]
    
    lines = []
    lines.append("Framework 12 Features:")
    lines.append("")
    
    # Tablet mode status
    tm = diag.tablet_mode
    if tm:
        if tm.working:
            lines.append("  Tablet Mode: ✅ Working")
        else:
            lines.append("  Tablet Mode: ❌ Not working")
            if tm.issue:
                lines.append(f"    Issue: {tm.issue}")
        pinctrl_label = '✅ (built-in)' if tm.pinctrl_builtin else ('✅' if tm.pinctrl_loaded else '❌')
        lines.append(f"    pinctrl_tigerlake:  {pinctrl_label}")
        lines.append(f"    soc_button_array:   {'✅' if tm.soc_button_loaded else '❌'}")
        lines.append(f"    gpio-keys:          {'✅' if tm.gpio_keys_detected else '❌'}")
        # Debian: show persistent fix status when working
        # If pinctrl is built-in, soc_button_array loads reliably every boot — no conf needed
        if distro_id == 'debian' and tm.working and not tm.pinctrl_builtin:
            if Path('/etc/modules-load.d/fw12-tablet.conf').exists():
                lines.append("    fw12-tablet.conf:   ✅ installed")
            else:
                lines.append("    ⚠️  Working now, but may not persist across reboots")
                lines.append("    Permanent fix (run as root with: su -):")
                if tm.pinctrl_builtin:
                    lines.append('      echo "soc_button_array" > /etc/modules-load.d/fw12-tablet.conf')
                else:
                    lines.append('      echo -e "pinctrl_tigerlake\\nsoc_button_array" > /etc/modules-load.d/fw12-tablet.conf')
    
    lines.append("")
    
    # Screen rotation status
    sr = diag.screen_rotation
    if sr:
        if sr.working and not sr.issue:
            lines.append("  Screen Rotation: ✅ Working")
        elif sr.working and sr.issue:
            lines.append("  Screen Rotation: ⚠️ Working (with known issue)")
        else:
            lines.append("  Screen Rotation: ❌ Not working")
            if sr.issue:
                lines.append(f"    Issue: {sr.issue}")
        cros_ec_ok = sr.cros_ec_detected or sr.iio_accel_present
        ver_str = f" v{sr.sensor_proxy_version}" if sr.sensor_proxy_version else ""
        lines.append(f"    cros_ec:            {'✅' if cros_ec_ok else '❌'}")
        lines.append(f"    iio-accel:          {'✅' if sr.iio_accel_present else '❌'}")
        lines.append(f"    sensor-proxy:       {'✅' if sr.sensor_proxy_running else '❌'}{ver_str}")
        if sr.iio_buffer_accel_rule_active is True and sr.sensor_proxy_version == '3.7':
            if sr.accel_functional is True:
                lines.append("    udev rule:          ✅ active (accelerometer functional)")
            else:
                lines.append("    udev rule:          ⚠️ iio-buffer-accel active (causes 3.7 regression)")
        elif sr.iio_buffer_accel_rule_active is False and sr.sensor_proxy_version == '3.7':
            lines.append("    udev rule:          ✅ patched")
    
    # NixOS: full step-by-step guide when fixes are needed
    if distro_id == 'nixos':
        has_fixes = (tm and not tm.working) or (sr and (not sr.working or sr.issue))
        if has_fixes:
            lines.extend(_nixos_fw12_guide(diag))
        return lines
    
    # Non-NixOS: use distro-specific fix suggestions
    if tm and not tm.working:
        distro_version = get_distro_version() if distro_id == 'ubuntu' else None
        lines.extend(_tablet_mode_fix(tm, distro_id, distro_family, distro_version))
    
    if sr and (not sr.working or sr.issue):
        lines.extend(_rotation_fix(sr, distro_id, distro_family))
    
    # Virtual keyboard (KDE only, non-NixOS)
    if diag.is_kde:
        if diag.virtual_keyboard_installed:
            lines.append(f"  On-Screen Keyboard: ✅ {diag.virtual_keyboard_pkg} installed")
        else:
            lines.append(f"  On-Screen Keyboard: ❌ Not installed")
            lines.extend(_virtual_keyboard_fix(distro_id, distro_family,
                                               diag.plasma_major, diag.plasma_minor))
    
    return lines


def _nixos_fw12_guide(diag: FW12Diagnostics) -> list[str]:
    """Full step-by-step NixOS fix guide for Framework 12."""
    lines = []
    lines.append("")
    lines.append("Framework 12 NixOS Fix: Tablet Mode + Screen Rotation")
    lines.append("======================================================")
    lines.append("")
    lines.append("STEP 1: Add the nixos-hardware channel")
    lines.append("---------------------------------------")
    lines.append("Copy and paste this entire line into your terminal:")
    lines.append("")
    lines.append("sudo nix-channel --add https://github.com/NixOS/nixos-hardware/archive/master.tar.gz nixos-hardware && sudo nix-channel --update")
    lines.append("")
    lines.append("Wait for it to finish.")
    lines.append("")
    lines.append("")
    lines.append("STEP 2: Edit configuration.nix")
    lines.append("-------------------------------")
    lines.append("Open the file:")
    lines.append("")
    lines.append("sudo nano /etc/nixos/configuration.nix")
    lines.append("")
    lines.append("Find the imports section near the top. It looks something like this:")
    lines.append("")
    lines.append("    imports = [")
    lines.append("        ./hardware-configuration.nix")
    lines.append("    ];")
    lines.append("")
    lines.append("Change it to:")
    lines.append("")
    lines.append("    imports = [")
    lines.append("        ./hardware-configuration.nix")
    lines.append("        <nixos-hardware/framework/12-inch/13th-gen-intel>")
    lines.append("    ];")
    lines.append("")
    lines.append("Then find an empty line anywhere in the file and add:")
    lines.append("")
    lines.append("    hardware.sensor.iio.enable = true;")
    lines.append("")
    lines.append("Save and exit: Ctrl+O, Enter, Ctrl+X")
    lines.append("")
    lines.append("")
    lines.append("STEP 3: Rebuild and reboot")
    lines.append("---------------------------")
    lines.append("Copy and paste:")
    lines.append("")
    lines.append("sudo nixos-rebuild switch")
    lines.append("")
    lines.append("")
    lines.append("STEP 4: After reboot, run the diagnostic again")
    lines.append("-----------------------------------------------")
    lines.append("Both Tablet Mode and Screen Rotation should show as working.")
    lines.append("")
    lines.append("GNOME will provide rotation now, but, KDE Plasma has proven to be more")
    lines.append("reliable on NixOS for onscreen keyboard.")
    lines.append("")
    lines.append("To switch to KDE Plasma, add the following to configuration.nix:")
    lines.append("")
    lines.append("  # Enable the KDE Plasma Desktop Environment.")
    lines.append("  services.displayManager.sddm.enable = true;")
    lines.append("  services.desktopManager.plasma6.enable = true;")
    lines.append("")
    lines.append("")
    lines.append("  environment.systemPackages = with pkgs; [")
    lines.append("    # ... your other packages ...")
    lines.append("    maliit-keyboard")
    lines.append("    maliit-framework")
    lines.append("  ];")
    lines.append("")
    lines.append("")
    lines.append("  services.displayManager.sddm.settings = {")
    lines.append("    General = {")
    lines.append('      InputMethod = "qtvirtualkeyboard";')
    lines.append("    };")
    lines.append("  };")
    lines.append("  services.displayManager.sddm.extraPackages = [ pkgs.kdePackages.qtvirtualkeyboard ];")
    lines.append("")
    lines.append("")
    lines.append("  sudo nixos-rebuild switch")
    
    return lines


def _tablet_mode_fix(tm: TabletModeStatus, distro_id: Optional[str],
                     distro_family: Optional[str],
                     distro_version: Optional[str] = None) -> list[str]:
    """Generate distro-specific tablet mode fix suggestions."""
    lines = []
    
    if distro_id == 'nixos':
        lines.append("")
        lines.append("    Tablet Mode Fix (recommended):")
        lines.append("        Add to configuration.nix:")
        lines.append("            imports = [ <nixos-hardware/framework/12-inch/13th-gen-intel> ];")
        lines.append("")
        lines.append("    Tablet Mode Fix (manual alternative):")
        lines.append("        Add to configuration.nix:")
        lines.append("            boot.initrd.kernelModules = [ \"pinctrl_tigerlake\" ];")
    elif distro_family == 'arch':
        if not tm.pinctrl_loaded:
            lines.append("")
            lines.append("    Tablet Mode Fix:")
            lines.append("      sudo modprobe pinctrl_tigerlake")
            lines.append("      sudo modprobe soc_button_array")
        elif tm.pinctrl_loaded and tm.soc_button_loaded and not tm.gpio_keys_detected:
            # Boot race — pinctrl loaded after soc_button_array
            lines.append("")
            lines.append("    Tablet Mode Fix (temporary):")
            lines.append("      sudo rmmod soc_button_array && sudo modprobe soc_button_array")
        
        # Persistent fix for boot race
        if not Path('/etc/systemd/system/fw12-tablet-fix.service').exists():
            lines.append("")
            lines.append("    Tablet Mode Fix (permanent):")
            lines.append('      printf \'[Unit]\\nDescription=Reload soc_button_array for FW12 tablet mode\\nAfter=multi-user.target\\n\\n[Service]\\nType=oneshot\\nExecStart=/bin/sh -c "rmmod soc_button_array && modprobe soc_button_array"\\n\\n[Install]\\nWantedBy=multi-user.target\\n\' | sudo tee /etc/systemd/system/fw12-tablet-fix.service')
            lines.append("      sudo systemctl enable fw12-tablet-fix.service")
        else:
            lines.append("    fw12-tablet-fix.service: ✅ installed")
    elif distro_id == 'ubuntu':
        if distro_version and (distro_version.startswith('24.') or distro_version == '25.04'):
            lines.append("")
            lines.append("    Framework 12 tablet mode requires Ubuntu 25.10 or later.")
        elif distro_version and distro_version.startswith('25.10'):
            svc_exists = Path('/etc/systemd/system/reload-soc-module.service').exists()
            if svc_exists:
                lines.append("    reload-soc-module.service: ✅ installed")
            else:
                lines.append("")
                lines.append("    Tablet Mode Fix (Ubuntu 25.10 workaround):")
                lines.append("    See: https://github.com/FrameworkComputer/linux-docs/blob/main/framework12/Ubuntu-25-04-accel-ubuntu25.04.md#2504-and-2510-both-apply-to-this-guide")
        else:
            # Other Ubuntu versions — generic fix
            if not tm.pinctrl_loaded:
                lines.append("")
                lines.append("    Tablet Mode Fix:")
                lines.append("      sudo modprobe pinctrl_tigerlake")
                lines.append("      sudo modprobe soc_button_array")
            elif not tm.gpio_keys_detected:
                lines.append("")
                lines.append("    Tablet Mode Fix (module load order race):")
                lines.append("      sudo rmmod soc_button_array && sudo modprobe soc_button_array")
    elif distro_id == 'debian':
        conf_exists = Path('/etc/modules-load.d/fw12-tablet.conf').exists()
        if not tm.pinctrl_loaded:
            lines.append("")
            lines.append("    Tablet Mode Fix (run as root with: su -):")
            lines.append("      modprobe pinctrl_tigerlake")
            lines.append("      modprobe soc_button_array")
        elif not tm.soc_button_loaded:
            lines.append("")
            lines.append("    Tablet Mode Fix (run as root with: su -):")
            lines.append("      modprobe soc_button_array")
        elif not tm.gpio_keys_detected:
            lines.append("")
            lines.append("    Tablet Mode Fix (run as root with: su -):")
            lines.append("      rmmod soc_button_array && modprobe soc_button_array")
        if not conf_exists:
            lines.append("")
            lines.append("    Tablet Mode Fix (permanent, run as root with: su -):")
            if tm.pinctrl_builtin:
                lines.append('      echo "soc_button_array" > /etc/modules-load.d/fw12-tablet.conf')
            else:
                lines.append('      echo -e "pinctrl_tigerlake\\nsoc_button_array" > /etc/modules-load.d/fw12-tablet.conf')
        else:
            lines.append("    fw12-tablet.conf: ✅ installed")
    else:
        # Generic Linux
        if not tm.pinctrl_loaded:
            lines.append("")
            lines.append("    Tablet Mode Fix:")
            lines.append("      sudo modprobe pinctrl_tigerlake")
            lines.append("      sudo modprobe soc_button_array")
        elif not tm.gpio_keys_detected:
            lines.append("")
            lines.append("    Tablet Mode Fix (module load order race):")
            lines.append("      sudo rmmod soc_button_array && sudo modprobe soc_button_array")
    
    return lines


def _rotation_fix(sr: ScreenRotationStatus, distro_id: Optional[str],
                  distro_family: Optional[str]) -> list[str]:
    """Generate distro-specific screen rotation fix suggestions."""
    lines = []
    
    if distro_id == 'nixos':
        # Only show fixes relevant to the actual problem
        if not sr.cros_ec_detected or not sr.iio_accel_present:
            lines.append("")
            lines.append("    Screen Rotation Fix (hardware module):")
            lines.append("        Add to configuration.nix:")
            lines.append("            imports = [ <nixos-hardware/framework/12-inch/13th-gen-intel> ];")
        
        if not sr.sensor_proxy_running:
            lines.append("")
            lines.append("    Screen Rotation Fix:")
            lines.append("        Add to configuration.nix:")
            lines.append("            hardware.sensor.iio.enable = true;")
        
        if sr.iio_buffer_accel_rule_active is True and sr.sensor_proxy_version == '3.7':
            lines.append("")
            lines.append("    Screen Rotation Fix (iio-sensor-proxy 3.7 bug):")
            lines.append("    iio-sensor-proxy 3.7 has a broken udev rule (iio-buffer-accel).")
            lines.append("    NixOS 25.05 and 25.11 (unstable) include the fix — update your system.")
            lines.append("")
            lines.append("    If you can't update, add this overlay to configuration.nix:")
            lines.append("      nixpkgs.overlays = [")
            lines.append("        (final: prev: {")
            lines.append("          iio-sensor-proxy = prev.iio-sensor-proxy.overrideAttrs (oldAttrs: {")
            lines.append("            postPatch = oldAttrs.postPatch + ''")
            lines.append("              sed -i -e 's/.*iio-buffer-accel/#&/' data/80-iio-sensor-proxy.rules")
            lines.append("            '';")
            lines.append("          });")
            lines.append("        })")
            lines.append("      ];")
        elif sr.iio_buffer_accel_rule_active is None and sr.sensor_proxy_version == '3.7':
            lines.append("")
            lines.append("    Note: iio-sensor-proxy 3.7 detected but couldn't verify udev rule status.")
            lines.append("    NixOS 25.05 and 25.11 include the fix. If you're on an older release,")
            lines.append("    see: https://github.com/FrameworkComputer/linux-docs/blob/main/framework12/nixOS.md")
    elif distro_family == 'arch':
        if not sr.sensor_proxy_running:
            lines.append("")
            lines.append("    Screen Rotation Fix:")
            lines.append("      sudo pacman -S iio-sensor-proxy")
            lines.append("      sudo systemctl enable --now iio-sensor-proxy")
        if sr.iio_buffer_accel_rule_active is True and sr.sensor_proxy_version == '3.7':
            lines.append("")
            lines.append("    Screen Rotation Fix (iio-sensor-proxy 3.7 bug):")
            lines.append("      sudo sed 's/.*iio-buffer-accel/#&/' /usr/lib/udev/rules.d/80-iio-sensor-proxy.rules | sudo tee /etc/udev/rules.d/80-iio-sensor-proxy.rules")
            lines.append("      sudo udevadm trigger --settle")
            lines.append("      sudo systemctl restart iio-sensor-proxy")
        elif sr.iio_buffer_accel_rule_active is None and sr.sensor_proxy_version == '3.7':
            lines.append("")
            lines.append("    Screen Rotation Note (iio-sensor-proxy 3.7):")
            lines.append("    Check if iio-buffer-accel udev rule needs patching:")
            lines.append("      grep iio-buffer-accel /usr/lib/udev/rules.d/80-iio-sensor-proxy.rules")
            lines.append("    If the line is NOT commented out, patch it:")
            lines.append("      sudo sed 's/.*iio-buffer-accel/#&/' /usr/lib/udev/rules.d/80-iio-sensor-proxy.rules | sudo tee /etc/udev/rules.d/80-iio-sensor-proxy.rules")
            lines.append("      sudo udevadm trigger --settle")
            lines.append("      sudo systemctl restart iio-sensor-proxy")
    elif distro_id == 'ubuntu':
        if not sr.sensor_proxy_running:
            lines.append("")
            lines.append("    Screen Rotation Fix:")
            lines.append("      sudo apt install iio-sensor-proxy")
            lines.append("      sudo systemctl enable --now iio-sensor-proxy")
        if sr.iio_buffer_accel_rule_active is True and sr.sensor_proxy_version == '3.7':
            lines.append("")
            lines.append("    Screen Rotation Fix (iio-sensor-proxy 3.7 bug):")
            lines.append("    See: https://github.com/FrameworkComputer/linux-docs/blob/main/framework12/Ubuntu-25-04-accel-ubuntu25.04.md#2504-and-2510-both-apply-to-this-guide")
    elif distro_id == 'debian':
        if not sr.sensor_proxy_running:
            lines.append("")
            lines.append("    Screen Rotation Fix (run as root with: su -):")
            lines.append("      apt install iio-sensor-proxy")
            lines.append("      systemctl enable --now iio-sensor-proxy")
        if sr.iio_buffer_accel_rule_active is True and sr.sensor_proxy_version == '3.7':
            lines.append("")
            lines.append("    Screen Rotation Fix (iio-sensor-proxy 3.7 bug, run as root with: su -):")
            lines.append("      sed 's/.*iio-buffer-accel/#&/' /usr/lib/udev/rules.d/80-iio-sensor-proxy.rules > /etc/udev/rules.d/80-iio-sensor-proxy.rules")
            lines.append("      udevadm trigger --settle")
            lines.append("      systemctl restart iio-sensor-proxy")
    else:
        # Generic
        if not sr.sensor_proxy_running:
            lines.append("")
            lines.append("    Screen Rotation Fix:")
            lines.append("    Install and enable iio-sensor-proxy for your distribution.")
    
    return lines


def _virtual_keyboard_fix(distro_id: Optional[str],
                          distro_family: Optional[str],
                          plasma_major: int = 6,
                          plasma_minor: int = 0) -> list[str]:
    """Generate distro-specific on-screen keyboard install instructions for KDE Plasma."""
    lines = []
    
    # plasma-keyboard is new in Plasma 6.6; before that, all distros used maliit-keyboard.
    # Debian doesn't package plasma-keyboard regardless of version.
    has_plasma_kbd = (plasma_major > 6 or (plasma_major == 6 and plasma_minor >= 6)) \
                     and distro_family != 'debian'
    
    if has_plasma_kbd:
        pkg = "plasma-keyboard"
        settings_path = "System Settings → Keyboard → Virtual Keyboard"
    else:
        pkg = "maliit-keyboard"
        if plasma_major >= 6:
            settings_path = "System Settings → Keyboard → Virtual Keyboard → select Maliit"
        else:
            settings_path = "System Settings → Input & Output → Virtual Keyboard → select Maliit"
    
    if distro_id == 'nixos':
        lines.append("")
        lines.append(f"    Install {pkg} for on-screen keyboard in tablet mode.")
        lines.append(f"    Search for the package: https://search.nixos.org/packages?query={pkg}")
    elif distro_family == 'arch':
        if has_plasma_kbd:
            # plasma-keyboard is in [extra]
            lines.append("")
            lines.append("    To install:")
            lines.append(f"      sudo pacman -S {pkg}")
        else:
            # maliit-keyboard is AUR-only on Arch
            lines.append("")
            lines.append(f"    {pkg} is in the AUR (not in official repos).")
            lines.append("    To install with an AUR helper:")
            lines.append(f"      paru -S {pkg}")
            lines.append("    or:")
            lines.append(f"      yay -S {pkg}")
    elif distro_family == 'fedora':
        lines.append("")
        lines.append("    To install:")
        lines.append(f"      sudo dnf install {pkg}")
    elif distro_family == 'debian':
        lines.append("")
        lines.append("    To install:")
        lines.append(f"      sudo apt install {pkg}")
    elif distro_family == 'opensuse':
        lines.append("")
        lines.append("    To install:")
        lines.append(f"      sudo zypper install {pkg}")
    else:
        lines.append("")
        lines.append(f"    Install {pkg} for your distribution.")
    
    lines.append("")
    lines.append("    To activate in KDE Plasma:")
    lines.append(f"      {settings_path}")
    
    return lines
