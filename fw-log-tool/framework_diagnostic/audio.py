"""
Audio diagnostics.

Detects:
- Sound server (PipeWire vs PulseAudio)
- Session manager (WirePlumber vs pipewire-media-session)
- Default sink/source
- Mute status at sound server level and ALSA level
- Volume levels

All checks use deterministic command output, no interpretation.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from .utils import run_command
from .dependencies import get_distro_id


def _run_user_command(cmd: list[str]) -> tuple[int, str, str]:
    """Run a command as the real user, not root.
    
    Audio commands (pactl, amixer) need the user's PulseAudio/PipeWire
    session. When running under sudo, we use 'sudo -u $SUDO_USER' to
    drop back to the real user, preserving XDG_RUNTIME_DIR so pactl
    can find the socket.
    
    When running under su -c (Debian), SUDO_USER is not set. Fall back
    to loginctl to find the graphical session user.
    """
    login_user = os.environ.get('SUDO_USER', '')
    login_uid = os.environ.get('SUDO_UID', '')
    
    if login_user and login_user != 'root' and os.geteuid() == 0:
        # sudo case — existing path
        runtime_dir = f'/run/user/{login_uid}' if login_uid else f'/run/user/1000'
        env_cmd = [
            'sudo', '-u', login_user,
            f'XDG_RUNTIME_DIR={runtime_dir}',
        ] + cmd
        return run_command(env_cmd)
    
    if not login_user and os.geteuid() == 0:
        # su -c case — SUDO_USER not set, find graphical session user via loginctl
        user, uid = _find_graphical_session_user()
        if user:
            runtime_dir = f'/run/user/{uid}'
            shell_cmd = f'XDG_RUNTIME_DIR={runtime_dir} ' + ' '.join(cmd)
            return run_command(['su', user, '-c', shell_cmd])
    
    return run_command(cmd)


def _find_graphical_session_user() -> tuple[str, str]:
    """Find the user and UID of the graphical session via loginctl.
    
    Returns (username, uid) or ('', '') if not found.
    """
    if hasattr(_find_graphical_session_user, '_cached'):
        return _find_graphical_session_user._cached
    
    result = ('', '')
    rc, stdout, _ = run_command(['loginctl', 'list-sessions', '--no-legend'])
    if rc == 0:
        for line in stdout.strip().split('\n'):
            parts = line.split()
            if len(parts) < 3:
                continue
            session_id = parts[0]
            rc2, stdout2, _ = run_command([
                'loginctl', 'show-session', session_id,
                '-p', 'Type', '--value'
            ])
            if rc2 == 0 and stdout2.strip() in ('wayland', 'x11'):
                # Found graphical session — get user and uid
                rc3, stdout3, _ = run_command([
                    'loginctl', 'show-session', session_id,
                    '-p', 'Name', '--value'
                ])
                rc4, stdout4, _ = run_command([
                    'loginctl', 'show-session', session_id,
                    '-p', 'User', '--value'
                ])
                if rc3 == 0 and rc4 == 0:
                    result = (stdout3.strip(), stdout4.strip())
                break
    
    _find_graphical_session_user._cached = result
    return result


@dataclass
class AudioDevice:
    """An audio sink or source."""
    name: str = ""          # internal name, e.g. "alsa_output.pci-0000_c1_00.1..."
    description: str = ""   # human name, e.g. "Built-in Audio Analog Stereo"
    muted: Optional[bool] = None
    volume_pct: Optional[int] = None


@dataclass
class AudioInfo:
    """Complete audio diagnostic results."""
    # Sound server
    server_name: str = ""       # "PipeWire", "PulseAudio", or ""
    server_version: str = ""
    pactl_available: bool = False

    # Session manager (PipeWire only)
    session_manager: str = ""   # "WirePlumber", "pipewire-media-session", ""

    # Default devices
    default_sink: Optional[AudioDevice] = None
    default_source: Optional[AudioDevice] = None

    # ALSA level
    amixer_available: bool = False
    alsa_master_muted: Optional[bool] = None
    alsa_master_volume: Optional[int] = None
    alsa_capture_muted: Optional[bool] = None

    warnings: list[str] = field(default_factory=list)


def _parse_volume(text: str) -> Optional[int]:
    """Extract first percentage from pactl volume output.

    e.g. "Volume: front-left: 42330 /  65% / -11.27 dB, ..."  -> 65
    """
    m = re.search(r'(\d+)%', text)
    if m:
        return int(m.group(1))
    return None


def _get_sink_description(sink_name: str) -> str:
    """Get human-readable description for a sink via pactl list sinks."""
    rc, stdout, _ = _run_user_command(['pactl', 'list', 'sinks', 'short'])
    if rc != 0:
        return ""
    # short format: "index\tname\tmodule\tsample_spec\tstate"
    # Try the long form for description
    rc, stdout, _ = _run_user_command(['pactl', 'list', 'sinks'])
    if rc != 0:
        return ""

    # Walk through looking for our sink, then grab Description
    in_target = False
    for line in stdout.split('\n'):
        stripped = line.strip()
        if stripped.startswith('Name:') and sink_name in stripped:
            in_target = True
        elif stripped.startswith('Name:'):
            in_target = False
        elif in_target and stripped.startswith('Description:'):
            return stripped.split(':', 1)[1].strip()
    return ""


def _get_source_description(source_name: str) -> str:
    """Get human-readable description for a source via pactl list sources."""
    rc, stdout, _ = _run_user_command(['pactl', 'list', 'sources'])
    if rc != 0:
        return ""

    in_target = False
    for line in stdout.split('\n'):
        stripped = line.strip()
        if stripped.startswith('Name:') and source_name in stripped:
            in_target = True
        elif stripped.startswith('Name:'):
            in_target = False
        elif in_target and stripped.startswith('Description:'):
            return stripped.split(':', 1)[1].strip()
    return ""


def detect_audio() -> AudioInfo:
    """Detect audio configuration and status."""
    if get_distro_id() == 'nixos':
        return AudioInfo()

    info = AudioInfo()

    # Check pactl available
    rc, stdout, _ = _run_user_command(['pactl', 'info'])
    if rc != 0:
        return info
    info.pactl_available = True

    # Parse server info
    for line in stdout.split('\n'):
        if line.startswith('Server Name:'):
            info.server_name = line.split(':', 1)[1].strip()
        elif line.startswith('Server Version:') or line.startswith('server.version'):
            info.server_version = line.split(':', 1)[1].strip()

    # Normalize server name
    if 'pipewire' in info.server_name.lower() or 'PipeWire' in info.server_name:
        raw = info.server_name
        info.server_name = 'PipeWire'
        # PipeWire server name often contains version
        ver_match = re.search(r'(\d+\.\d+[\.\d]*)', raw)
        if ver_match and not info.server_version:
            info.server_version = ver_match.group(1)
    elif 'pulse' in info.server_name.lower():
        info.server_name = 'PulseAudio'

    # Session manager (PipeWire only)
    if info.server_name == 'PipeWire':
        rc, stdout, _ = _run_user_command(['systemctl', '--user', 'is-active', 'wireplumber.service'])
        if rc == 0 and stdout.strip() == 'active':
            info.session_manager = 'WirePlumber'
        else:
            rc, stdout, _ = _run_user_command(['systemctl', '--user', 'is-active',
                                         'pipewire-media-session.service'])
            if rc == 0 and stdout.strip() == 'active':
                info.session_manager = 'pipewire-media-session'

    # Default sink
    rc, stdout, _ = _run_user_command(['pactl', 'get-default-sink'])
    if rc == 0 and stdout.strip():
        sink = AudioDevice(name=stdout.strip())
        sink.description = _get_sink_description(sink.name)

        # Sink mute
        rc2, out2, _ = _run_user_command(['pactl', 'get-sink-mute', '@DEFAULT_SINK@'])
        if rc2 == 0:
            sink.muted = 'yes' in out2.lower()

        # Sink volume
        rc2, out2, _ = _run_user_command(['pactl', 'get-sink-volume', '@DEFAULT_SINK@'])
        if rc2 == 0:
            sink.volume_pct = _parse_volume(out2)

        info.default_sink = sink

    # Default source
    rc, stdout, _ = _run_user_command(['pactl', 'get-default-source'])
    if rc == 0 and stdout.strip():
        source = AudioDevice(name=stdout.strip())
        source.description = _get_source_description(source.name)

        # Source mute
        rc2, out2, _ = _run_user_command(['pactl', 'get-source-mute', '@DEFAULT_SOURCE@'])
        if rc2 == 0:
            source.muted = 'yes' in out2.lower()

        # Source volume
        rc2, out2, _ = _run_user_command(['pactl', 'get-source-volume', '@DEFAULT_SOURCE@'])
        if rc2 == 0:
            source.volume_pct = _parse_volume(out2)

        info.default_source = source

    # ALSA level
    rc, stdout, _ = _run_user_command(['amixer', 'get', 'Master'])
    if rc == 0:
        info.amixer_available = True
        # Parse: "Mono: Playback 42330 [65%] [on]" or [off]
        if '[off]' in stdout:
            info.alsa_master_muted = True
        elif '[on]' in stdout:
            info.alsa_master_muted = False
        vol = _parse_volume(stdout)
        if vol is not None:
            info.alsa_master_volume = vol
    else:
        # amixer might exist but Master might not — try 'amixer scontrols'
        rc2, _, _ = _run_user_command(['amixer', 'scontrols'])
        if rc2 == 0:
            info.amixer_available = True
            # Master doesn't exist on this card, not an error

    # ALSA capture mute
    rc, stdout, _ = _run_user_command(['amixer', 'get', 'Capture'])
    if rc == 0:
        if '[off]' in stdout:
            info.alsa_capture_muted = True
        elif '[on]' in stdout:
            info.alsa_capture_muted = False

    # Warnings
    if info.default_sink and info.default_sink.muted:
        info.warnings.append("Default audio output is muted (sound server)")
    if info.default_sink and info.default_sink.volume_pct == 0:
        info.warnings.append("Default audio output volume is 0%")
    if info.alsa_master_muted:
        info.warnings.append("ALSA Master is muted (hardware level)")
    if info.alsa_master_volume is not None and info.alsa_master_volume == 0:
        info.warnings.append("ALSA Master volume is 0%")
    if info.default_source and info.default_source.muted:
        info.warnings.append("Default microphone is muted")
    if info.alsa_capture_muted:
        info.warnings.append("ALSA Capture is muted (hardware level)")

    return info


def format_audio_report(audio: AudioInfo) -> list[str]:
    """Format audio diagnostic results for the report."""
    if not audio.pactl_available:
        if get_distro_id() == 'nixos':
            return [
                "Audio:",
                "  PipeWire with WirePlumber (NixOS)",
                "  Audio detection not supported in NixOS nix-shell environment.",
                "  Run: nix-shell -p pulseaudio --run \"pactl info\"",
            ]
        return ["Audio: pactl not available — cannot detect audio configuration"]

    lines = []
    lines.append("Audio:")

    # Server
    ver_str = f" v{audio.server_version}" if audio.server_version else ""
    sm_str = f" ({audio.session_manager})" if audio.session_manager else ""
    lines.append(f"  Server: {audio.server_name}{ver_str}{sm_str}")

    # Default output
    if audio.default_sink:
        sink = audio.default_sink
        desc = sink.description or sink.name
        mute_str = ""
        if sink.muted:
            mute_str = " ❌ MUTED"
        elif sink.muted is False:
            mute_str = ""
        vol_str = f" {sink.volume_pct}%" if sink.volume_pct is not None else ""
        lines.append(f"  Output: {desc}{vol_str}{mute_str}")

    # Default input
    if audio.default_source:
        source = audio.default_source
        desc = source.description or source.name
        # Filter out monitor sources (not real microphones)
        if '.monitor' not in source.name:
            mute_str = " ❌ MUTED" if source.muted else ""
            vol_str = f" {source.volume_pct}%" if source.volume_pct is not None else ""
            lines.append(f"  Input: {desc}{vol_str}{mute_str}")

    # ALSA level
    if audio.amixer_available:
        alsa_parts = []
        if audio.alsa_master_muted is not None:
            if audio.alsa_master_muted:
                alsa_parts.append("Master ❌ MUTED")
            else:
                vol = f" {audio.alsa_master_volume}%" if audio.alsa_master_volume is not None else ""
                alsa_parts.append(f"Master{vol}")
        if audio.alsa_capture_muted is True:
            alsa_parts.append("Capture ❌ MUTED")
        if alsa_parts:
            lines.append(f"  ALSA: {', '.join(alsa_parts)}")

    # Warnings
    for w in audio.warnings:
        lines.append(f"  ⚠️  {w}")

    return lines
