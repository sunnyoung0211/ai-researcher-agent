"""Capture the actual experiment interpreter, package lock and host hardware without loading a model."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from airesearcher.core.fsutil import sha256_bytes


def capture(root: Path, python: str):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    lock = subprocess.run(
        [python, "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
        env=env,
        check=True,
    ).stdout
    digest = sha256_bytes(lock.encode())[:16]
    directory = root / "environments" / digest
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "requirements.lock").write_text(lock, encoding="utf-8")
    code = (
        "import sys,json,importlib.metadata as m; names=['torch','transformers','peft','scikit-learn','numpy']; "
        "installed={d.metadata['Name'].lower():d.version for d in m.distributions()}; "
        "print(json.dumps({'python':sys.version,'executable':sys.executable,"
        "'frameworks':{n:installed.get(n) for n in names}}))"
    )
    info = json.loads(
        subprocess.run(
            [python, "-c", code], capture_output=True, text=True, encoding="utf-8", timeout=20, env=env, check=True
        ).stdout
    )
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        gpu = None
    text = (
        "# Reproducing an archived experiment\n\n"
        f"Recorded interpreter: `{info['executable']}`. Python: `{info['python'].splitlines()[0]}`.\n\n"
        "1. Create a compatible isolated Python environment.\n"
        "2. Install the recorded packages with `python -m pip install -r requirements.lock`.\n"
        "3. Restore prepared data/model files and verify the SHA256 values listed in the run's inputs.\n"
        "4. Set AIR_DATA_DIR and AIR_MODEL_DIR to those verified local directories.\n"
        "5. Copy the archived run directory, including config.yaml, then enter its code/ directory.\n"
        "   Execute run.json's command, changing the output directory to a new empty location.\n"
        "   Preserve the configuration and random seed.\n\n"
        "HF_HUB_OFFLINE=1 and TRANSFORMERS_OFFLINE=1 are required. CUDA hardware and a compatible driver\n"
        "are required for CUDA configurations. A different platform may require compatible wheel versions;\n"
        "the lock is an environment record, not a guarantee of bitwise reproducibility.\n"
    )
    (directory / "REPRODUCE.md").write_text(text, encoding="utf-8")
    return {
        **info,
        "lock_path": f"environments/{digest}/requirements.lock",
        "lock_sha256": sha256_bytes(lock.encode()),
        "gpu": gpu,
    }
