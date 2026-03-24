# Framework Log Gathering Tool

Collects hardware facts and system state from Framework laptops and desktops running Linux.

> ** Your diagnostic report contains sensitive system data** — IP addresses, WiFi network names, VPN connections, DNS servers, kernel boot parameters, and full system logs. **Review the output before sharing it publicly** (e.g. forum posts, GitHub issues). Redact anything you're not comfortable posting.

> ** This tool auto-installs missing dependencies** — packages like `lspci`, `dmidecode`, and `lm-sensors` are installed via your distro's package manager (`apt`, `dnf`, `pacman`, `zypper`) **without prompting**. Dependencies come from your distro's official repositories. If [`framework_tool`](https://github.com/FrameworkComputer/framework-system) is not found locally, it is downloaded from GitHub and run as root, then deleted. On immutable distros (Bluefin, Bazzite, etc.) package installation is skipped because the package manager can't be used directly — if tools are missing, results may be incomplete.
>

## Log Gathering Tool not working **or** prefer a manual approach instead?

Paste this single-line command instead, then press Enter:

```echo "Saving logs to logs_24h.txt..."; (echo "== DMESG for Last 24 Hours =="; sudo journalctl -k --since="24 hours ago"; echo "== JOURNALCTL for Last 24 Hours =="; sudo journalctl --since="24 hours ago") > logs_24h.txt```

A file named logs_24h.txt will appear in your current directory. Attach that file in your reply to support.

-----------------------------------
-----------------------------------
-----------------------------------

## Log Gathering Tool Quick Start

```bash
curl -sO https://raw.githubusercontent.com/FrameworkComputer/linux-docs/main/fw-log-tool/fw_diag.pyz && chmod +x fw_diag.pyz
sudo ./fw_diag.pyz
```

First line downloads the tool and makes it executable. Second line runs it. No wrapper script, no pip install, no venv — just Python 3.

### Other run modes

```bash
sudo ./fw_diag.pyz --since boot        # Since last boot
sudo ./fw_diag.pyz --since 24h         # Last 24 hours
sudo ./fw_diag.pyz -o my_report.txt    # Custom output file
sudo ./fw_diag.pyz --json              # Structured JSON output
```

`sudo` is recommended. Without it, some sections (dmidecode, dmesg, NVMe SMART) will be incomplete.

## What It Collects

- **System info** — kernel version, distro, desktop environment, session type (Wayland/X11), power management daemon (ppd/tuned/TLP) with active profile and conflict detection
- **Hardware** — GPU (vendor, driver, iGPU/dGPU classification), NVMe drives, WiFi adapter, RAM (type, speed), expansion cards with USB port mapping, webcam, mic capture devices, connected displays (connector, resolution, refresh rate, PSR status), RF kill state
- **Battery** — level, health %, cycle count, charge rate, charge limit
- **Disk health** — NVMe health via `nvme smart-log`, SATA health via `smartctl`
- **Thermal** — current temps with AMD/Intel-specific thresholds (modern AMD runs hotter by design)
- **Network** — internet/WiFi/ethernet connectivity, VPN connections, IP addresses, DNS servers, WiFi power save state
- **Audio** — PipeWire/PulseAudio server info, default output/input devices, mute/volume state, ALSA mixer levels
- **Bluetooth** — adapter power/block state, connected and paired devices
- **Sleep/suspend** — current mode (s2idle/deep), available modes, ACPI states, kernel suspend stats, suspend/resume cycle counts
- **Firmware** — BIOS/EC versions, Secure Boot status (mokutil → EFI variable → bootctl fallback), Thunderbolt firmware version, fwupd devices and update status, fingerprint reader (driver, fprintd service, enrollment, PAM config). If [`framework_tool`](https://github.com/FrameworkComputer/framework-system) is not found locally, downloads it from GitHub, runs `--versions` and `--pd-info`, then deletes the downloaded binary.
- **Distro compatibility** — checks your distro/version against the Framework support matrix
- **FW12-specific** — tablet mode, screen rotation, touchscreen/stylus (Framework Laptop 12 only)
- **Raw logs** — journalctl for the selected time range + full dmesg ring buffer (not time-filtered — captures early boot messages journald may miss), plus a log summary that flags critical events (GPU errors, kernel panics, NVMe I/O errors, filesystem errors) with confidence levels

Output lands in `diagnostic_output.txt` (or `.json` with `--json`).

## Supported Framework Devices

- Framework Laptop 13 (11th–13th Gen Intel, Intel Core Ultra, AMD Ryzen 7040, AMD Ryzen AI 300)
- Framework Laptop 16 (AMD Ryzen 7040, AMD Ryzen AI 300)
- Framework Laptop 12
- Framework Desktop (AMD Ryzen AI Max 300)

## Distro Support

The supported distro versions listed here reflect the current compatibility matrix and will be updated as new releases come out. The tool itself works on beta, pre-release, and development versions of these distros — data collection doesn't depend on a specific release version.

**Officially supported** (per [frame.work/linux](https://frame.work/linux)):
- Fedora 43
- Ubuntu (version depends on model — 25.10 for newest hardware, 24.04+ or 22.04 for older models)
- Bazzite

**Community supported** — the tool runs and auto-installs deps on:
- Arch, CachyOS, EndeavourOS, Garuda
- Debian, Pop!_OS, elementary OS, Zorin
- openSUSE Tumbleweed/Leap
- NixOS (required tools like `lspci`, `dmidecode`, `sensors` aren't in PATH by default — the tool re-execs itself inside `nix-shell -p` with them, nothing permanently installed)
- Bluefin, Aurora, Kinoite, Silverblue (immutable — auto-install skipped, required tools must already be present)
- Linux Mint (not on AI 300 or Desktop models)
- Manjaro (11th/12th Gen Intel only)

Auto-install uses `apt`, `dnf`, `pacman`, or `zypper` depending on your distro.

## Requirements

- Python 3 (no third-party Python packages needed)
- Standard Linux CLI tools (`lspci`, `dmidecode`, `sensors`, `iw`, `nvme`, `fwupdmgr`, etc.) — installed automatically if missing
