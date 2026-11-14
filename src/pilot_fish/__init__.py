from importlib.metadata import PackageNotFoundError, version

from .monitor import Monitor
from .probe import Tap
from .sinks import JsonlSink

try:
    __version__ = version("pilot-fish")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = ["JsonlSink", "Monitor", "Tap"]