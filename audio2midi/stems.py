"""Optional stem separation (vocals / bass / drums / other) using Demucs.

Demucs is big (it needs PyTorch) so it is NOT in requirements.txt. Install it with:
    pip install -r requirements-stems.txt
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from typing import Callable, Dict, Optional

STEM_NAMES = ("vocals", "bass", "drums", "other")
MODEL = "htdemucs"


def available() -> bool:
    return importlib.util.find_spec("demucs") is not None


def separate(in_path: str, work_dir: str, log: Optional[Callable[[str], None]] = None) -> Dict[str, str]:
    """Split a song into stems. Returns {stem_name: wav_path}."""
    say = log or (lambda s: None)
    if getattr(sys, "frozen", False):
        raise RuntimeError(
            "Splitting a song into parts only works in the Python version (run.bat), not in the .exe."
        )
    if not available():
        raise RuntimeError(
            "Splitting a song needs an extra helper called Demucs.\n"
            "Close this program, open a Command Prompt in the audio2midi folder and run:\n"
            "    pip install -r requirements-stems.txt\n"
            "(it is a big download, a few GB). Then try again."
        )
    say("Splitting the song into vocals, bass, drums and other (this can take several minutes) ...")
    cmd = [sys.executable, "-m", "demucs", "-n", MODEL, "-o", work_dir, in_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        raise RuntimeError("Demucs could not split the song:\n" + "\n".join(tail))
    track = os.path.splitext(os.path.basename(in_path))[0]
    folder = os.path.join(work_dir, MODEL, track)
    out = {}
    for name in STEM_NAMES:
        p = os.path.join(folder, name + ".wav")
        if not os.path.isfile(p):
            raise RuntimeError(f"Demucs finished but {name}.wav was not created in {folder}")
        out[name] = p
    return out
