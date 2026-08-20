# orchestrator 컨테이너 전용: Ollama 로 나가는 요청에서 이미지를 줄이거나 뺀다.
#
# 왜 필요한가:
#   Spotfire 는 페이지 스크린샷을 원본 해상도(4K)로 보낸다. 비전 모델은 이미지를
#   패치 단위 토큰으로 바꾸므로 4K 한 장이 4000~5000 토큰을 먹는다. 그래서
#   컨텍스트를 키우면 CPU 전용 환경에서 처리 시간이 몇 분씩 걸려 Analyst 가
#   "The request took too long to complete" 로 끊고, 컨텍스트를 줄이면
#   "request (N tokens) exceeds the available context size" 로 거부당한다.
#
# 어디에 끼어드는가 (중요):
#   이전 판은 langchain 의 ChatOllama._convert_messages_to_ollama_messages 를
#   후킹했는데, 이 이름은 langchain_community / langchain_ollama / 버전에 따라
#   달라서 조용히 아무 일도 안 할 수 있다. 그래서 이번에는 훨씬 아래층 -
#   실제로 HTTP 요청 본문(JSON)을 만드는 지점 - 을 감싼다:
#     * requests.Session.request  (langchain_community 의 ChatOllama 경로)
#     * httpx  BaseClient.build_request  (ollama 파이썬 클라이언트 경로)
#   둘 다 json= 인자에 최종 payload 가 그대로 들어오므로, 그 안에서 이미지를
#   찾아 바꾸면 어떤 라이브러리를 쓰든 무조건 걸린다.
#
# 어떻게 로드되는가:
#   docker-compose 가 이 파일을 /opt/copilot-patch/sitecustomize.py 로 마운트하고
#   PYTHONPATH=/opt/copilot-patch 를 준다. 파이썬은 기동 시 sys.path 어디에 있든
#   sitecustomize 를 자동 임포트하므로, 이미지 안 venv 경로를 몰라도 된다.
#   (이전 판은 /app/venv/lib/python3.12/site-packages 를 찍어서 마운트했는데,
#    그 경로가 실제와 다르면 도커가 엉뚱한 자리에 파일만 만들고 끝난다.)
#
# 설정 (.env):
#   COPILOT_VISION_MODE      shrink(기본) | off
#                            off = 이미지를 아예 빼고 텍스트(시각화 메타데이터)만
#                            보낸다. GPU 가 없어 비전 모델이 사실상 못 도는 환경에서
#                            "느리게 실패" 대신 "빠르게 대답" 하도록 만드는 스위치.
#   COPILOT_IMAGE_MAX_EDGE   축소 후 최대 변 픽셀 (기본 1280, 0 이면 축소 안 함)
#   COPILOT_IMAGE_ON_ERROR   keep(기본) | drop
#                            Pillow 가 없거나 디코딩에 실패했을 때의 처리.
#                            keep = 원본을 그대로 보냄(=예전 동작), drop = 이미지 제거.

import base64
import io
import json as _json
import logging
import os
import sys

_LOG = logging.getLogger("copilot-image-patch")


def _banner(msg):
    """logging 설정 전에도 보이도록 stderr 로 직접 찍는다."""
    sys.stderr.write("[copilot-image-patch] %s\n" % msg)
    sys.stderr.flush()


MODE = os.environ.get("COPILOT_VISION_MODE", "shrink").strip().lower()
ON_ERROR = os.environ.get("COPILOT_IMAGE_ON_ERROR", "keep").strip().lower()

try:
    MAX_EDGE = int(os.environ.get("COPILOT_IMAGE_MAX_EDGE", "1280"))
except ValueError:
    MAX_EDGE = 1280

_DROP = object()  # 이미지를 제거하라는 표시


# --------------------------------------------------------------------------
# 이미지 한 장 처리
# --------------------------------------------------------------------------

def _handle_failure(data, reason):
    _LOG.warning("image shrink failed (%s) -> %s", reason, ON_ERROR)
    _banner("image shrink failed (%s) -> %s" % (reason, ON_ERROR))
    return _DROP if ON_ERROR == "drop" else data


