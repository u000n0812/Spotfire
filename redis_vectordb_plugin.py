# 데이터 로더 이미지의 /app/plugins/vectordbs/redis.py 를 대체하는 패치 파일.
#
# 원본 플러그인은 orchestrator 용 getRetriever() 만 구현하고 있어서
# 데이터 로더 본체(plugin_host.py)가 호출하는 getVectorDB() 가 없어
# "AttributeError: 'RedisRetrieverPlugin' object has no attribute 'getVectorDB'"
# 오류가 발생함. milvus 플러그인의 getVectorDB 패턴을 따라 구현을 추가하고,
# 기존 getRetriever 는 그대로 유지함.

from plugin_host import hookimpl

from typing import Any
import os

from langchain.retrievers.multi_vector import MultiVectorRetriever
from langchain_community.storage import RedisStore
from langchain_community.vectorstores import Redis as RedisVectorDB

ID_KEY = "doc_id"

# 검색/적재 양쪽이 같은 인덱스 스키마를 쓰도록 원본과 동일하게 schema.yml 사용
current_file_path = os.path.abspath(__file__)
parent_dir = os.path.dirname(current_file_path)
INDEX_SCHEMA = os.path.join(parent_dir, "schema.yml")


def key_prefix_for(index_name: str) -> str:
    """인덱스별로 서로 다른 Redis 키 접두사를 만든다.

    RediSearch 인덱스는 "이 접두사로 시작하는 키"로 정의된다. 예전엔 이 값을
    "summary" 로 고정해 둬서, 인덱스 이름을 나눠도 모든 인덱스가 같은 summary:*
    키를 덮었다. 그 결과 인덱스가 6개든 10개든 전부 적재된 문서 전체를 보게 되고
    (FT.SEARCH 건수가 모두 동일하게 나옴), 문서 집합을 나눠 검색하는 것이
    불가능했다. Spotfire 매뉴얼과 업무 문서를 함께 적재하면 서로 섞인다.

    적재와 검색이 반드시 같은 값을 써야 하므로 양쪽 플러그인이 이 함수를 공유한다.
    REDIS_KEY_PREFIX 를 지정하면 예전 동작(전 인덱스 공유)으로 되돌릴 수 있다.
    """
    override = os.environ.get("REDIS_KEY_PREFIX", "").strip()
    return override or index_name


class RedisRetrieverPlugin:
    @hookimpl
    def getVectorDB(self, index_name: str, embeddings: Any, drop_old: bool) -> Any:
        redis_url = os.environ.get("REDIS_URL")

        if drop_old:
            try:
                RedisVectorDB.drop_index(
                    index_name=index_name,
                    delete_documents=True,
                    redis_url=redis_url,
                )
            except Exception:
                # 기존 인덱스가 없으면 무시하고 새로 생성
                pass

        # key_prefix/스키마는 getRetriever 와 동일하게 맞춰서
        # orchestrator 가 적재된 문서를 그대로 검색할 수 있게 함
        vector_db = RedisVectorDB(
            redis_url=redis_url,
            index_name=index_name,
            key_prefix=key_prefix_for(index_name),
            index_schema=INDEX_SCHEMA,
            embedding=embeddings,
        )

        return vector_db

    @hookimpl
    def getRetriever(self, type: str, index_name: str,
                     search_type: str, search_threshold: float, search_top_k: int,
                     embeddings: Any, model: Any) -> Any:
        vector_db = RedisVectorDB(
            redis_url=os.environ.get("REDIS_URL"),
            index_name=index_name,
            key_prefix=key_prefix_for(index_name),
            index_schema=INDEX_SCHEMA,
            embedding=embeddings,
        )

        store = RedisStore(
            redis_url=os.environ.get("REDIS_URL"),
            namespace="image",
        )

        retriever = MultiVectorRetriever(
            vectorstore=vector_db,
            byte_store=store,
            id_key=ID_KEY,
        )

        return retriever
