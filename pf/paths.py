"""Where the app keeps its data files (config, logs, history, prompt catalog).

In development that's the project root. When frozen into a PyInstaller exe,
`__file__` lives in a temporary extraction dir that is deleted on exit, so we
must instead use the folder containing the .exe - giving a portable app whose
config/logs/prompts sit next to the executable.
"""

import sys
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, 'frozen', False):  # running inside a PyInstaller bundle
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
