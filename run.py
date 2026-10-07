"""Run TubeFetch in development:  python run.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from app.main import run_app

if __name__ == "__main__":
    raise SystemExit(run_app())
