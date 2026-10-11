"""Upload rules: the single source of truth, served to the frontend at GET /upload-rules."""
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_RULES = json.loads((Path(__file__).parent / "upload_rules.json").read_text("utf-8"))

IGNORED_NAMES: frozenset[str] = frozenset(_RULES["ignored_names"])
MAX_FILE_BYTES: int = _RULES["max_file_bytes"]
MAX_TOTAL_BYTES: int = _RULES["max_total_bytes"]
EXTENSIONS: dict[str, dict] = _RULES["extensions"]


def rules_payload() -> dict:
    return _RULES


@dataclass(frozen=True)
class Classification:
    file_type: str  # doc | source | other
    language: str | None


def normalize_path(raw: str) -> str | None:
    """Return a safe relative POSIX path, or None if it is empty / absolute / escapes the root."""
    path = raw.replace("\\", "/")
    if not path or path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        return None
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts or ".." in parts or any("\x00" in p for p in parts):
        return None
    return "/".join(parts)


def rejection_reason(path: str, size: int) -> str | None:
    """Why a (normalized) path must be skipped, or None if it is acceptable."""
    pure = PurePosixPath(path)
    if any(part in IGNORED_NAMES for part in pure.parts):
        return "ignored"
    if pure.suffix.lower() not in EXTENSIONS:
        return "unsupported_type"
    if size > MAX_FILE_BYTES:
        return "too_large"
    return None


def classify(path: str) -> Classification:
    info = EXTENSIONS[PurePosixPath(path).suffix.lower()]
    return Classification(info["type"], info["language"])
