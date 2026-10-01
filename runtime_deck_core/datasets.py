"""Freeze quality corpora so comparisons can refer to identical bytes."""

import hashlib
import os
import tempfile
from pathlib import Path


def freeze_corpus(source, root, cancelled=lambda: False):
    source = Path(source)
    directory = Path(root) / "benchmarks" / "corpora"
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    temporary = None
    try:
        with source.open("rb") as input_stream, tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            temporary = Path(output.name)
            while True:
                if cancelled():
                    raise InterruptedError("Corpus preparation cancelled")
                chunk = input_stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                output.write(chunk)
        destination = directory / f"{digest.hexdigest()}.txt"
        os.replace(temporary, destination)
        return {"original_path": str(source), "path": str(destination), "sha256": digest.hexdigest(),
                "size_bytes": destination.stat().st_size}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
