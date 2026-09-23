import sys
from pathlib import Path

# vision/ modules are flat (run.py is launched as a script), so put vision/ on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
