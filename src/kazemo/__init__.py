"""kazemo — pre-processing toolkit for Kazakh / code-mixed social-media text."""

from .pipeline import Record, process, stats
from .preprocess import clean, deduplicate, detect_language, detect_script, pseudonymise

__all__ = ["Record", "clean", "deduplicate", "detect_language", "detect_script", "process", "pseudonymise", "stats"]
__version__ = "0.1.0"
