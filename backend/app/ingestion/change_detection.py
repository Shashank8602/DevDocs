"""Change detection: a pure function, no I/O."""
from dataclasses import dataclass, field


@dataclass
class Diff:
    created: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def diff(old: dict[str, str | None], new: dict[str, str]) -> Diff:
    """Compare stored {path: hash} with uploaded {path: hash}.

    1. in old, not in new  -> deleted
    2. in new, not in old  -> created
    3. in both, hash differs -> modified
    4. in both, hash equal   -> unchanged
    5. a rename/move is a different path, so it is deleted + created.
    A stored file with no hash (never hashed) can't be proven unchanged, so it counts as modified.
    """
    result = Diff()
    for path in sorted(old.keys() | new.keys()):
        if path not in new:
            result.deleted.append(path)
        elif path not in old:
            result.created.append(path)
        elif old[path] is None or old[path] != new[path]:
            result.modified.append(path)
        else:
            result.unchanged.append(path)
    return result
