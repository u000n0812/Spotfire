# orchestrator 컨테이너 전용: Ollama 로 보내는 이미지를 축소한다.
#
# 왜 필요한가:
#   Spotfire 는 페이지 스크린샷을 원본 해상도(4K)로 보낸다. 비전 모델은 이미지를
#   패치 단위 토큰으로 바꾸므로 4K 한 장이 4000~5000 토큰을 먹는다. 그래서
#   컨텍스트를 키우면 CPU 전용 환경에서 처리 시간이 몇 분씩 걸려 Analyst 가
#   "The request took too long to complete" 로 끊고, 컨텍스트를 줄이면
#   "request (N tokens) exceeds the available context size" 로 거부당한다.
#
#   가로/세로 최대 변을 1280 으로 줄이면 토큰 수가 대략 1/9 로 떨어진다.
#   대시보드의 축 레이블·범례를 읽는 데는 이 정도 해상도면 대개 충분하고,
#   원본 4K 를 그대로 넣는 것보다 오히려 판독이 안정적인 경우도 많다.
#
# 어디에 끼어드는가:
#   벤더 코드(chains.py)가 아니라 langchain 의 ChatOllama 가 메시지를 Ollama 용
#   형식으로 바꾸는 지점을 감싼다. orchestrator 버전이 올라가도 langchain 인터페이스만
#   같으면 그대로 동작하고, 벤더 파일을 덮어쓰지 않으므로 다른 기능을 망가뜨리지 않는다.
#
# 크기 조절: .env 의 COPILOT_IMAGE_MAX_EDGE (기본 1280). 0 이면 축소하지 않는다.

import base64
import io
import logging
import os

_LOG = logging.getLogger("copilot-image-shrink")

try:
    MAX_EDGE = int(os.environ.get("COPILOT_IMAGE_MAX_EDGE", "1280"))
except ValueError:
    MAX_EDGE = 1280


def _shrink_b64_image(data):
    """base64 이미지를 축소해서 돌려준다. 실패하면 원본을 그대로 반환한다.

    data URL("data:image/png;base64,...") 과 순수 base64 문자열 양쪽을 받는다.
    """
    if MAX_EDGE <= 0 or not data:
        return data

    prefix = ""
    payload = data
    if isinstance(data, str) and data.startswith("data:"):
        head, _, payload = data.partition(",")
        prefix = head + ","

    try:
        from PIL import Image

        raw = base64.b64decode(payload)
        img = Image.open(io.BytesIO(raw))
        width, height = img.size
        longest = max(width, height)
        if longest <= MAX_EDGE:
            return data

        scale = MAX_EDGE / float(longest)
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        # 팔레트/알파 이미지가 섞여 들어와도 안전하게 저장되도록 RGB 로 맞춘다.
        img = img.convert("RGB").resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        shrunk = base64.b64encode(buf.getvalue()).decode("ascii")

        _LOG.warning(
            "Shrank image %dx%d -> %dx%d (%d KB -> %d KB) for the vision model",
            width, height, new_size[0], new_size[1],
            len(payload) // 1024, len(shrunk) // 1024,
        )
        return prefix + shrunk
    except Exception as exc:
        # 축소에 실패하면 원본을 보낸다. 여기서 예외를 내면 요청 자체가 죽는다.
        _LOG.warning("Image shrink skipped (%s: %s)", type(exc).__name__, exc)
        return data


def _install():
    from langchain_community.chat_models import ollama as _ollama_chat

    chat_cls = _ollama_chat.ChatOllama
    method_name = "_convert_messages_to_ollama_messages"
    original = getattr(chat_cls, method_name, None)
    if original is None:
        _LOG.warning(
            "ChatOllama.%s not found - image shrinking is inactive on this version",
            method_name,
        )
        return

    def patched(self, messages):
        converted = original(self, messages)
        try:
            for message in converted:
                images = message.get("images")
                if images:
                    message["images"] = [_shrink_b64_image(i) for i in images]
        except Exception as exc:
            _LOG.warning("Image shrink pass skipped (%s: %s)", type(exc).__name__, exc)
        return converted

    setattr(chat_cls, method_name, patched)
    _LOG.warning("Image shrinking active: longest edge capped at %d px", MAX_EDGE)


if MAX_EDGE > 0:
    try:
        _install()
    except Exception as exc:
        # langchain 이 아직 임포트되지 않았거나 구조가 바뀐 경우.
        # orchestrator 기동을 막지 않도록 조용히 넘어간다.
        _LOG.warning("Image shrink not installed (%s: %s)", type(exc).__name__, exc)
