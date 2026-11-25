"""Read the same offline managed component that extended-pkg installs."""

import atexit
from functools import lru_cache
from pathlib import Path
import tarfile
import tempfile


@lru_cache(maxsize=1)
def runtime_root():
    repo = Path(__file__).resolve().parents[3]
    payloads = repo / "overlays/233-dev.2/root/usr/local/share/233-firmware"
    archive = payloads / "multiace-managed-1.27b-233.20261122-flow.15.116.tar.gz"
    temp = tempfile.TemporaryDirectory()
    atexit.register(temp.cleanup)
    with tarfile.open(archive) as package:
        package.extractall(temp.name, filter="data")
    return next(Path(temp.name).iterdir())
