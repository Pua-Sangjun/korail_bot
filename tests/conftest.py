"""Pytest configuration and shared fixtures."""
import sys
from pathlib import Path

# Add src/ to Python path so tests can import project modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
