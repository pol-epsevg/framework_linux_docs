"""Log summary module - extracts system activity from raw logs.

Scans combined journalctl + dmesg content and produces a structured
summary of system lifecycle events, suspend/resume cycles, and
critical error detection. Raw logs are always preserved unmodified.
"""
import re
import os
from dataclasses import dataclass, field
from typing import List, Optional
from pathlib import Path

# ── Critical event patterns ──────────────────────────────────────────
# Checked against combined log text. Both journalctl and dmesg formats.
# No kernel: prefix requirement — works with either source.
_CRITICAL_CHECKS = [
    # (pattern, label, confidence)
    # 'high' = exact kernel string, no known false positives
    # 'medium' = correct subsystem, format may vary by kernel version
    # 'low' = best-effort, may have false positives or miss variants
    (r'Kernel panic\s*-\s*not syncing', 'Kernel panic', 'high'),
    (r'Oops:\s', 'Kernel oops', 'high'),
    (r'(?:amdgpu.*\*ERROR\*.*timeout|amdgpu.*GPU reset|amdgpu.*GPU recovery'
     r'|i915.*GPU HANG|i915.*wedged|i915.*Resetting chip'
     r'|\bxe\b.*\*ERROR\*.*timeout'
     r'|NVRM.*Xid|nvidia-modeset.*ERROR|NVRM.*GPU has fallen off the bus)',
     'GPU error', 'medium'),
    (r'nvme\s+nvme\d+:\s+I/O.*(?:error|timeout)|I/O error.*dev nvme', 'NVMe I/O error', 'high'),
    (r'EXT4-fs error|BTRFS.*error.*device', 'Filesystem error', 'high'),
    (r'iwlwifi.*(?:firmware error|Microcode SW error)'
     r'|mt792[12]e?.*(?:firmware error|timeout)',
     'WiFi firmware crash', 'medium'),
    (r'Out of memory:\s+Kill', 'OOM kill', 'high'),
    (r'CPU\d+.*temperature above threshold'
     r'|k10temp.*critical'
     r'|thermal_zone\d+.*critical',
     'Thermal throttling', 'medium'),
    # Two formats: "PM: Device X failed to suspend/resume" (standard)
    # and "usb X-X: PM: failed to resume async" (xHCI/USB)
    (r'PM:\s+Device\s+\S+\s+failed to (?:suspend|resume)'
     r'|PM:\s+failed to (?:suspend|resume)',
     'Suspend device failure', 'high'),
    (r'amd_pmc.*(?:timeout|failed)', 'AMD PMC error', 'medium'),
    (r'cros_ec.*timeout', 'EC timeout', 'low'),
    (r'usb\s+\S+:\s+Failed to suspend', 'USB suspend failure', 'high'),
    (r'xhci_hcd.*HC died', 'xHCI controller died', 'high'),
    (r'mce:\s.*Hardware Error'
     r'|Machine check events logged'
     r'|MCE:\s(?!In-kernel MCE decoding)',
     'Machine check exception', 'medium'),
    (r'EDAC\s+(?:MC|sbridge|skx|ie31200|amd64)\d*:\s.*(?:CE|UE|[Ee]rror)',
     'Memory hardware error', 'medium'),
    (r'fwupd\[\d+\].*(?:failed to (?:update|flash|write|install) firmware'
     r'|Update Error:)'
     r'|fwupdmgr.*failed to update',
     'fwupd update failure', 'high'),
    (r'ACPI Error:'
     r'|ACPI Exception:'
     r'|ACPI BIOS Error',
     'ACPI error', 'low'),
]

# ── Lifecycle event patterns ─────────────────────────────────────────
_LIFECYCLE = [
    (r'PM:\s+suspend entry', 'SUSPEND'),
    (r'PM:\s+suspend exit', 'RESUME'),
    (r'Linux version \S+', 'BOOT'),
    (r'systemd\[1\]:\s+Shutting down', 'SHUTDOWN'),
    (r'systemd\[1\]:\s+Started.*plymouth-reboot', 'REBOOT'),
    (r'systemd-shutdown\[1\]:\s+Rebooting', 'REBOOT'),
    (r'systemd\[1\]:\s+([\w@.-]+\.service):\s+Failed with result', 'SVC_FAIL'),
    (r'systemd\[1\]:\s+Startup finished in.*=\s*(.+)', 'BOOT_DONE'),
]

# ── Per-cycle error patterns ─────────────────────────────────────────
# Matched between suspend entry and resume exit to count cycle errors.
_CYCLE_ERROR = re.compile(
    r'failed.*(?:resume|suspend)'
    r'|HC died'
    r'|\*ERROR\*'
    r'|Call Trace'
    r'|GPU (?:HANG|reset|recovery)'
    r'|firmware error'
    r'|I/O error',
    re.IGNORECASE
)

