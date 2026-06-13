# 데이터 로더 이미지의 /app/plugins/embeddings/ollama.py 를 대체하는 패치 파일.
#
# 원본 플러그인은 OllamaEmbeddings() 를 인자 없이 생성해서
# base_url 이 기본값(http://localhost:11434)이 되고 — 컨테이너 안에서는
# 호스트 Ollama 에 절대 연결될 수 없음(Connection refused) — 임베딩 모델명도
# EMBEDDING_MODEL_NAME 환경변수를 무시하고 라이브러리 기본값을 사용함.
# models/ollama.py(ChatOllama) 가 환경변수를 읽는 방식과 동일하게 수정.
#
# 추가 1) num_ctx 를 nomic-embed-text 최대치(8192)로 올림.
# 추가 2) 임베딩 직전 텍스트를 MAX_EMBED_CHARS 로 잘라냄.
#   - pdf 청킹은 조각을 최대 4000자로 자르지만, nomic-embed-text 토크나이저는
#     영어 기준이라 한국어는 글자당 토큰이 3~4배로 늘어남. 4000자 한국어 조각이
#     8192 토큰을 넘겨 "the input length exceeds the context length" 오류 발생.
#   - 임베딩에 쓰는 텍스트만 자르며(검색용 벡터 계산에만 영향), Redis 에
#     저장되는 원문(page_content)은 그대로 유지됨.

from plugin_host import hookimpl

from typing import Any, List
import os

from langchain_community.embeddings import OllamaEmbeddings

# 1500자 * 최악 4토큰/자 = 6000토큰 < 8192, 안전 여유 확보
MAX_EMBED_CHARS = 1500


class _TruncatingOllamaEmbeddings(OllamaEmbeddings):
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        texts = [t[:MAX_EMBED_CHARS] for t in texts]
        return super().embed_documents(texts)

    def embed_query(self, text: str) -> List[float]:
        return super().embed_query(text[:MAX_EMBED_CHARS])


class OllamaEmbeddingsPlugin:
    @hookimpl
    def getEmbeddings(self) -> Any:
        embeddings = _TruncatingOllamaEmbeddings(
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.environ.get("EMBEDDING_MODEL_NAME", "nomic-embed-text"),
            num_ctx=8192,
        )

        return embeddings
