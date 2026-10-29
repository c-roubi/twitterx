# -*- coding: utf-8 -*-
"""Unified logging: console + daily file with colors and English."""
import logging
import os
from datetime import datetime

from .config import LOGS_DIR

_configured = False

# ANSI color codes (sqlmap style)
class Colors:
    """Terminal color codes for professional CLI output."""
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'

    # Status indicators
    SUCCESS = f"{GREEN}[+]{RESET}"
    ERROR = f"{RED}[-]{RESET}"
    WARNING = f"{YELLOW}[!]{RESET}"
    INFO = f"{CYAN}[*]{RESET}"
    DEBUG = f"{MAGENTA}[~]{RESET}"


class ColoredFormatter(logging.Formatter):
    """Custom formatter with sqlmap-style colors."""

    LEVEL_COLORS = {
        'DEBUG': Colors.DEBUG,
        'INFO': Colors.INFO,
        'WARNING': Colors.WARNING,
        'ERROR': Colors.ERROR,
        'CRITICAL': Colors.ERROR,
    }

    def format(self, record):
        # Add colors based on level
        level_indicator = self.LEVEL_COLORS.get(record.levelname, Colors.INFO)

        # Format: [TIME] [LEVEL] [MODULE] MESSAGE
        timestamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")

        message = (
            f"{Colors.DIM}{timestamp}{Colors.RESET} | "
            f"{level_indicator} | "
            f"{Colors.CYAN}{record.name}{Colors.RESET} | "
            f"{record.getMessage()}"
        )

        return message


def get_logger(name="app"):
    """Get logger with colors and English output."""
    global _configured
    logger = logging.getLogger(f"smartengage.{name}")

    logger.setLevel(logging.DEBUG)

    if not _configured:
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)

        # Console handler with colors
        if not root_logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(ColoredFormatter())
            root_logger.addHandler(console_handler)

        # File handler (plain text for logs)
        try:
            os.makedirs(LOGS_DIR, exist_ok=True)
            log_filename = f"bot_{datetime.now():%Y-%m-%d}.log"
            file_path = os.path.join(LOGS_DIR, log_filename)

            file_handler = logging.FileHandler(file_path, encoding="utf-8")
            file_formatter = logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            file_handler.setFormatter(file_formatter)
            root_logger.addHandler(file_handler)
        except Exception as e:
            print(f"{Colors.WARNING} Warning: Could not create log file: {e}{Colors.RESET}")

        _configured = True

    return logger


# Convenience functions
def info(msg, name="app"):
    """Log info message."""
    get_logger(name).info(msg)

def error(msg, name="app"):
    """Log error message."""
    get_logger(name).error(msg)

def warning(msg, name="app"):
    """Log warning message."""
    get_logger(name).warning(msg)

def debug(msg, name="app"):
    """Log debug message."""
    get_logger(name).debug(msg)
