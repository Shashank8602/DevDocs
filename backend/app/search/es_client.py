from elasticsearch import AsyncElasticsearch

from app.config import settings

es = AsyncElasticsearch(settings.elasticsearch_url)


async def ping_elasticsearch() -> None:
    if not await es.ping():
        raise ConnectionError("Elasticsearch did not respond")
