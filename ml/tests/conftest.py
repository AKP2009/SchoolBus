import sys
from pathlib import Path

# Make `import ml...` work from any working directory, as the backend does from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
