"""Per-fingerprint chunk indices: mapping, code-aware analyzer, and index creation.

`dense_vector` dims are fixed per mapping, so each embedding fingerprint (provider + model + dims)
gets its own index. Changing the embedding config therefore creates a new index instead of corrupting
the old one."""
import re

from elasticsearch import BadRequestError

from app.config import settings
from app.search import es_client

ANALYZER = "code_text"

SETTINGS = {
    # `refresh=wait_for` on bulk writes waits for the next refresh; keep that short so ingestion stays quick.
    "refresh_interval": "250ms",
    "number_of_replicas": 0,  # a single local node: a replica could never be assigned (index stays yellow)
    "analysis": {
        "filter": {
            # validate_token / JwtService / jwtService match as a whole and as their parts.
            "code_word_delimiter": {
                "type": "word_delimiter_graph",
                "preserve_original": True,
                "split_on_case_change": True,
                "split_on_numerics": False,
                "generate_word_parts": True,
                "catenate_words": False,
            },
        },
        "analyzer": {
            ANALYZER: {
                "type": "custom",
                "tokenizer": "standard",
                "filter": ["code_word_delimiter", "lowercase"],
            },
        },
    },
}


def index_name(fingerprint: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", fingerprint.lower()).strip("-")
    return f"{settings.es_index_prefix}-{slug}"


def mappings(dims: int) -> dict:
    return {
        "dynamic": "strict",
        "properties": {
            "project_id": {"type": "keyword"},
            "file_id": {"type": "keyword"},
            "file_path": {"type": "keyword"},
            "file_type": {"type": "keyword"},
            "content": {"type": "text", "analyzer": ANALYZER},
            # Searchable copies of the path and the heading / function name, so "what does auth.py do" and
            # "the upload rules section" can match on them in BM25 (`file_path` itself is a keyword).
            "path_text": {"type": "text", "analyzer": ANALYZER},
            "section": {"type": "text", "analyzer": ANALYZER},
            "embedding": {
                "type": "dense_vector", "dims": dims, "index": True, "similarity": "cosine",
            },
        },
    }


_ensured: set[str] = set()


async def ensure_index(fingerprint: str, dims: int) -> str:
    """Create the fingerprint's index if it is missing. Returns the index name."""
    name = index_name(fingerprint)
    if name in _ensured and await es_client.es.indices.exists(index=name):
        return name
    if not await es_client.es.indices.exists(index=name):
        try:
            await es_client.es.indices.create(index=name, settings=SETTINGS, mappings=mappings(dims))
        except BadRequestError as exc:
            if "resource_already_exists_exception" not in str(exc):  # created concurrently is fine
                raise
    else:  # an index created before these fields existed: adding fields to a mapping is non-breaking
        props = mappings(dims)["properties"]
        await es_client.es.indices.put_mapping(
            index=name, properties={k: props[k] for k in ("path_text", "section")},
        )
        await es_client.es.indices.put_settings(index=name, settings={"index": {"number_of_replicas": 0}})
    _ensured.add(name)
    return name
