"""
Sleep/suspend/resume status analysis.

NEW FUNCTION: check_sleep_status()
Reports:
- Sleep mode (s2idle vs deep)
- s2idle status
- ACPI states
- Suspend/resume counts
- Inhibitors
- Resume errors
- AMD PMC issues
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from enum import Enum


class SleepMode(Enum):
    """Available sleep modes."""
    S2IDLE = 's2idle'
    DEEP = 'deep'
    UNKNOWN = 'unknown'


@dataclass
class SleepStatus:
    """Complete sleep/suspend status."""
    # Current configuration
    current_mode: SleepMode = SleepMode.UNKNOWN
    available_modes: list[str] = field(default_factory=list)
    s2idle_enabled: bool = False
    
    # Raw evidence from /sys files
    mem_sleep_raw: str = ""  # Raw content of /sys/power/mem_sleep
    state_raw: str = ""  # Raw content of /sys/power/state
    
    # Kernel suspend stats (from /sys/power/suspend_stats/)
    kernel_suspend_success: int = 0
    kernel_suspend_fail: int = 0
    
    # ACPI states
    acpi_states: list[str] = field(default_factory=list)
    
    # Counters from logs
    suspend_count: int = 0
    resume_count: int = 0
    failed_suspend_count: int = 0
    failed_resume_count: int = 0
    
    # Timestamps from logs
    last_suspend_time: str = ""
    last_resume_time: str = ""
    
    # PSR (Panel Self Refresh) status
    psr_enabled: bool = False
    psr_status: str = ""
    psr_issues: list[str] = field(default_factory=list)  # Issues like screen blinking on resume
    
    # Inhibitors
    inhibitors: list[str] = field(default_factory=list)
    
    # Error tracking
    resume_errors: list[str] = field(default_factory=list)
    amd_pmc_issues: list[str] = field(default_factory=list)
    
    # Health status
    is_healthy: bool = True
    issues: list[str] = field(default_factory=list)
    
    # Sleep blockers (NEW)
    blockers: list['S2IdleBlocker'] = field(default_factory=list)
    
    # Framework workaround services
    disable_wakeup_service: Optional[bool] = None  # True=exists


@dataclass
class S2IdleBlocker:
    """A device or condition blocking proper s2idle/sleep."""
    device: str
    reason: str
    fix: str
    source: str = ""  # Where we found this (log, sysfs, etc.)


# Inhibitors to ignore (provide no useful info - normal system services)



def get_current_sleep_mode() -> tuple[SleepMode, list[str], str]:
    """
    Read the current sleep mode from /sys/power/mem_sleep.
    
    The file format is: "[s2idle] deep" where brackets indicate current.
    
    Returns:
        Tuple of (current_mode, available_modes, raw_content)
    """
    mem_sleep_path = Path('/sys/power/mem_sleep')
    
    if not mem_sleep_path.exists():
        return SleepMode.UNKNOWN, [], ""
    
    try:
        content = mem_sleep_path.read_text().strip()
        available = content.replace('[', '').replace(']', '').split()
        
        # Find the currently selected mode (in brackets)
        match = re.search(r'\[(\w+)\]', content)
        if match:
            mode_str = match.group(1)
            if mode_str == 's2idle':
                return SleepMode.S2IDLE, available, content
            elif mode_str == 'deep':
                return SleepMode.DEEP, available, content
        
        return SleepMode.UNKNOWN, available, content
    except Exception:
        return SleepMode.UNKNOWN, [], ""


def get_acpi_sleep_states() -> tuple[list[str], str]:
    """
    Read available ACPI sleep states from /sys/power/state.
    
    Returns:
        Tuple of (states_list, raw_content)
    """
    state_path = Path('/sys/power/state')
    
    if not state_path.exists():
        return [], ""
    
    try:
        content = state_path.read_text().strip()
        return content.split(), content
    except Exception:
        return [], ""


def get_kernel_suspend_stats() -> tuple[int, int]:
    """
    Read kernel suspend statistics from /sys/power/suspend_stats/.
    
    Returns:
        Tuple of (success_count, fail_count)
    """
    success = 0
    fail = 0
    
    success_path = Path('/sys/power/suspend_stats/success')
    fail_path = Path('/sys/power/suspend_stats/fail')
    
    try:
        if success_path.exists():
            success = int(success_path.read_text().strip())
    except (ValueError, PermissionError):
        pass
    
    try:
        if fail_path.exists():
            fail = int(fail_path.read_text().strip())
    except (ValueError, PermissionError):
        pass
    
    return success, fail


def get_psr_status() -> tuple[bool, str]:
    """
    Check Panel Self Refresh (PSR) status for AMD and Intel GPUs.
    
    Returns:
        Tuple of (psr_enabled, status_string)
    """
    # Try AMD GPU PSR status
    psr_paths = [
        '/sys/kernel/debug/dri/0/amdgpu_dm_dsc_disable',
        '/sys/kernel/debug/dri/0/eDP-1/psr_state',
        '/sys/kernel/debug/dri/1/eDP-1/psr_state',
    ]
    
    # Try to find PSR info from debugfs - AMD
    for card_num in range(4):
        psr_path = Path(f'/sys/kernel/debug/dri/{card_num}/eDP-1/psr_capability')
        if psr_path.exists():
            try:
                content = psr_path.read_text().strip()
                if content:
                    # Parse PSR capability info
                    enabled = 'enabled' in content.lower() or 'dc_version' in content.lower()
                    return enabled, content[:100]
            except (PermissionError, OSError):
                pass
    
    # Fixed: Try Intel PSR status paths
    for card_num in range(4):
        intel_psr_path = Path(f'/sys/kernel/debug/dri/{card_num}/i915_edp_psr_status')
        if intel_psr_path.exists():
            try:
                content = intel_psr_path.read_text().strip()
                if content:
                    enabled = 'enabled' in content.lower() or 'active' in content.lower()
                    # Extract just the key status line
                    for line in content.split('\n'):
                        if 'PSR' in line or 'Enabled' in line or 'Status' in line:
                            return enabled, line.strip()[:100]
                    return enabled, content[:100]
            except (PermissionError, OSError):
                pass
    
    # Try alternative method via dmesg parsing would happen in log analysis
    return False, ""


def get_last_suspend_resume_times(log_content: str) -> tuple[str, str]:
    """
    Extract the most recent suspend and resume timestamps from logs.
    
    Args:
        log_content: Log content to parse
    
    Returns:
        Tuple of (last_suspend_time, last_resume_time)
    """
    last_suspend = ""
    last_resume = ""
    
    # Patterns to match suspend/resume with timestamps
    suspend_patterns = [
        r'(\w+\s+\d+\s+\d+:\d+:\d+).*PM: suspend entry',
        r'(\w+\s+\d+\s+\d+:\d+:\d+).*PM: Entering mem sleep',
        r'\[(\w+\s+\w+\s+\d+\s+\d+:\d+:\d+\s+\d+)\].*PM: suspend entry',
    ]
    
    resume_patterns = [
        r'(\w+\s+\d+\s+\d+:\d+:\d+).*PM: suspend exit',
        r'(\w+\s+\d+\s+\d+:\d+:\d+).*PM: resume',
        r'\[(\w+\s+\w+\s+\d+\s+\d+:\d+:\d+\s+\d+)\].*PM: suspend exit',
    ]
    
    for line in log_content.split('\n'):
        for pattern in suspend_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                last_suspend = match.group(1)
                break
        
        for pattern in resume_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                last_resume = match.group(1)
                break
    
    return last_suspend, last_resume


def count_suspend_resume_events(log_content: str) -> tuple[int, int, int, int]:
    """
    Count suspend/resume events from log content.
    
    Args:
        log_content: The log file content to analyze
    
    Returns:
        Tuple of (suspend_count, resume_count, failed_suspend, failed_resume)
    """
    suspend_count = 0
    resume_count = 0
    failed_suspend = 0
    failed_resume = 0
    
    # Fixed: Only use patterns that indicate actual suspend ENTRY, not intermediate steps
    # Previous bug: "Syncing filesystems" and "Freezing user space" happen during
    # every suspend, causing 3-4x overcounting
    suspend_patterns = [
        r'PM: suspend entry',
        r'PM: Entering mem sleep',
    ]
    
    # Patterns for successful resume
    resume_patterns = [
        r'PM: suspend exit',
        r'PM: Finishing wakeup',
    ]
    
    # Patterns for failed suspend
    failed_suspend_patterns = [
        r'PM:.*suspend.*failed',
        r'suspend.*abort',
        r'Failed to suspend',
        r'Suspend failed',
    ]
    
    # Patterns for failed resume - ONLY system-level PM failures
    # NOT component errors like USB/GPU/WiFi resume hiccups which are
    # normal and don't indicate a failed system resume
    failed_resume_patterns = [
        r'^.*PM:.*resume from.*failed',
        r'^.*PM: Some devices failed to resume',
        r'^.*PM: noirq resume of devices failed',
        r'^.*PM: late resume of devices failed',
    ]
    
    for line in log_content.split('\n'):
        line_lower = line.lower()
        
        for pattern in suspend_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                suspend_count += 1
                break
        
        for pattern in resume_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                resume_count += 1
                break
        
        for pattern in failed_suspend_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                failed_suspend += 1
                break
        
        for pattern in failed_resume_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                failed_resume += 1
                break
    
    return suspend_count, resume_count, failed_suspend, failed_resume


def find_resume_errors(log_content: str) -> list[str]:
    """
    Find specific resume error messages in log content.
    
    Returns:
        List of error messages related to resume failures
    """
    errors = []
    
    error_patterns = [
        (r'PM:.*Device.*failed to resume', 'Device resume failure'),
        (r'ACPI.*resume.*failed', 'ACPI resume failure'),
        (r'USB.*resume.*failed', 'USB device resume failure'),
        (r'nvme.*resume.*failed', 'NVMe resume failure'),
        (r'amdgpu.*resume.*failed', 'GPU resume failure'),
        (r'i915.*resume.*failed', 'Intel GPU resume failure'),
        (r'iwlwifi.*resume.*failed', 'WiFi resume failure'),
    ]
    
    seen = set()
    
    for line in log_content.split('\n'):
        for pattern, desc in error_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                # Extract key info, deduplicate
                key = f"{desc}: {line[:100]}"
                if key not in seen:
                    seen.add(key)
                    errors.append(line.strip())
    
    return errors


def find_amd_pmc_issues(log_content: str) -> list[str]:
    """
    Find AMD PMC (Power Management Controller) issues in log content.
    
    AMD PMC issues can cause:
    - Failed suspend
    - Slow resume
    - High power consumption in s2idle
    
    Returns:
        List of AMD PMC related issues
    """
    issues = []
    seen = set()
    
    pmc_patterns = [
        r'amd_pmc.*timeout',
        r'amd_pmc.*failed',
        r'amd_pmc.*SMU.*error',
        r'amd_pmc.*response.*timeout',
        r'amd_pmc.*command.*failed',
        r'amd_pmc.*not.*responding',
    ]
    
    for line in log_content.split('\n'):
        for pattern in pmc_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                # Deduplicate similar messages
                key = line[:80]
                if key not in seen:
                    seen.add(key)
                    issues.append(line.strip())
    
    return issues


def find_sleep_blockers_in_logs(log_content: str) -> list[S2IdleBlocker]:
    """
    Detect sleep blockers from log patterns.
    
    Detects 20+ specific patterns including:
    - AMD/Intel GPU suspend failures
    - USB device blocking/waking
    - NVMe not entering low power
    - Goodix fingerprint reader (Framework)
    - Thunderbolt wake
    - Intel WiFi wake
    - Audio codec issues
    - S0ix substate failures
    - EC (Embedded Controller) blocking
    - ACPI wakeup events
    
    Returns:
        List of S2IdleBlocker with device, reason, and fix
    """
    blockers = []
    seen_devices = set()
    
    # Pattern: (regex, device, reason, fix)
    blocker_patterns = [
        # GPU issues
        (r'amdgpu.*suspend.*failed', 'AMD GPU', 'GPU failed to suspend',
         'Try: amdgpu.runpm=0 kernel parameter or update GPU firmware'),
        (r'amdgpu.*timeout.*waiting', 'AMD GPU', 'GPU timeout during power state change',
         'Try: echo high > /sys/class/drm/card0/device/power_dpm_force_performance_level'),
        (r'i915.*suspend.*failed', 'Intel GPU', 'GPU failed to suspend',
         'Try: i915.enable_dc=0 kernel parameter'),
        (r'i915.*timeout.*waiting', 'Intel GPU', 'GPU timeout during suspend',
         'Update Intel graphics driver or try i915.enable_psr=0'),
        
        # USB issues
        (r'usb.*suspend.*failed', 'USB device', 'USB device blocking suspend',
         'Identify device with lsusb, check /sys/bus/usb/devices/*/power/control'),
        (r'usb.*wakeup.*enabled', 'USB device', 'USB device configured as wakeup source',
         'Disable with: echo disabled > /sys/bus/usb/devices/X/power/wakeup'),
        (r'usb.*reset.*resume', 'USB device', 'USB device needs reset on resume',
         'May indicate USB device firmware issue or power management incompatibility'),
        
        # NVMe issues
        (r'nvme.*APST.*disabled', 'NVMe SSD', 'NVMe power saving disabled',
         'Enable with: nvme_core.default_ps_max_latency_us=5500'),
        (r'nvme.*not.*entering.*low.*power', 'NVMe SSD', 'NVMe not entering low power state',
         'Check drive firmware, try: nvme_core.default_ps_max_latency_us=0'),
        (r'nvme.*suspend.*failed', 'NVMe SSD', 'NVMe suspend failed',
         'Update NVMe firmware or try nvme_core.default_ps_max_latency_us=0'),
        
        # Framework-specific: Goodix fingerprint reader
        (r'goodix.*suspend.*failed', 'Goodix fingerprint', 'Fingerprint reader blocking suspend',
         'Disable fingerprint in BIOS or blacklist goodix module'),
        (r'goodix.*timeout', 'Goodix fingerprint', 'Fingerprint reader timeout',
         'echo "blacklist goodix" >> /etc/modprobe.d/blacklist.conf'),
        
        # Thunderbolt
        (r'thunderbolt.*wake', 'Thunderbolt', 'Thunderbolt causing wake',
         'Disable Thunderbolt wake in BIOS or: echo disabled > /sys/bus/pci/devices/*/power/wakeup'),
        (r'thunderbolt.*suspend.*failed', 'Thunderbolt', 'Thunderbolt suspend failed',
         'Disconnect Thunderbolt devices before suspend'),
        
        # Intel WiFi
        (r'iwlwifi.*wakeup', 'Intel WiFi', 'WiFi configured as wake source',
         'Disable with: iw phy phy0 wowlan disable'),
        (r'iwlwifi.*suspend.*failed', 'Intel WiFi', 'WiFi failed to suspend',
         'Try: iwlwifi.power_save=0 or update firmware'),
        
        # Audio codec
        (r'snd_hda.*suspend.*failed', 'Audio codec', 'Audio codec blocking suspend',
         'Try: snd_hda_intel.power_save=1 snd_hda_intel.power_save_controller=Y'),
        (r'sof.*suspend.*failed', 'SOF Audio', 'Sound Open Firmware suspend failed',
         'Update SOF firmware or try legacy HDA driver'),
        
        # EC (Embedded Controller)
        (r'ec.*block.*sleep', 'EC', 'Embedded Controller blocking sleep',
         'Likely firmware issue - check for BIOS update'),
        (r'ACPI.*EC.*timeout', 'EC', 'EC communication timeout',
         'Try: ec_intr=0 kernel parameter'),
        
        # ACPI wakeup
        (r'ACPI.*wakeup.*GLAN', 'LAN', 'LAN configured for wake-on-LAN',
         'Disable WoL: ethtool -s eth0 wol d'),
        (r'ACPI.*wakeup.*XHC', 'XHCI', 'USB controller wake enabled',
         'echo XHC > /proc/acpi/wakeup to toggle'),
        (r'ACPI.*wakeup.*RP0[0-9]', 'PCIe Root Port', 'PCIe device wake enabled',
         'Check /proc/acpi/wakeup and toggle relevant device'),
        
        # s0ix failures
        (r's0ix.*fail', 's0ix', 's0ix entry failed',
         'Check /sys/kernel/debug/pmc_core/substate_requirements (Intel) or amd_pmc (AMD)'),
        (r'SLPS0.*fail', 's0ix', 'SLPS0 (s0ix) check failed',
         'BIOS/firmware may not support s0ix properly'),
        
        # Power management general
        (r'PM:.*Device.*failed.*suspend', 'Unknown device', 'Device failed to suspend',
         'Check dmesg for specific device name'),
    ]
    
    for line in log_content.split('\n'):
        for pattern, device, reason, fix in blocker_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                # Deduplicate by device
                if device not in seen_devices:
                    seen_devices.add(device)
                    blockers.append(S2IdleBlocker(
                        device=device,
                        reason=reason,
                        fix=fix,
                        source=line.strip()[:100]
                    ))
    
    return blockers


def check_s2idle_status() -> tuple[bool, Optional[str]]:
    """
    Check if s2idle is working correctly.
    
    Returns:
        Tuple of (is_working, error_message)
    """
    # Check if s2idle is the current mode
    mode, available, _ = get_current_sleep_mode()
    
    if mode != SleepMode.S2IDLE:
        if 's2idle' in available:
            return False, f"s2idle available but not active. Current mode: {mode.value}"
        else:
            return False, "s2idle not available on this system"
    
    # Check for AMD-specific s2idle requirements
    try:
        result = subprocess.run(
            ['cat', '/sys/power/pm_debug_messages'],
            capture_output=True,
            text=True,
            timeout=2
        )
        # If we can read this, debug messages are available
    except Exception:
        pass
    
    return True, None


def check_sleep_status(log_content: Optional[str] = None) -> SleepStatus:
    """
    Comprehensive sleep status check.
    
    NEW FUNCTION - Reports:
    - Sleep mode (s2idle vs deep)
    - s2idle status
    - ACPI states
    - Suspend/resume counts
    - Inhibitors
    - Resume errors
    - AMD PMC issues
    - Kernel suspend stats
    - PSR status
    - Last suspend/resume times
    
    Args:
        log_content: Optional log content to analyze for suspend/resume events
    
    Returns:
        SleepStatus object with all findings
    """
    status = SleepStatus()
    
    # Get current sleep configuration (with raw evidence)
    status.current_mode, status.available_modes, status.mem_sleep_raw = get_current_sleep_mode()
    status.acpi_states, status.state_raw = get_acpi_sleep_states()
    
    # Get kernel suspend stats
    status.kernel_suspend_success, status.kernel_suspend_fail = get_kernel_suspend_stats()
    
    # Get PSR status
    status.psr_enabled, status.psr_status = get_psr_status()
    
    # Check s2idle status
    s2idle_ok, s2idle_error = check_s2idle_status()
    status.s2idle_enabled = s2idle_ok
    if not s2idle_ok and s2idle_error:
        status.issues.append(s2idle_error)
        status.is_healthy = False
    
    # Inhibitors removed - they provide no useful info
    # Only log-based blocker detection matters
    
    # If no log content provided, read kernel logs for this boot
    # IMPORTANT: Use journalctl -k -b first, NOT dmesg -T.
    # dmesg -T is unreliable after suspend/resume: the monotonic clock
    # pauses during suspend, so dmesg -T recalculates wall-clock times
    # incorrectly (often producing future dates).
    if not log_content:
        try:
            result = subprocess.run(
                ['journalctl', '-k', '--no-pager', '-b'],
                capture_output=True,
                text=True,
                timeout=15
            )
            if result.returncode == 0 and result.stdout.strip():
                log_content = result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        # Fallback to dmesg only if journalctl unavailable
        if not log_content:
            try:
                result = subprocess.run(
                    ['sudo', 'dmesg', '-T'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0 and result.stdout.strip():
                    log_content = result.stdout
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
    
    # Analyze log content if available - do this BEFORE PMC blocker check
    # so we know how many suspend attempts there were
    if log_content:
        # Count events
        (status.suspend_count, status.resume_count,
         status.failed_suspend_count, status.failed_resume_count) = \
            count_suspend_resume_events(log_content)
        
        # Get last suspend/resume timestamps
        status.last_suspend_time, status.last_resume_time = get_last_suspend_resume_times(log_content)
        
        # Find specific errors
        status.resume_errors = find_resume_errors(log_content)
        status.amd_pmc_issues = find_amd_pmc_issues(log_content)
    
    # Sleep blockers - ONLY from log analysis, no sysfs queries that cause false positives
    
    # Continue analyzing log content if available
    if log_content:
        
        # Find sleep blockers in logs
        log_blockers = find_sleep_blockers_in_logs(log_content)
        status.blockers.extend(log_blockers)
        
        # Check PSR status from logs if not found via sysfs
        if not status.psr_status:
            psr_match = re.search(r'PSR.*(?:DC|sink).*version.*(\d+)', log_content, re.IGNORECASE)
            if psr_match:
                status.psr_enabled = True
                # Look for full PSR status line
                for line in log_content.split('\n'):
                    if 'PSR' in line and ('DC' in line or 'sink' in line):
                        status.psr_status = line.strip()[-80:]
                        break
        
        # Detect PSR-related issues (screen blinking/flickering on resume)
        psr_issue_patterns = [
            (r'PSR.*exit.*error', 'PSR exit error detected'),
            (r'PSR.*timeout', 'PSR timeout'),
            (r'amdgpu.*PSR.*fail', 'AMD GPU PSR failure'),
            (r'i915.*PSR.*error', 'Intel PSR error'),
            (r'drm.*underrun.*resume', 'Display underrun on resume'),
            (r'flickering.*resume|resume.*flickering', 'Screen flickering on resume'),
        ]
        for pattern, description in psr_issue_patterns:
            if re.search(pattern, log_content, re.IGNORECASE):
                if description not in status.psr_issues:
                    status.psr_issues.append(description)
        
        # Check health based on findings
        if status.failed_suspend_count > 0:
            status.is_healthy = False
            status.issues.append(f"{status.failed_suspend_count} failed suspend(s)")
        
        if status.failed_resume_count > 0:
            status.is_healthy = False
            status.issues.append(f"{status.failed_resume_count} failed resume(s)")
        
        if status.resume_errors:
            status.is_healthy = False
            status.issues.append(f"{len(status.resume_errors)} resume error(s)")
        
        if status.amd_pmc_issues:
            status.is_healthy = False
            status.issues.append(f"{len(status.amd_pmc_issues)} AMD PMC issue(s)")
    
    # Mark unhealthy if we have blockers
    if status.blockers:
        status.is_healthy = False
        status.issues.append(f"{len(status.blockers)} sleep blocker(s) detected")
    
    # Framework workaround services
    status.disable_wakeup_service = Path('/etc/systemd/system/disable-wakeup.service').exists()
    
    return status


def format_sleep_status_report(status: SleepStatus) -> list[str]:
    """
    Format sleep status for the diagnostic report.
    
    Returns:
        List of report lines
    """
    lines = []
    
    lines.append("Sleep/Suspend Status:")
    
    # Current mode
    mode_str = status.current_mode.value
    if status.current_mode == SleepMode.S2IDLE:
        lines.append(f"  Mode: {mode_str} (modern standby) ✅")
    elif status.current_mode == SleepMode.DEEP:
        lines.append(f"  Mode: {mode_str} (S3 sleep)")
    else:
        lines.append(f"  Mode: {mode_str}")
    
    # Evidence - raw file contents
    if status.mem_sleep_raw:
        lines.append(f"  Evidence: /sys/power/mem_sleep = {status.mem_sleep_raw}")
    if status.state_raw:
        lines.append(f"  Evidence: /sys/power/state = {status.state_raw}")
    
    # Available modes
    if status.available_modes:
        lines.append(f"  Available modes: {', '.join(status.available_modes)}")
    
    # ACPI states
    if status.acpi_states:
        lines.append(f"  ACPI states: {', '.join(status.acpi_states)}")
    
    # Kernel suspend stats (from /sys/power/suspend_stats/)
    if status.kernel_suspend_success > 0 or status.kernel_suspend_fail > 0:
        lines.append(f"  Kernel suspend stats (since boot): {status.kernel_suspend_success} successful, {status.kernel_suspend_fail} failed")
    
    # Log suspend/resume counts with timestamps
    if status.suspend_count > 0 or status.resume_count > 0:
        lines.append(f"  Log suspend cycles (since boot): {status.suspend_count} suspend, {status.resume_count} resume")
        if status.last_suspend_time:
            lines.append(f"    Last suspend: {status.last_suspend_time}")
        if status.last_resume_time:
            lines.append(f"    Last resume: {status.last_resume_time}")
        if status.failed_suspend_count > 0:
            lines.append(f"  ❌ Failed suspends: {status.failed_suspend_count}")
        if status.failed_resume_count > 0:
            lines.append(f"  ❌ Failed resumes: {status.failed_resume_count}")
    
    # PSR (Panel Self Refresh) status
    if status.psr_status or status.psr_issues:
        lines.append(f"  PSR (Panel Self Refresh): {status.psr_status if status.psr_status else 'Enabled'}")
        # Only show fix advice if actual PSR issues were detected
        if status.psr_issues:
            for issue in status.psr_issues:
                lines.append(f"    ⚠️  {issue}")
            lines.append("    Fix: Try kernel parameter amdgpu.dcdebugmask=0x10 (AMD) or i915.enable_psr=0 (Intel)")
    
    # AMD PMC issues (from logs)
    if status.amd_pmc_issues:
        lines.append(f"  ⚠️  AMD PMC issues: {len(status.amd_pmc_issues)}")
    
    # Sleep blockers (NEW)
    if status.blockers:
        lines.append(f"  ⚠️  Sleep blockers detected: {len(status.blockers)}")
        for blocker in status.blockers:
            lines.append(f"    Device: {blocker.device}")
            lines.append(f"      Reason: {blocker.reason}")
            lines.append(f"      Fix: {blocker.fix}")
    
    # Framework workaround services
    if status.disable_wakeup_service:
        lines.append("  disable-wakeup.service: ✅ installed")
    
    return lines
