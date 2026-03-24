"""
Shared utility functions.

Centralizes command execution so it's not duplicated across
hardware.py, thermal.py, network.py, and system_info.py.
"""

import subprocess


def run_command(cmd: list[str], timeout: int = 10, env=None) -> tuple[int, str, str]:
    """
    Run a command and return (returncode, stdout, stderr).

    Returns (-1, "", <reason>) on timeout or missing command.
    
    Args:
        env: Optional environment dict. If provided, replaces the default
             environment for the subprocess.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", "command not found"


def run_sudo_command(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command with sudo."""
    return run_command(['sudo'] + cmd, timeout)
