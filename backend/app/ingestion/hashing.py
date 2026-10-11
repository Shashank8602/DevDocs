"""SHA-256 hashing that streams in 1 MB blocks, so large files never sit in memory."""
import hashlib
from typing import BinaryIO

BLOCK_SIZE = 1024 * 1024


def copy_and_hash(src: BinaryIO, dst: BinaryIO) -> tuple[str, int]:
    """Copy src to dst block by block; return (sha256 hex digest, bytes copied)."""
    digest = hashlib.sha256()
    size = 0
    while block := src.read(BLOCK_SIZE):
        digest.update(block)
        dst.write(block)
        size += len(block)
    return digest.hexdigest(), size
