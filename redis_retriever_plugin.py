# orchestrator 이미지의 /app/plugins/retrievers/redis.py 를 대체하는 패치 파일.
#
# 원본은 MultiVectorRetriever 를 써서 "벡터로 요약을 찾고 → doc_id 로 별도
# 저장소(byte_store)에서 원본을 꺼내는" 2단계 구조임. 하지만 data loader 는
# 문서를 그냥 통째로 vector store 에 저장했을 뿐(doc_id/parent store 없음)이라,
# 2단계에서 원본을 못 찾아 검색 결과가 항상 0건이 됨(sources: []).
# → 벡터 유사도 검색 결과를 그대로 반환하는 단순 retriever 로 교체.

from plugin_host import hookimpl

from typing import Any
import os

from langchain_community.vectorstores import Redis as RedisVectorDB

current_file_path = os.path.abspath(__file__)
parent_dir = os.path.dirname(current_file_path)
INDEX_SCHEMA = os.path.join(parent_dir, "schema.yml")


class RedisRetrieverPlugin:
    @hookimpl
    def getRetriever(self, type: str, index_name: str,
                     search_type: str, search_threshold: float, search_top_k: int,
                     embeddings: Any, model: Any) -> Any:
        vector_db = RedisVectorDB(
            redis_url=os.environ.get("REDIS_URL"),
            index_name=index_name,
            key_prefix="summary",
            index_schema=INDEX_SCHEMA,
            embedding=embeddings,
        )

        # 벡터 store 자체를 retriever 로 사용 (검색된 문서를 그대로 반환)
        return vector_db.as_retriever(
            search_type="similarity",
            search_kwargs={"k": search_top_k or 4},
        )
