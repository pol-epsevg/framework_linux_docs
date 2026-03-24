"""
Thermal monitoring and temperature analysis.
"""

import re
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from .hardware import CPUVendor, AMDGeneration
from .utils import run_command


class ThermalStatus(Enum):
    """Thermal status levels."""
    NORMAL = 'normal'
    ELEVATED = 'elevated'
    WARNING = 'warning'
    CRITICAL = 'critical'
    EMERGENCY = 'emergency'


@dataclass
class ThermalReading:
    """A single thermal reading."""
    sensor: str
    temp_celsius: float
    source: str  # Tctl, Package, Core, etc.


@dataclass
class ThermalInfo:
    """Complete thermal information."""
    cpu_temp: Optional[float] = None
    cpu_source: str = ""
    gpu_temp: Optional[float] = None
    nvme_temp: Optional[float] = None
    
    status: ThermalStatus = ThermalStatus.NORMAL
    readings: list[ThermalReading] = field(default_factory=list)
    
    # Thresholds (set based on CPU type)
    watch_threshold: int = 80
    warning_threshold: int = 85
    critical_threshold: int = 90
    emergency_threshold: int = 100


def parse_sensors_output() -> dict[str, float]:
    """Parse output from the 'sensors' command."""
    temps = {}
    
    rc, stdout, _ = run_command(['sensors'])
    if rc != 0:
        return temps
    
    for line in stdout.split('\n'):
        # Parse lines like "Tctl:         +45.0°C"
        # or "Package id 0:  +42.0°C"
        # or "Core 0:        +40.0°C"
        # or "edge:          +38.0°C"
        # or "Composite:     +35.0°C"
        
        match = re.match(r'^\s*([^:]+):\s*\+?([\d.]+)°?C', line)
        if match:
            sensor_name = match.group(1).strip()
            temp = float(match.group(2))
            temps[sensor_name] = temp
    
    return temps


def get_cpu_temperature(temps: dict[str, float]) -> tuple[Optional[float], str]:
    """
    Get CPU temperature from sensors data.
    
    Priority order:
    1. Tctl (AMD)
    2. Package (Intel)
    3. Core 0
    4. cpu@4c (some ARM/other)
    
    Returns:
        Tuple of (temperature, source_name)
    """
    # Try Tctl first (AMD)
    if 'Tctl' in temps:
        return temps['Tctl'], 'Tctl'
    
    # Try Package (Intel)
    for key in temps:
        if 'Package' in key:
            return temps[key], 'Package'
    
    # Try Core 0
    for key in temps:
        if 'Core' in key and '0' in key:
            return temps[key], 'Core'
    
    # Try cpu@4c
    for key in temps:
        if 'cpu' in key.lower():
            return temps[key], key
    
    return None, ""


def get_gpu_temperature(temps: dict[str, float]) -> Optional[float]:
    """Get GPU temperature from sensors data."""
    # AMD GPU edge temp
    if 'edge' in temps:
        return temps['edge']
    
    # Junction temp (can be higher)
    if 'junction' in temps:
        return temps['junction']
    
    return None


def get_nvme_temperature(temps: dict[str, float]) -> Optional[float]:
    """Get NVMe temperature from sensors data."""
    if 'Composite' in temps:
        return temps['Composite']
    return None


def get_thermal_thresholds(
    cpu_vendor: CPUVendor,
    amd_generation: AMDGeneration,
    is_framework: bool
) -> tuple[int, int, int, int]:
    """
    Get thermal thresholds based on CPU type.
    
    Returns:
        Tuple of (watch, warning, critical, emergency) temperatures
    """
    if cpu_vendor == CPUVendor.AMD:
        if amd_generation == AMDGeneration.MODERN:
            # Modern AMD (Ryzen 7000/8000, AI 300) runs hot by design
            # Tjmax is typically 100-105°C
            return (90, 95, 100, 105)
        else:
            # Older AMD
            return (85, 90, 95, 105)
    elif cpu_vendor == CPUVendor.INTEL:
        # Intel typically has Tjmax of 100°C
        return (80, 85, 90, 100)
    else:
        # Unknown - use conservative thresholds
        return (80, 85, 90, 100)