# Path to the xHCI resume workaround script
_XHCI_FIX_PATH = Path('/usr/local/bin/xhci-resume-fix.sh')
_XHCI_FIX_SERVICE = 'xhci-resume-fix.service'


@dataclass
class SuspendCycle:
    """One suspend/resume cycle."""
    cycle_num: int
    suspend_time: str = ''
    resume_time: str = ''
    errors: int = 0
    error_lines: list = field(default_factory=list)
    resumed: bool = False


@dataclass
class SystemActivity:
    """Structured summary of system events from logs."""
    boot_count: int = 0
    shutdown_count: int = 0
    reboot_count: int = 0
    boot_times: list = field(default_factory=list)
    service_failures: dict = field(default_factory=dict)
    suspend_cycles: list = field(default_factory=list)
    critical_found: list = field(default_factory=list)
    critical_clear: list = field(default_factory=list)
    crashes: int = 0
    total_suspend_since_boot: int = 0  # from /sys/power/suspend_stats/success
    xhci_hc_died: bool = False
    xhci_fix_installed: bool = False
    xhci_fix_service_enabled: bool = False


def _extract_timestamp(line: str) -> str:
    """Pull timestamp from journalctl or dmesg format."""
    # journalctl: "Feb 18 16:58:46 hostname ..."
    m = re.match(r'^(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})', line)
    if m:
        return m.group(1)
    # dmesg -T: "[Sun Feb 22 16:55:27 2026] ..."
    m = re.match(r'^\[(\w{3}\s+\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4})\]', line)
    if m:
        return m.group(1)
    return ''


def _check_xhci_workaround() -> tuple:
    """Check if the xHCI resume fix workaround is installed.
    
    Returns (script_exists: bool, service_enabled: bool)
    """
    script_exists = _XHCI_FIX_PATH.is_file()
    
    service_enabled = False
    if script_exists:
        try:
            import subprocess
            result = subprocess.run(
                ['systemctl', 'is-enabled', _XHCI_FIX_SERVICE],
                capture_output=True, text=True, timeout=5
            )
            service_enabled = result.stdout.strip() == 'enabled'
        except Exception:
            pass
    
    return script_exists, service_enabled


