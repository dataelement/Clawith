"""Test configuration for clawith-bridge."""
import sys
from pathlib import Path

# Ensure the package root is on sys.path when running pytest from any cwd.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
