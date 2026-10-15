"""Find the current Beam CLI even when pyenv puts the legacy SDK first."""

import os
import shutil
import subprocess
from pathlib import Path


def supports_vms(executable):
    try:
        return subprocess.run(
            [str(executable), "vm", "--help"], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_beam_cli(preferred=None):
    requested = os.environ.get("BEAM_CLI") or preferred
    if requested:
        path = Path(requested).expanduser()
        if not path.is_absolute() and len(path.parts) == 1:
            path = Path(shutil.which(str(path)) or path)
        path = path.resolve()
        if path.is_file() and os.access(path, os.X_OK) and supports_vms(path):
            return str(path)
        raise ValueError(f"Beam CLI at {path} does not support VM commands. Choose the CLI installed by https://www.beam.cloud/install.")

    candidates = [Path(folder) / "beam" for folder in os.get_exec_path()]
    candidates.append(Path.home() / ".local" / "bin" / "beam")
    seen = set()
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file() and os.access(candidate, os.X_OK) and supports_vms(candidate):
            return str(candidate)
    raise ValueError(
        "No Beam CLI with VM support was found. Install it with "
        "curl -fsSL https://www.beam.cloud/install | sh, "
        "or set BEAM_CLI to its executable path."
    )
