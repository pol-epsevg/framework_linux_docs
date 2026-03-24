# framework_diagnostic/

Source modules for `fw_diag.pyz`. This is what's inside the zip.

## Modules

| File | What it does |
|---|---|
| `__main__.py` | Entry point — argparse, interactive menu, report assembly, JSON output |
| `hardware.py` | GPU, NVMe, WiFi, RAM, expansion cards, webcam, mic, displays, RF kill, battery, disk health (nvme-cli + smartctl) |
| `firmware.py` | fwupd devices, BIOS/EC versions, Secure Boot (mokutil - EFI var - bootctl), Thunderbolt FW, fingerprint reader, kernel cmdline, `framework_tool --versions` / `--pd-info` |
| `system_info.py` | Kernel version, distro, desktop environment, session type (Wayland/X11), power management daemon (ppd/tuned/TLP), active profile, conflict detection |
| `thermal.py` | CPU/GPU/NVMe temps via `sensors`, AMD/Intel-specific thresholds |
| `network.py` | Internet/WiFi/ethernet connectivity, IP addresses, DNS, VPN detection, WiFi power save |
| `audio.py` | PipeWire vs PulseAudio, session manager, default sink/source, mute/volume at server and ALSA level |
| `bluetooth.py` | Adapter status, connected/paired devices, rfkill soft/hard block |
| `sleep.py` | Sleep mode (s2idle/deep), ACPI states, kernel suspend stats, suspend/resume counts, inhibitors, resume errors |
| `log_summary.py` | Scans journalctl + dmesg for critical events (GPU errors, kernel panics, NVMe I/O errors, OOM kills, etc.) with confidence levels |
| `distro_compat.py` | Checks running distro/version against Framework's per-model support matrix |
| `fw12.py` | Framework Laptop 12 only — tablet mode, screen rotation, touchscreen/stylus |
| `dependencies.py` | Checks for required CLI tools, auto-installs via apt/dnf/pacman/zypper, NixOS nix-shell re-exec, immutable distro handling |
| `output.py` | ANSI colors, progress display, report builder |
| `utils.py` | `run_command()` and `run_sudo_command()` wrappers used by all modules |
| `__init__.py` | Version (`5.3.0`), public API exports |
