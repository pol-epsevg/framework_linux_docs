"""
Output formatting, ANSI colors, and progress display.
"""

import sys
from enum import Enum


class Color(Enum):
    """ANSI color codes."""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    RED = '\033[0;31m'
    YELLOW = '\033[1;33m'
    GREEN = '\033[0;32m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    MAGENTA = '\033[0;35m'


def colorize(text: str, color: Color, bold: bool = False) -> str:
    """Apply ANSI color to text."""
    prefix = Color.BOLD.value if bold else ''
    return f"{prefix}{color.value}{text}{Color.RESET.value}"


def print_colored(text: str, color: Color, bold: bool = False, end: str = '\n'):
    """Print colored text to stdout."""
    print(colorize(text, color, bold), end=end)


def print_error(text: str):
    """Print error message in red."""
    print_colored(f"❌ {text}", Color.RED, bold=True)


def print_warning(text: str):
    """Print warning message in yellow."""
    print_colored(f"⚠️  {text}", Color.YELLOW)


def print_success(text: str):
    """Print success message in green."""
    print_colored(f"✅ {text}", Color.GREEN)


def print_info(text: str):
    """Print info message in blue."""
    print_colored(f"ℹ️  {text}", Color.BLUE)


def show_progress(percentage: int, context: str = "Processing"):
    """Display a progress bar."""
    bar_width = 40
    filled = int(bar_width * percentage / 100)
    bar = '█' * filled + '░' * (bar_width - filled)
    sys.stdout.write(f"\r{Color.CYAN.value}[{bar}] {percentage:3d}% - {context}{Color.RESET.value}")
    sys.stdout.flush()
    if percentage >= 100:
        print()  # Newline when complete


class ReportBuilder:
    """Builds the diagnostic report output."""
    
    def __init__(self):
        self.lines: list[str] = []
    
    def add_line(self, line: str = ""):
        """Add a line to the report."""
        self.lines.append(line)
    
    def add_section(self, title: str):
        """Add a section header."""
        self.add_line()
        self.add_line(f"===== {title} =====")
        self.add_line()
    
    def add_key_value(self, key: str, value: str, indent: int = 0):
        """Add a key-value pair."""
        prefix = "  " * indent
        self.add_line(f"{prefix}{key}: {value}")
    
    def add_bullet(self, text: str, indent: int = 0):
        """Add a bullet point."""
        prefix = "  " * indent
        self.add_line(f"{prefix}• {text}")
    
    def add_indented(self, text: str, indent: int = 1):
        """Add indented text."""
        prefix = "  " * indent
        self.add_line(f"{prefix}{text}")
    
    def get_content(self) -> str:
        """Get the full report content."""
        return '\n'.join(self.lines)
    
    def write_to_file(self, filepath: str):
        """Write report to a file."""
        with open(filepath, 'w') as f:
            f.write(self.get_content())
