"""
Framework device Linux distribution compatibility checking.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class SupportLevel(Enum):
    """Distribution support level."""
    OFFICIALLY_SUPPORTED = 'officially_supported'
    COMPATIBLE_COMMUNITY_SUPPORTED = 'community_supported'
    UNTESTED = 'untested'


@dataclass
class DistroInfo:
    """Linux distribution information."""
    id: str  # e.g., "fedora", "ubuntu"
    version: str  # e.g., "43", "24.04"
    pretty_name: str  # e.g., "Fedora Linux 43"


@dataclass
class CompatibilityResult:
    """Result of compatibility check."""
    support_level: SupportLevel
    model_name: str
    distro_info: DistroInfo
    recommendation: str = ""


# ===========================================================================
# COMPATIBILITY MATRICES
# ===========================================================================
#
# Last updated: February 2026
# Source: https://frame.work/linux
#
# Format: model_name -> {distro_id: versions_list, ...}
# Use '*' for rolling releases where any recent version works
#
# NOTE: frame.work/linux is the single source of truth.
# Last verified: 2026-02-07 from PDF capture of frame.work/linux

# --- Newer models: Ubuntu 25.10, no 22.04 LTS ---

# Framework Laptop 16 (AMD Ryzen AI 300) — kernel min 6.15
FRAMEWORK_LAPTOP_16_AI300 = {
    'model': 'Framework Laptop 16 (AMD Ryzen AI 300)',
    'kernel_min': '6.15',
    'kernel_rec': '6.15+',
    'official': {
        'fedora': ['43'],
        'ubuntu': ['25.10'],
        'bazzite': ['*'],
    },
    'community': {
        'arch': ['*'],
        'cachyos': ['*'],
        'nixos': ['25.10+'],
        'alma': ['*'],
        'aurora': ['*'],
        'bluefin': ['*'],
        'centos': ['*'],
        'debian': ['*'],
        'elementary': ['*'],
        'endeavouros': ['*'],
        'garuda': ['*'],
        'kali': ['*'],
        'kinoite': ['*'],
        'opensuse-tumbleweed': ['*'],
        'opensuse-leap': ['*'],
        'pop': ['*'],
        'rhel': ['*'],
        'rocky': ['*'],
        'silverblue': ['*'],
        'zorin': ['*'],
    }
}

# Framework Laptop 12 (13th Gen Intel Core) — kernel min 6.1
FRAMEWORK_LAPTOP_12 = {
    'model': 'Framework Laptop 12',
    'kernel_min': '6.1',
    'kernel_rec': '6.13+',
    'official': {
        'fedora': ['43'],
        'ubuntu': ['25.10'],
        'bazzite': ['*'],
    },
    'community': {
        'arch': ['*'],
        'cachyos': ['*'],
        'linuxmint': ['*'],
        'nixos': ['25.10+'],
        'alma': ['*'],
        'aurora': ['*'],
        'bluefin': ['*'],
        'centos': ['*'],
        'debian': ['*'],
        'elementary': ['*'],
        'endeavouros': ['*'],
        'garuda': ['*'],
        'kali': ['*'],
        'kinoite': ['*'],
        'opensuse-tumbleweed': ['*'],
        'opensuse-leap': ['*'],
        'pop': ['*'],
        'rhel': ['*'],
        'rocky': ['*'],
        'silverblue': ['*'],
        'zorin': ['*'],
    }
}

# Framework Desktop (AMD Ryzen AI Max 300) — kernel min 6.11
FRAMEWORK_DESKTOP = {
    'model': 'Framework Desktop',
    'kernel_min': '6.11',
    'kernel_rec': '6.15+',
    'official': {
        'fedora': ['43'],
        'ubuntu': ['25.10'],
        'bazzite': ['*'],
    },
    'community': {
        'arch': ['*'],
        'cachyos': ['*'],
        'nixos': ['25.10+'],
        'alma': ['*'],
        'aurora': ['*'],
        'bluefin': ['*'],
        'centos': ['*'],
        'debian': ['*'],
        'elementary': ['*'],
        'endeavouros': ['*'],
        'garuda': ['*'],
        'kali': ['*'],
        'kinoite': ['*'],
        'opensuse-tumbleweed': ['*'],
        'opensuse-leap': ['*'],
        'pop': ['*'],
        'rhel': ['*'],
        'rocky': ['*'],
        'silverblue': ['*'],
        'zorin': ['*'],
    }
}

# Framework Laptop 13 (AMD Ryzen AI 300) — kernel min 6.11
FRAMEWORK_LAPTOP_13_AI300 = {
    'model': 'Framework Laptop 13 (AMD Ryzen AI 300)',
    'kernel_min': '6.11',
    'kernel_rec': '6.15+',
    'official': {
        'fedora': ['43'],
        'ubuntu': ['25.10'],
        'bazzite': ['*'],
    },
    'community': {
        'arch': ['*'],
        'cachyos': ['*'],
        'nixos': ['25.10+'],
        'alma': ['*'],
        'aurora': ['*'],
        'bluefin': ['*'],
        'centos': ['*'],
        'debian': ['*'],
        'elementary': ['*'],
        'endeavouros': ['*'],
        'garuda': ['*'],
        'kali': ['*'],
        'kinoite': ['*'],
        'opensuse-tumbleweed': ['*'],
        'opensuse-leap': ['*'],
        'pop': ['*'],
        'rhel': ['*'],
        'rocky': ['*'],
        'silverblue': ['*'],
        'zorin': ['*'],
    }
}

# --- Mid models: Ubuntu 24.04+, no 22.04 LTS ---

# Framework Laptop 13 (Intel Core Ultra Series 1) — kernel min 6.8
FRAMEWORK_LAPTOP_13_INTEL_ULTRA = {
    'model': 'Framework Laptop 13 (Intel Core Ultra)',
    'kernel_min': '6.8',
    'kernel_rec': '6.12+',
    'official': {
        'fedora': ['43'],
        'ubuntu': ['24.04+'],
        'bazzite': ['*'],
    },
    'community': {
        'arch': ['*'],
        'cachyos': ['*'],
        'linuxmint': ['*'],
        'nixos': ['25.10+'],
        'alma': ['*'],
        'aurora': ['*'],
        'bluefin': ['*'],
        'centos': ['*'],
        'debian': ['*'],
        'elementary': ['*'],
        'endeavouros': ['*'],
        'garuda': ['*'],
        'kali': ['*'],
        'kinoite': ['*'],
        'opensuse-tumbleweed': ['*'],
        'opensuse-leap': ['*'],
        'pop': ['*'],
        'rhel': ['*'],
        'rocky': ['*'],
        'silverblue': ['*'],
        'zorin': ['*'],
    }
}

# --- Older models: Ubuntu 24.04+ AND 22.04 LTS, NixOS 24.11+ ---

# Framework Laptop 13 (AMD Ryzen 7040) — kernel min 6.6
FRAMEWORK_LAPTOP_13_AMD_7040 = {
    'model': 'Framework Laptop 13 (AMD Ryzen 7040)',
    'kernel_min': '6.6',
    'kernel_rec': '6.10+',
    'official': {
        'fedora': ['43'],
        'ubuntu': ['24.04+', '22.04'],
        'bazzite': ['*'],
    },
    'community': {
        'arch': ['*'],
        'cachyos': ['*'],
        'nixos': ['24.11+'],
        'linuxmint': ['*'],
        'alma': ['*'],
        'aurora': ['*'],
        'bluefin': ['*'],
        'centos': ['*'],
        'debian': ['*'],
        'elementary': ['*'],
        'endeavouros': ['*'],
        'garuda': ['*'],
        'kali': ['*'],
        'kinoite': ['*'],
        'opensuse-tumbleweed': ['*'],
        'opensuse-leap': ['*'],
        'pop': ['*'],
        'rhel': ['*'],
        'rocky': ['*'],
        'silverblue': ['*'],
        'zorin': ['*'],
    }
}

# Framework Laptop 16 (AMD Ryzen 7040) — kernel min 6.6
FRAMEWORK_LAPTOP_16 = {
    'model': 'Framework Laptop 16',
    'kernel_min': '6.6',
    'kernel_rec': '6.10+',
    'official': {
        'fedora': ['43'],
        'ubuntu': ['24.04+', '22.04'],
        'bazzite': ['*'],
    },
    'community': {
        'arch': ['*'],
        'cachyos': ['*'],
        'nixos': ['24.11+'],
        'linuxmint': ['*'],
        'alma': ['*'],
        'aurora': ['*'],
        'bluefin': ['*'],
        'centos': ['*'],
        'debian': ['*'],
        'elementary': ['*'],
        'endeavouros': ['*'],
        'garuda': ['*'],
        'kali': ['*'],
        'kinoite': ['*'],
        'opensuse-tumbleweed': ['*'],
        'opensuse-leap': ['*'],
        'pop': ['*'],
        'rhel': ['*'],
        'rocky': ['*'],
        'silverblue': ['*'],
        'zorin': ['*'],
    }
}

# Framework Laptop 13 (13th Gen Intel Core)
FRAMEWORK_LAPTOP_13_INTEL_13GEN = {
    'model': 'Framework Laptop 13 (13th Gen Intel)',
    'official': {
        'fedora': ['43'],
        'ubuntu': ['24.04+', '22.04'],
        'bazzite': ['*'],
    },
    'community': {
        'arch': ['*'],
        'cachyos': ['*'],
        'nixos': ['24.11+'],
        'linuxmint': ['*'],
        'alma': ['*'],
        'aurora': ['*'],
        'bluefin': ['*'],
        'centos': ['*'],
        'debian': ['*'],
        'elementary': ['*'],
        'endeavouros': ['*'],
        'garuda': ['*'],
        'kali': ['*'],
        'kinoite': ['*'],
        'opensuse-tumbleweed': ['*'],
        'opensuse-leap': ['*'],
        'pop': ['*'],
        'rhel': ['*'],
        'rocky': ['*'],
        'silverblue': ['*'],
        'zorin': ['*'],
    }
}

# --- Oldest models: add Manjaro XFCE to community ---

# Framework Laptop 13 (12th Gen Intel Core)
FRAMEWORK_LAPTOP_13_INTEL_12GEN = {
    'model': 'Framework Laptop 13 (12th Gen Intel)',
    'official': {
        'fedora': ['43'],
        'ubuntu': ['24.04+', '22.04'],
        'bazzite': ['*'],
    },
    'community': {
        'manjaro': ['*'],
        'linuxmint': ['*'],
        'arch': ['*'],
        'cachyos': ['*'],
        'nixos': ['24.11+'],
        'alma': ['*'],
        'aurora': ['*'],
        'bluefin': ['*'],
        'centos': ['*'],
        'debian': ['*'],
        'elementary': ['*'],
        'endeavouros': ['*'],
        'garuda': ['*'],
        'kali': ['*'],
        'kinoite': ['*'],
        'opensuse-tumbleweed': ['*'],
        'opensuse-leap': ['*'],
        'pop': ['*'],
        'rhel': ['*'],
        'rocky': ['*'],
        'silverblue': ['*'],
        'zorin': ['*'],
    }
}

# Framework Laptop 13 (11th Gen Intel Core)
FRAMEWORK_LAPTOP_13_INTEL_11GEN = {
    'model': 'Framework Laptop 13 (11th Gen Intel)',
    'official': {
        'fedora': ['43'],
        'ubuntu': ['24.04+', '22.04'],
        'bazzite': ['*'],
    },
    'community': {
        'manjaro': ['*'],
        'linuxmint': ['*'],
        'arch': ['*'],
        'cachyos': ['*'],
        'nixos': ['24.11+'],
        'alma': ['*'],
        'aurora': ['*'],
        'bluefin': ['*'],
        'centos': ['*'],
        'debian': ['*'],
        'elementary': ['*'],
        'endeavouros': ['*'],
        'garuda': ['*'],
        'kali': ['*'],
        'kinoite': ['*'],
        'opensuse-tumbleweed': ['*'],
        'opensuse-leap': ['*'],
        'pop': ['*'],
        'rhel': ['*'],
        'rocky': ['*'],
        'silverblue': ['*'],
        'zorin': ['*'],
    }
}


def get_distro_info() -> Optional[DistroInfo]:
    """Read distribution information from /etc/os-release."""
    os_release = Path('/etc/os-release')
    
    if not os_release.exists():
        return None
    
    info = {}
    try:
        content = os_release.read_text()
        for line in content.split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                info[key] = value.strip('"')
    except Exception:
        return None
    
    return DistroInfo(
        id=info.get('ID', 'unknown'),
        version=info.get('VERSION_ID', 'unknown'),
        pretty_name=info.get('PRETTY_NAME', 'Unknown Linux')
    )


def determine_framework_model(product_name: str, model_version: str, cpu_model: str = "") -> dict:
    """
    Determine which Framework model compatibility matrix to use.
    
    Args:
        product_name: From dmidecode system-product-name
        model_version: From dmidecode system-version
        cpu_model: CPU model string for disambiguation
    
    Returns:
        The appropriate compatibility matrix dict
    """
    combined = f"{product_name} {model_version}".lower()
    
    # Framework Laptop 12
    if 'laptop 12' in combined:
        return FRAMEWORK_LAPTOP_12
    
    # Framework Desktop
    if 'desktop' in combined:
        return FRAMEWORK_DESKTOP
    
    # Framework Laptop 16
    if 'laptop 16' in combined:
        if 'ai' in combined or 'ai 300' in cpu_model.lower():
            return FRAMEWORK_LAPTOP_16_AI300
        return FRAMEWORK_LAPTOP_16
    
    # Framework Laptop 13
    if 'laptop 13' in combined or 'framework' in combined:
        # Check CPU for disambiguation
        if 'ai 300' in cpu_model.lower() or 'ai' in combined:
            return FRAMEWORK_LAPTOP_13_AI300
        elif 'ultra' in cpu_model.lower() or 'core ultra' in combined:
            return FRAMEWORK_LAPTOP_13_INTEL_ULTRA
        elif '7040' in cpu_model or 'ryzen' in cpu_model.lower():
            return FRAMEWORK_LAPTOP_13_AMD_7040
        elif '13th gen' in combined or '-13' in cpu_model:
            return FRAMEWORK_LAPTOP_13_INTEL_13GEN
        elif '12th gen' in combined or '-12' in cpu_model:
            return FRAMEWORK_LAPTOP_13_INTEL_12GEN
        elif '11th gen' in combined or '-11' in cpu_model:
            return FRAMEWORK_LAPTOP_13_INTEL_11GEN
        elif 'core i' in cpu_model.lower():
            # Generic Intel — guess by CPU generation number if present
            return FRAMEWORK_LAPTOP_13_INTEL_13GEN
        
        # Default to the most recent/common
        return FRAMEWORK_LAPTOP_13_INTEL_13GEN
    
    # Unknown - return generic Laptop 13
    return FRAMEWORK_LAPTOP_13_INTEL_13GEN


def check_version_match(supported_versions: list[str], current_version: str) -> bool:
    """Check if current version matches any supported version.
    
    Supports:
      '*'     — any version (rolling releases)
      '24.04+' — 24.04 or newer (compares major.minor numerically)
      '43'    — exact match
    """
    if '*' in supported_versions:
        return True
    for sv in supported_versions:
        if sv.endswith('+'):
            # "24.04+" means >= 24.04
            try:
                min_parts = [int(x) for x in sv.rstrip('+').split('.')]
                cur_parts = [int(x) for x in current_version.split('.')]
                if cur_parts >= min_parts:
                    return True
            except (ValueError, AttributeError):
                continue
        elif sv == current_version:
            return True
    return False


def check_framework_distro_compatibility(
    product_name: str,
    model_version: str,
    cpu_model: str = ""
) -> Optional[CompatibilityResult]:
    """
    Check if the current distro is compatible with the Framework device.
    
    Args:
        product_name: From dmidecode system-product-name
        model_version: From dmidecode system-version
        cpu_model: CPU model for disambiguation
    
    Returns:
        CompatibilityResult or None if not a Framework device
    """
    # Check if this is a Framework device
    framework_indicators = ['Framework', 'Laptop 13', 'Laptop 16', 'Laptop 12', 'Desktop']
    if not any(ind in product_name for ind in framework_indicators):
        return None
    
    # Get current distro
    distro = get_distro_info()
    if distro is None:
        return None
    
    # Get the appropriate compatibility matrix
    compat_matrix = determine_framework_model(product_name, model_version, cpu_model)
    model_name = compat_matrix['model']
    
    # Check official support
    if distro.id in compat_matrix.get('official', {}):
        versions = compat_matrix['official'][distro.id]
        if check_version_match(versions, distro.version):
            return CompatibilityResult(
                support_level=SupportLevel.OFFICIALLY_SUPPORTED,
                model_name=model_name,
                distro_info=distro
            )
        else:
            # Distro is official but wrong version
            return CompatibilityResult(
                support_level=SupportLevel.UNTESTED,
                model_name=model_name,
                distro_info=distro,
                recommendation=f"{model_name} officially supports {distro.id.title()} {', '.join(versions)}. Current: {distro.id.title()} {distro.version}"
            )
    
    # Check community support
    if distro.id in compat_matrix.get('community', {}):
        versions = compat_matrix['community'][distro.id]
        if check_version_match(versions, distro.version):
            return CompatibilityResult(
                support_level=SupportLevel.COMPATIBLE_COMMUNITY_SUPPORTED,
                model_name=model_name,
                distro_info=distro
            )
        else:
            return CompatibilityResult(
                support_level=SupportLevel.UNTESTED,
                model_name=model_name,
                distro_info=distro,
                recommendation=f"{model_name} community supports {distro.id.title()} {', '.join(versions)}. Current: {distro.id.title()} {distro.version}"
            )
    
    # Not in any list
    official_distros = list(compat_matrix.get('official', {}).keys())
    community_distros = list(compat_matrix.get('community', {}).keys())
    
    recommendation = f"{model_name} officially supports: {', '.join(d.title() for d in official_distros)}."
    if community_distros:
        recommendation += f" Community supported: {', '.join(d.title() for d in community_distros)}"
    
    return CompatibilityResult(
        support_level=SupportLevel.UNTESTED,
        model_name=model_name,
        distro_info=distro,
        recommendation=recommendation
    )


def format_compatibility_report(result: CompatibilityResult) -> list[str]:
    """Format compatibility result for the diagnostic report."""
    lines = []
    
    lines.append("Distribution Compatibility:")
    lines.append(f"  Device: {result.model_name}")
    lines.append(f"  Distribution: {result.distro_info.pretty_name}")
    
    if result.support_level == SupportLevel.OFFICIALLY_SUPPORTED:
        lines.append("  Status: ✅ Officially supported and tested")
    elif result.support_level == SupportLevel.COMPATIBLE_COMMUNITY_SUPPORTED:
        lines.append("  Status: 🔵 Community supported")
    else:
        lines.append("  Status: ⚠️  Untested configuration")
        if result.recommendation:
            lines.append(f"  Note: {result.recommendation}")
    
    return lines

