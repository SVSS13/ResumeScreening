import sys
from pathlib import Path

SUBMISSION_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUBMISSION_DIR / "src"))
sys.path.insert(0, str(SUBMISSION_DIR))
sys.path.insert(0, str(Path(__file__).parent))
