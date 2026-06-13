# 데이터 로더 이미지의 /app/plugins/embeddings/ollama.py 를 대체하는 패치 파일.
#
# 원본 플러그인은 OllamaEmbeddings() 를 인자 없이 생성해서
# base_url 이 기본값(http://localhost:11434)이 되고 — 컨테이너 안에서는
# 호스트 Ollama 에 절대 연결될 수 없음(Connection refused) — 임베딩 모델명도
# EMBEDDING_MODEL_NAME 환경변수를 무시하고 라이브러리 기본값을 사용함.
# models/ollama.py(ChatOllama) 가 환경변수를 읽는 방식과 동일하게 수정.
#
# 추가로 num_ctx 를 키움: nomic-embed-text 의 Ollama 기본 컨텍스트는
# 2048 토큰인데, 한국어 텍스트는 토큰이 많이 나와 조각 하나가 이를 넘기면
# "the input length exceeds the context length" 오류로 임베딩이 실패함.
# nomic-embed-text 가 지원하는 최대치(8192)로 올려 회피.

from plugin_host import hookimpl

from typing import Any
import os

from langchain_community.embeddings import OllamaEmbeddings


class OllamaEmbeddingsPlugin:
    @hookimpl
    def getEmbeddings(self) -> Any:
        embeddings = OllamaEmbeddings(
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            model=os.environ.get("EMBEDDING_MODEL_NAME", "nomic-embed-text"),
            num_ctx=8192,
        )

        return embeddings
