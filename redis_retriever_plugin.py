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


def key_prefix_for(index_name: str) -> str:
    """적재 쪽(vectordbs/redis.py)과 동일한 규칙으로 키 접두사를 만든다.

    예전엔 "summary" 로 고정돼 있었는데, RediSearch 인덱스는 키 접두사로 정의되므로
    모든 인덱스가 같은 키 공간을 덮어 전부 같은 문서를 보게 됐다(인덱스별 문서 수가
    똑같이 나옴). 인덱스 이름을 접두사로 쓰면 문서 집합이 인덱스별로 분리된다.

    주의: 적재와 검색이 같은 값을 써야 하므로 양쪽을 함께 바꿔야 하고, 접두사가
    바뀌면 기존 키(summary:*)는 새 인덱스에 안 잡히므로 재적재가 필요하다.
    """
    override = os.environ.get("REDIS_KEY_PREFIX", "").strip()
    return override or index_name


class RedisRetrieverPlugin:
    @hookimpl
    def getRetriever(self, type: str, index_name: str,
                     search_type: str, search_threshold: float, search_top_k: int,
                     embeddings: Any, model: Any) -> Any:
        # 기존 인덱스 검색에는 생성자 Redis(...) 가 아니라 from_existing_index 를 써야
        # 실제로 데이터가 조회됨(생성자는 빈 결과 반환). 진단 테스트로 확인됨.
        vector_db = RedisVectorDB.from_existing_index(
            embeddings,
            index_name=index_name,
            redis_url=os.environ.get("REDIS_URL"),
            schema=INDEX_SCHEMA,
            key_prefix=key_prefix_for(index_name),
        )

        # 벡터 store 자체를 retriever 로 사용 (검색된 문서를 그대로 반환)
        return vector_db.as_retriever(
            search_type="similarity",
            search_kwargs={"k": search_top_k or 4},
        )
