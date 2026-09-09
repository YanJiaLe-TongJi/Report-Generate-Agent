"""Writable state is always separate from the signed/read-only application."""
import os
from pathlib import Path
from platformdirs import user_data_path
RESOURCE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('REPORT_APP_DATA', user_data_path('PhysicsReport', appauthor=False)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
try:
    from build_info import EDITION
except ImportError:
    EDITION = 'word'
os.environ.setdefault('MPLCONFIGDIR', str(DATA_DIR / 'matplotlib'))