def extract_activity(log_text: str) -> SystemActivity:
    """Scan log text and extract structured system activity.
    
    Does NOT modify or filter the log text. Read-only scan.
    """
    activity = SystemActivity()
    lines = log_text.splitlines()

    # ── Critical checks ──────────────────────────────────────────
    for pattern, label, confidence in _CRITICAL_CHECKS:
        regex = re.compile(pattern)
        found = False
        for line in lines:
            if regex.search(line):
                found = True
                if label == 'xHCI controller died':
                    activity.xhci_hc_died = True
                break
        if found:
            activity.critical_found.append((label, confidence))
        else:
            activity.critical_clear.append((label, confidence))

    # ── Check xHCI workaround if xHCI issues detected ────────────
    found_labels = [label for label, _ in activity.critical_found]
    if activity.xhci_hc_died or 'Suspend device failure' in found_labels:
        script_ok, service_ok = _check_xhci_workaround()
        activity.xhci_fix_installed = script_ok
        activity.xhci_fix_service_enabled = service_ok

    # ── Lifecycle events (ordered) ───────────────────────────────
    events = []  # (line_num, timestamp, event_type, detail)
    for i, line in enumerate(lines):
        for pattern, event_type in _LIFECYCLE:
            m = re.search(pattern, line)
            if m:
                ts = _extract_timestamp(line)
                detail = ''
                if event_type == 'SVC_FAIL':
                    detail = m.group(1)
                elif event_type == 'BOOT_DONE':
                    detail = m.group(1).strip()
                elif event_type == 'BOOT':
                    km = re.search(r'Linux version (\S+)', line)
                    if km:
                        detail = km.group(1)
                events.append((i, ts, event_type, detail))
                break

    # ── Count lifecycle events ───────────────────────────────────
    prev_event = None  # track what preceded each boot
    for _, ts, etype, detail in events:
        if etype == 'BOOT':
            activity.boot_count += 1
            # Flush any pending boot that never got a BOOT_DONE
            pending = getattr(activity, '_pending_boot', None)
            if pending:
                boot_ts, boot_type = pending
                activity.boot_times.append((boot_ts, None, boot_type))
            # Determine boot type based on what preceded it
            if prev_event == 'REBOOT':
                boot_type = 'Restart'
            elif prev_event == 'SHUTDOWN':
                boot_type = 'Power on'
            elif prev_event == 'BOOT':
                boot_type = 'Crash recovery'
            else:
                boot_type = 'Power on'  # first boot in window
            # Stash for pairing with BOOT_DONE
            activity._pending_boot = (ts, boot_type)
        elif etype == 'SHUTDOWN':
            if prev_event != 'REBOOT':  # don't count shutdown that's part of a reboot
                activity.shutdown_count += 1
        elif etype == 'REBOOT':
            if prev_event != 'REBOOT':  # dedup: plymouth + systemd-shutdown both fire
                activity.reboot_count += 1
        elif etype == 'BOOT_DONE':
            pending = getattr(activity, '_pending_boot', None)
            if pending:
                boot_ts, boot_type = pending
                activity.boot_times.append((boot_ts, detail, boot_type))
                activity._pending_boot = None
            else:
                activity.boot_times.append((ts, detail, 'Power on'))
        elif etype == 'SVC_FAIL':
            activity.service_failures[detail] = activity.service_failures.get(detail, 0) + 1
        if etype in ('BOOT', 'SHUTDOWN', 'REBOOT'):
            # REBOOT takes priority: systemd fires both REBOOT and SHUTDOWN
            # on reboot, so don't let SHUTDOWN overwrite a preceding REBOOT
            if etype == 'SHUTDOWN' and prev_event == 'REBOOT':
                pass  # keep REBOOT as prev_event
            else:
                prev_event = etype

    # Flush last pending boot if no BOOT_DONE followed
    pending = getattr(activity, '_pending_boot', None)
    if pending:
        boot_ts, boot_type = pending
        activity.boot_times.append((boot_ts, None, boot_type))
        activity._pending_boot = None

    # ── Crash detection ──────────────────────────────────────────
    # Boot without preceding shutdown or reboot = crash/power loss
    prev_was_down = True  # first boot is normal
    for _, _, etype, _ in events:
        if etype in ('SHUTDOWN', 'REBOOT'):
            prev_was_down = True
        elif etype == 'BOOT':
            if not prev_was_down:
                activity.crashes += 1
            prev_was_down = False

    # ── Suspend/resume scoreboard ────────────────────────────────
    # Pair suspend entries with resume exits, count errors between them
    suspend_indices = []
    resume_indices = []
    for idx, (line_num, ts, etype, _) in enumerate(events):
        if etype == 'SUSPEND':
            suspend_indices.append((line_num, ts))
        elif etype == 'RESUME':
            resume_indices.append((line_num, ts))

    cycle_num = 0
    ri = 0  # resume index pointer
    for s_line, s_ts in suspend_indices:
        cycle_num += 1
        cycle = SuspendCycle(cycle_num=cycle_num, suspend_time=s_ts)

        # Find the next resume after this suspend
        r_line = None
        r_ts = ''
        while ri < len(resume_indices):
            if resume_indices[ri][0] > s_line:
                r_line = resume_indices[ri][0]
                r_ts = resume_indices[ri][1]
                ri += 1
                break
            ri += 1

        if r_line is not None:
            cycle.resumed = True
            cycle.resume_time = r_ts
            # Capture errors between suspend and resume
            for line in lines[s_line:r_line + 1]:
                if _CYCLE_ERROR.search(line):
                    cycle.errors += 1
                    # Strip timestamp, keep the message
                    stripped = re.sub(
                        r'^(?:\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\S+\s+|'
                        r'\[.*?\]\s*)', '', line).strip()
                    if stripped:
                        cycle.error_lines.append(stripped)
        else:
            cycle.resumed = False

        activity.suspend_cycles.append(cycle)

    # ── Kernel suspend total (authoritative, not limited by log window) ──
    try:
        success_path = Path('/sys/power/suspend_stats/success')
        if success_path.exists():
            activity.total_suspend_since_boot = int(success_path.read_text().strip())
    except (ValueError, OSError):
        pass

    return activity


