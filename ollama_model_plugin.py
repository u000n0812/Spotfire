# orchestrator 이미지의 /app/plugins/models/ollama.py 를 대체하는 패치 파일.
#
# 원본은 ChatOllama 에 num_ctx 를 지정하지 않아 Ollama 기본값(2048 토큰)을 사용함.
# 검색 문맥(topk * 청크)을 조금만 키워도 2048 을 넘쳐, 모델이 입력을 제대로 못 읽고
# 깨진 출력("YH" 등)을 내거나 답을 놓침.
# → num_ctx 를 키워 더 많은 문맥을 안정적으로 처리.
#   RAM 부족/속도 문제 시 .env 에 CHAT_NUM_CTX 를 넣어 조절 가능(미설정 시 8192).
#   (qwen2.5 는 최대 32k, gemma3:4b 는 최대 8k 이상 지원)

from plugin_host import hookimpl

from typing import Any
import os

from langchain_community.llms import Ollama
from langchain_community.chat_models import ChatOllama

NUM_CTX = int(os.environ.get("CHAT_NUM_CTX", "8192"))


class OllamaPlugin:

    @hookimpl
    def getModel(self, model_mode: str, name: str, temperature: float) -> Any:
        model = None

        if model_mode == "completions":
            model = Ollama(
                base_url=os.environ.get("OLLAMA_BASE_URL"),
                model=name,
                temperature=temperature,
                num_ctx=NUM_CTX,
            )
        #  Default to chat mode
        else:
            model = ChatOllama(
                base_url=os.environ.get("OLLAMA_BASE_URL"),
                model=name,
                temperature=temperature,
                num_predict=-1,
                num_ctx=NUM_CTX,
            )

        return model
