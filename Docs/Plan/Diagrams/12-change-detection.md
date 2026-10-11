# 12 — Change Detection

`diff(old: {path: hash}, new: {path: hash}) -> {created, modified, deleted, unchanged}`. Pure function, no I/O. Source: Plan → Backend Behaviour §1; Phase 3.

```mermaid
flowchart TD
    Start["For each path in old ∪ new"] --> InNew{"In new?"}
    InNew -- no --> Del["deleted<br/>remove file, chunks, vectors"]
    InNew -- yes --> InOld{"In old?"}
    InOld -- no --> Cre["created<br/>→ pending → ingest"]
    InOld -- yes --> Same{"hash equal?"}
    Same -- yes --> Unch["unchanged<br/>skip entirely"]
    Same -- no --> Mod["modified<br/>delete old chunks/vectors,<br/>→ pending → ingest"]
```

## Worked example (Phase 3 proof)

| Action on `samples/ecommerce-demo` | Result |
|---|---|
| Edit `auth.py` | modified |
| Delete `database.py` | deleted |
| Add `security.py` | created |
| Move `utils.py` → `sub/utils.py` | `utils.py` deleted + `sub/utils.py` created |
| Everything else | unchanged |

```mermaid
flowchart LR
    subgraph Old["Stored"]
        o1["auth.py (h1)"]
        o2["database.py (h2)"]
        o3["utils.py (h3)"]
        o4["main.py (h4)"]
    end
    subgraph New["Uploaded"]
        n1["auth.py (h1')"]
        n2["security.py (h5)"]
        n3["sub/utils.py (h3)"]
        n4["main.py (h4)"]
    end
    o1 -- "hash differs" --> n1
    o4 -- "hash same" --> n4
    o2 -. "absent" .-> X1["deleted"]
    o3 -. "path absent" .-> X2["deleted"]
    n2 -. "new path" .-> X3["created"]
    n3 -. "new path" .-> X4["created"]
```

**Notes**
- A rename or move is always delete + create (no content-based rename detection).
- Hashing streams each file in 1 MB blocks (SHA-256).
- `PUT /files/{id}` with an identical hash skips processing.