def format_activity(activity: SystemActivity, time_range: str = "") -> list:
    """Format SystemActivity into report lines."""
    sections = []
    sections.append('=' * 60)
    sections.append('SYSTEM ACTIVITY')
    sections.append('=' * 60)
    if time_range:
        sections.append(f'  Log period: {time_range}')
    sections.append('')

    # ── Event summary ────────────────────────────────────────
    event_parts = []
    if activity.boot_count > 0:
        event_parts.append(f'{activity.boot_count} boot(s)')
    if activity.reboot_count > 0:
        event_parts.append(f'{activity.reboot_count} reboot(s)')
    if activity.shutdown_count > 0:
        event_parts.append(f'{activity.shutdown_count} shutdown(s)')
    if activity.suspend_cycles:
        in_window = len(activity.suspend_cycles)
        kernel_total = activity.total_suspend_since_boot
        if kernel_total > 0:
            event_parts.append(f'{in_window} sleep/wake cycle(s) in log window ({kernel_total} total since boot)')
        else:
            event_parts.append(f'{in_window} sleep/wake cycle(s)')

    if event_parts:
        sections.append(f'  Events: {", ".join(event_parts)}')
    else:
        sections.append('  Events: none detected')

    # Boot times (from log window — shows what each boot was, when, and how long)
    for ts, duration, boot_type in activity.boot_times:
        if ts and duration:
            sections.append(f'  {boot_type} at {ts} — startup took {duration}')
        elif ts:
            sections.append(f'  {boot_type} at {ts} — no startup duration (interrupted)')
        elif duration:
            sections.append(f'  {boot_type} — startup took {duration}')
        else:
            sections.append(f'  {boot_type}')

    # Crashes
    if activity.crashes > 0:
        sections.append(f'  ⚠️  Crashes detected: {activity.crashes} '
                        f'(boot without preceding shutdown)')

    # Service failures
    if activity.service_failures:
        sections.append('')
        sections.append('  Service failures:')
        for svc, count in sorted(activity.service_failures.items(),
                                  key=lambda x: -x[1]):
            sections.append(f'    {svc} ({count}x)')

    # ── Suspend/resume detail ────────────────────────────────────
    if activity.suspend_cycles:
        sections.append('')
        total = len(activity.suspend_cycles)
        clean = sum(1 for c in activity.suspend_cycles
                    if c.resumed and c.errors == 0)
        with_errors = sum(1 for c in activity.suspend_cycles
                         if c.resumed and c.errors > 0)
        didnt_resume = sum(1 for c in activity.suspend_cycles
                          if not c.resumed)

        kernel_total = activity.total_suspend_since_boot
        if kernel_total > 0 and kernel_total != total:
            sections.append(f'  Suspend/Resume: {total} of {kernel_total} sleep/wake cycle(s) in log window')
        else:
            sections.append(f'  Suspend/Resume: {total} sleep/wake cycle(s) detected')
        sections.append('')
        
        # Per-cycle detail
        for c in activity.suspend_cycles:
            s_time = c.suspend_time if c.suspend_time else 'unknown time'
            if c.resumed and c.errors == 0:
                r_time = c.resume_time if c.resume_time else 'unknown time'
                sections.append(
                    f'    Cycle {c.cycle_num}: Slept at {s_time} → '
                    f'Woke at {r_time} — ✅ clean (no errors)')
            elif c.resumed and c.errors > 0:
                r_time = c.resume_time if c.resume_time else 'unknown time'
                sections.append(
                    f'    Cycle {c.cycle_num}: Slept at {s_time} → '
                    f'Woke at {r_time} — ⚠️  {c.errors} error(s) during wake:')
                for err in c.error_lines:
                    sections.append(f'      → {err}')
            else:
                sections.append(
                    f'    Cycle {c.cycle_num}: Slept at {s_time} → '
                    f'❌ Never woke up (no resume found in logs)')
        
        # Summary
        sections.append('')
        summary_parts = []
        if clean:
            summary_parts.append(f'{clean} clean')
        if with_errors:
            summary_parts.append(f'{with_errors} woke with errors')
        if didnt_resume:
            summary_parts.append(f'{didnt_resume} failed to wake')
        sections.append(f'    Summary: {", ".join(summary_parts)}')

        # xHCI workaround status (only when relevant)
        if activity.xhci_hc_died:
            sections.append('')
            if activity.xhci_fix_installed and activity.xhci_fix_service_enabled:
                sections.append('    ℹ️  xHCI resume workaround: ✅ installed and enabled')
            elif activity.xhci_fix_installed:
                sections.append('    ℹ️  xHCI resume workaround: ⚠️  script exists but service not enabled')
                sections.append(f'       Run: sudo systemctl enable {_XHCI_FIX_SERVICE}')
            else:
                sections.append('    ℹ️  xHCI resume workaround: ❌ not installed')
                sections.append('       xHCI controller dies on every resume (HC died)')
                sections.append('       Workaround: install xhci-resume-fix.sh + systemd service')
                sections.append('       (rebinds xHCI controller after resume, upstream kernel fix pending)')

    elif activity.total_suspend_since_boot > 0:
        # Kernel reports suspend cycles but none fell in the log window
        sections.append('')
        sections.append(f'  Suspend/Resume: {activity.total_suspend_since_boot} sleep/wake cycle(s) since boot (none in log window)')

    # ── Critical checks ──────────────────────────────────────────
    sections.append('')
    if activity.critical_clear:
        clear_labels = [label for label, _ in activity.critical_clear]
        sections.append('  ✅ Clear: ' + ', '.join(clear_labels))
    if activity.critical_found:
        sections.append('  Detected:')
        for label, conf in activity.critical_found:
            sections.append(f'    ❌ {label} [{conf} confidence]')
    else:
        sections.append('  No critical errors detected.')
    sections.append('  ⚠️  Pattern-matched only — not a comprehensive scan. Review raw logs for issues not listed above.')

    return sections