def evaluate_thermal_status(
    temp: float,
    watch: int,
    warning: int,
    critical: int,
    emergency: int
) -> ThermalStatus:
    """Evaluate thermal status based on temperature and thresholds."""
    if temp >= emergency:
        return ThermalStatus.EMERGENCY
    elif temp >= critical:
        return ThermalStatus.CRITICAL
    elif temp >= warning:
        return ThermalStatus.WARNING
    elif temp >= watch:
        return ThermalStatus.ELEVATED
    else:
        return ThermalStatus.NORMAL


def check_current_temperatures(
    cpu_vendor: CPUVendor = CPUVendor.UNKNOWN,
    amd_generation: AMDGeneration = AMDGeneration.LEGACY,
    is_framework: bool = False
) -> ThermalInfo:
    """
    Check current system temperatures.
    
    Args:
        cpu_vendor: CPU vendor for threshold calibration
        amd_generation: AMD generation for threshold calibration
        is_framework: Whether this is a Framework device
    
    Returns:
        ThermalInfo with all readings and status
    """
    info = ThermalInfo()
    
    # Get thresholds
    watch, warning, critical, emergency = get_thermal_thresholds(
        cpu_vendor, amd_generation, is_framework
    )
    info.watch_threshold = watch
    info.warning_threshold = warning
    info.critical_threshold = critical
    info.emergency_threshold = emergency
    
    # Parse sensors
    temps = parse_sensors_output()
    
    # Get CPU temp
    cpu_temp, source = get_cpu_temperature(temps)
    if cpu_temp is not None:
        info.cpu_temp = cpu_temp
        info.cpu_source = source
        info.readings.append(ThermalReading(
            sensor='CPU',
            temp_celsius=cpu_temp,
            source=source
        ))
        
        # Evaluate status
        info.status = evaluate_thermal_status(
            cpu_temp, watch, warning, critical, emergency
        )
    
    # Get GPU temp
    gpu_temp = get_gpu_temperature(temps)
    if gpu_temp is not None:
        info.gpu_temp = gpu_temp
        info.readings.append(ThermalReading(
            sensor='GPU',
            temp_celsius=gpu_temp,
            source='edge'
        ))
    
    # Get NVMe temp
    nvme_temp = get_nvme_temperature(temps)
    if nvme_temp is not None:
        info.nvme_temp = nvme_temp
        info.readings.append(ThermalReading(
            sensor='NVMe',
            temp_celsius=nvme_temp,
            source='Composite'
        ))
    
    return info


def format_thermal_report(
    info: ThermalInfo,
    cpu_vendor: CPUVendor,
    amd_generation: AMDGeneration,
    is_framework: bool
) -> list[str]:
    """Format thermal info for the diagnostic report."""
    lines = []
    
    lines.append("Thermal Status:")
    
    # Show raw sensor data
    for reading in info.readings:
        lines.append(f"  {reading.sensor}: {reading.temp_celsius}°C ({reading.source})")
    
    # Show interpreted CPU status
    if info.cpu_temp is not None:
        threshold_info = ""
        if is_framework:
            if cpu_vendor == CPUVendor.AMD:
                if amd_generation == AMDGeneration.MODERN:
                    threshold_info = f" (Modern AMD: runs hot by design - watch at {info.watch_threshold}°C, throttles at {info.warning_threshold}°C, critical at {info.critical_threshold}°C)"
                else:
                    threshold_info = f" (Older AMD: watch at {info.watch_threshold}°C, warning at {info.warning_threshold}°C, critical at {info.critical_threshold}°C)"
            elif cpu_vendor == CPUVendor.INTEL:
                threshold_info = f" (Intel: watch at {info.watch_threshold}°C, warning at {info.warning_threshold}°C, critical at {info.critical_threshold}°C)"
        
        lines.append(f"  Current CPU: {info.cpu_temp}°C via {info.cpu_source}{threshold_info}")
    
    return lines

