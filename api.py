"""FastAPI application entry point.

Run with:
    uvicorn api:app --reload --port 8000
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from screener.api.app import app  # noqa: E402

__all__ = ["app"]