def _shrink_b64_image(data):
    """base64 이미지를 축소해서 돌려준다.

    data URL("data:image/png;base64,...") 과 순수 base64 문자열 양쪽을 받는다.
    제거해야 하면 _DROP 을 돌려준다.
    """
    if MODE == "off":
        return _DROP
    if MAX_EDGE <= 0 or not data or not isinstance(data, str):
        return data

    prefix = ""
    payload = data
    if data.startswith("data:"):
        head, _, payload = data.partition(",")
        prefix = head + ","

    try:
        from PIL import Image
    except Exception as exc:
        return _handle_failure(data, "Pillow unavailable: %s" % exc)

    try:
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

        _banner(
            "shrank image %dx%d -> %dx%d (%d KB -> %d KB)"
            % (width, height, new_size[0], new_size[1],
               len(payload) // 1024, len(shrunk) // 1024)
        )
        return prefix + shrunk
    except Exception as exc:
        return _handle_failure(data, "%s: %s" % (type(exc).__name__, exc))


_NOTE = (
    "\n\n[Note: the page screenshot was omitted because this deployment runs "
    "without a GPU. Answer using the visualization metadata above.]"
)


# --------------------------------------------------------------------------
# 요청 본문 안을 훑으며 이미지를 찾아 바꾼다
# --------------------------------------------------------------------------

def _process_message_images(node):
    """Ollama 형식: {"images": [b64, ...]} 를 제자리에서 고친다."""
    images = node.get("images")
    if not isinstance(images, list) or not images:
        return False

    kept = []
    dropped = 0
    for item in images:
        result = _shrink_b64_image(item)
        if result is _DROP:
            dropped += 1
        else:
            kept.append(result)

    if kept:
        node["images"] = kept
    else:
        node.pop("images", None)

    if dropped and isinstance(node.get("content"), str):
        node["content"] = node["content"] + _NOTE
    return True


def _process_openai_content(node):
    """OpenAI 형식: content 가 [{"type":"image_url","image_url":{"url":...}}] 인 경우."""
    content = node.get("content")
    if not isinstance(content, list):
        return False

    changed = False
    kept = []
    for part in content:
        if not isinstance(part, dict):
            kept.append(part)
            continue
        url_holder = part.get("image_url")
        if part.get("type") == "image_url" and isinstance(url_holder, dict):
            result = _shrink_b64_image(url_holder.get("url"))
            changed = True
            if result is _DROP:
                continue
            url_holder["url"] = result
        kept.append(part)

    if changed:
        node["content"] = kept
    return changed


def _walk(node, depth=0):
    """payload 를 재귀적으로 훑는다. 바뀐 게 있으면 True."""
    if depth > 12:
        return False

    changed = False
    if isinstance(node, dict):
        if _process_message_images(node):
            changed = True
        if _process_openai_content(node):
            changed = True
        for value in list(node.values()):
            if isinstance(value, (dict, list)) and _walk(value, depth + 1):
                changed = True
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, (dict, list)) and _walk(value, depth + 1):
                changed = True
    return changed


def _rewrite(payload, where):
    if not isinstance(payload, (dict, list)):
        return payload
    try:
        before = len(_json.dumps(payload))
    except Exception:
        before = -1
    try:
        if _walk(payload):
            try:
                after = len(_json.dumps(payload))
            except Exception:
                after = -1
            if before > 0 and after > 0:
                _banner("%s payload %d KB -> %d KB" % (where, before // 1024, after // 1024))
    except Exception as exc:
        # 여기서 예외를 내면 요청 자체가 죽는다. 원본을 그대로 보낸다.
        _LOG.warning("image pass skipped (%s: %s)", type(exc).__name__, exc)
    return payload


# --------------------------------------------------------------------------
# 후킹 지점
# --------------------------------------------------------------------------

def _patch_requests():
    import requests.sessions as _rs

    original = _rs.Session.request
    if getattr(original, "_copilot_patched", False):
        return "requests (already)"

    def request(self, method, url, *args, **kwargs):
        if kwargs.get("json") is not None:
            kwargs["json"] = _rewrite(kwargs["json"], "requests")
        return original(self, method, url, *args, **kwargs)

    request._copilot_patched = True
    _rs.Session.request = request
    return "requests.Session.request"


def _patch_httpx():
    import httpx._client as _hc

    original = _hc.BaseClient.build_request
    if getattr(original, "_copilot_patched", False):
        return "httpx (already)"

    def build_request(self, method, url, *args, **kwargs):
        if kwargs.get("json") is not None:
            kwargs["json"] = _rewrite(kwargs["json"], "httpx")
        return original(self, method, url, *args, **kwargs)

    build_request._copilot_patched = True
    _hc.BaseClient.build_request = build_request
    return "httpx.BaseClient.build_request"


def _install():
    installed = []
    for patch in (_patch_requests, _patch_httpx):
        try:
            installed.append(patch())
        except Exception as exc:
            _banner("could not patch via %s (%s: %s)"
                    % (patch.__name__, type(exc).__name__, exc))

    if not installed:
        _banner("NO HOOK INSTALLED - images are being sent unchanged")
        return

    if MODE == "off":
        detail = "vision OFF (images stripped)"
    else:
        detail = "max edge %d px, on-error=%s" % (MAX_EDGE, ON_ERROR)
    _banner("active: %s | hooks: %s" % (detail, ", ".join(installed)))


if MODE == "off" or MAX_EDGE > 0:
    try:
        _install()
    except Exception as exc:
        # orchestrator 기동을 막지 않도록 조용히 넘어간다.
        _banner("not installed (%s: %s)" % (type(exc).__name__, exc))
else:
    _banner("disabled (COPILOT_VISION_MODE=%s, COPILOT_IMAGE_MAX_EDGE=%d)" % (MODE, MAX_EDGE))
