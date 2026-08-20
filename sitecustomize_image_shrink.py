# orchestrator 컨테이너 전용: Ollama 로 나가는 요청을 CPU 환경에 맞게 손본다.
#
# 무엇을 고치나:
#   Analyst 의 "The request took too long to complete" 는 한 가지 원인이 아니다.
#   요청 하나에 드는 시간은 세 덩어리로 나뉘고, 이 파일은 셋 다 건드린다.
#
#     1) 프리필 - 입력 토큰을 읽는 시간.
#        Spotfire 는 4K 스크린샷을 보내고, 비전 모델은 이걸 패치 토큰으로 바꿔
#        한 장에 4000~5000 토큰을 쓴다. CPU 프리필은 초당 수십 토큰이라
#        이것만으로 2~4분이 나간다.  -> 이미지를 줄이거나(shrink) 뺀다(off).
#
#     2) 모델 로드 - Ollama 가 가중치를 메모리에 올리는 시간.
#        keep_alive 기본값은 5분이라, 잠깐 쉬면 다음 요청에서 7B 를 디스크에서
#        다시 읽는다. CPU 장비에서 이게 10~60초다.  -> keep_alive 를 길게 박는다.
#
#     3) 생성 - 답을 뱉는 시간.
#        CPU 에서 7B 는 초당 5~10 토큰. 모델이 800 토큰짜리 답을 쓰기로 마음먹으면
#        그것만 2분이다.  -> num_predict 로 상한을 건다.
#
# 어디에 끼어드는가:
#   벤더 코드나 langchain 사설 메서드가 아니라, 실제로 HTTP 본문(JSON)을 만드는
#   지점을 감싼다:
#     * requests.Session.request          (langchain_community 의 ChatOllama 경로)
#     * httpx  BaseClient.build_request   (ollama 파이썬 클라이언트 경로)
#   둘 다 json= 인자에 최종 payload 가 그대로 들어오므로, 어떤 라이브러리를 쓰든
#   무조건 걸린다. 라이브러리 버전이 올라가도 깨지지 않는다.
#
# 어떻게 로드되는가:
#   docker-compose 가 이 파일을 /opt/copilot-patch/sitecustomize.py 로 마운트하고
#   PYTHONPATH=/opt/copilot-patch 를 준다. 파이썬은 기동 시 sys.path 어디에 있든
#   sitecustomize 를 자동 임포트하므로 이미지 안 venv 경로를 몰라도 된다.
#
# 설정은 전부 .env 에 있다 (COPILOT_* 참고).

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


def _int_env(name, default):
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


MODE = os.environ.get("COPILOT_VISION_MODE", "shrink").strip().lower()
ON_ERROR = os.environ.get("COPILOT_IMAGE_ON_ERROR", "keep").strip().lower()
MAX_EDGE = _int_env("COPILOT_IMAGE_MAX_EDGE", 1280)

# 생성 상한. 0 이면 건드리지 않는다.
NUM_PREDICT = _int_env("COPILOT_NUM_PREDICT", 0)
# 컨텍스트 크기. 0 이면 건드리지 않는다(모델에 박힌 값을 씀).
NUM_CTX = _int_env("COPILOT_NUM_CTX", 0)
# 모델을 메모리에 붙잡아 두는 시간. 빈 문자열이면 건드리지 않는다.
KEEP_ALIVE = os.environ.get("COPILOT_KEEP_ALIVE", "").strip()
# 이미지를 뺐을 때 갈아끼울 텍스트 모델. 비우면 그대로 둔다.
TEXT_MODEL = os.environ.get("COPILOT_TEXT_MODEL", "").strip()

# 입력 토큰 상한. 0 이면 자르지 않는다.
MAX_PROMPT_TOKENS = _int_env("COPILOT_MAX_PROMPT_TOKENS", 0)
# 유지할 대화 턴 수(system 제외). 0 이면 자르지 않는다.
MAX_HISTORY = _int_env("COPILOT_MAX_HISTORY", 0)
# 나가는 요청을 파일로 떨궈 내용을 확인한다(비우면 안 함).
DUMP_DIR = os.environ.get("COPILOT_DUMP_DIR", "").strip()
# 이 토큰 수를 넘는 요청은 메시지별 크기를 로그에 풀어서 남긴다.
_DESCRIBE_OVER = _int_env("COPILOT_DESCRIBE_OVER", 1500)

_DROP = object()  # 이미지를 제거하라는 표시


# --------------------------------------------------------------------------
# 이미지 한 장 처리
# --------------------------------------------------------------------------

def _handle_failure(data, reason):
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
    "\n\n[Note: the page screenshot was omitted because this deployment has no GPU. "
    "Describe the page from the visualization metadata given above - chart types, "
    "axis columns, filters and markings. Do not claim to see the image, and do not "
    "guess at values you were not given.]"
)


# --------------------------------------------------------------------------
# 요청 본문 안을 훑으며 이미지를 찾아 바꾼다
# --------------------------------------------------------------------------

def _process_message_images(node, state):
    """Ollama 형식: {"images": [b64, ...]} 를 제자리에서 고친다."""
    images = node.get("images")
    if not isinstance(images, list) or not images:
        return

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

    state["dropped"] += dropped
    state["seen"] += len(images)
    if dropped and isinstance(node.get("content"), str):
        node["content"] = node["content"] + _NOTE


def _process_openai_content(node, state):
    """OpenAI 형식: content 가 [{"type":"image_url","image_url":{"url":...}}] 인 경우."""
    content = node.get("content")
    if not isinstance(content, list):
        return

    changed = False
    kept = []
    for part in content:
        if not isinstance(part, dict):
            kept.append(part)
            continue
        url_holder = part.get("image_url")
        if part.get("type") == "image_url" and isinstance(url_holder, dict):
            changed = True
            state["seen"] += 1
            result = _shrink_b64_image(url_holder.get("url"))
            if result is _DROP:
                state["dropped"] += 1
                continue
            url_holder["url"] = result
        kept.append(part)

    if changed:
        node["content"] = kept


def _walk(node, state, depth=0):
    if depth > 12:
        return
    if isinstance(node, dict):
        _process_message_images(node, state)
        _process_openai_content(node, state)
        for value in list(node.values()):
            if isinstance(value, (dict, list)):
                _walk(value, state, depth + 1)
    elif isinstance(node, list):
        for value in node:
            if isinstance(value, (dict, list)):
                _walk(value, state, depth + 1)


# --------------------------------------------------------------------------
# 생성 옵션 조정
# --------------------------------------------------------------------------

def _estimate_tokens(text):
    """토크나이저 없이 입력 토큰 수를 어림한다.

    한글/한자/가나는 대략 글자당 1토큰, 그 외(영문·숫자·JSON 기호)는 3.5글자당
    1토큰으로 잡는다. 정확할 필요는 없고, 자를지 말지를 판단할 정도면 된다.
    """
    if not isinstance(text, str):
        return 0
    cjk = 0
    for ch in text:
        code = ord(ch)
        if (0xAC00 <= code <= 0xD7A3        # 한글 음절
                or 0x1100 <= code <= 0x11FF  # 한글 자모
                or 0x3040 <= code <= 0x30FF  # 가나
                or 0x4E00 <= code <= 0x9FFF):  # 한자
            cjk += 1
    other = len(text) - cjk
    return int(cjk + other / 3.5)


def _message_text(msg):
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _middle_truncate(text, keep):
    """가운데를 들어낸다. 앞(지시문)과 뒤(질문)는 남겨야 의미가 유지된다."""
    if keep >= len(text):
        return text
    head = keep * 2 // 3
    tail = keep - head
    removed = len(text) - keep
    marker = ("\n...[%d characters of table data were removed here to fit the "
              "model's context. If the answer requires a value that is not "
              "shown, say so instead of guessing.]...\n" % removed)
    return text[:head] + marker + (text[-tail:] if tail else "")


def _trim_payload(payload, notes):
    """입력이 상한을 넘으면 대화 기록과 본문을 줄인다.

    데이터 질문 경로는 테이블 메타데이터(컬럼 목록·타입·샘플 값)를 프롬프트에
    통째로 싣는다. 컬럼이 많으면 수천~수만 토큰이 되고, 그러면
      - Ollama 가 "exceeds the available context size" 로 거부하거나
      - 거부하지 않더라도 CPU 프리필이 몇 분 걸려 Analyst 가 끊는다.
    둘 다 사용자에게는 똑같이 실패로 보인다.
    """
    if MAX_PROMPT_TOKENS <= 0:
        return

    messages = payload.get("messages")

    # 1) 오래된 대화 턴부터 버린다. system 은 지시문이라 남긴다.
    if isinstance(messages, list) and MAX_HISTORY > 0:
        system = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
        rest = [m for m in messages if not (isinstance(m, dict) and m.get("role") == "system")]
        if len(rest) > MAX_HISTORY:
            notes.append("history %d -> %d" % (len(rest), MAX_HISTORY))
            payload["messages"] = system + rest[-MAX_HISTORY:]
            messages = payload["messages"]

    def total():
        if isinstance(messages, list):
            return sum(_estimate_tokens(_message_text(m))
                       for m in messages if isinstance(m, dict))
        return _estimate_tokens(payload.get("prompt"))

    before = total()
    if before <= MAX_PROMPT_TOKENS:
        return

    # 2) /api/generate 는 messages 가 없고 prompt 문자열 하나다.
    if not isinstance(messages, list):
        text = payload.get("prompt")
        if isinstance(text, str) and text:
            ratio = MAX_PROMPT_TOKENS / float(before)
            payload["prompt"] = _middle_truncate(text, max(500, int(len(text) * ratio)))
            notes.append("prompt %d -> ~%d tok" % (before, MAX_PROMPT_TOKENS))
        return

    # 3) 가장 긴 메시지부터 조금씩 깎는다. 보통 그게 테이블 메타데이터 덩어리다.
    for _ in range(40):
        if total() <= MAX_PROMPT_TOKENS:
            break
        biggest = None
        biggest_len = 0
        for msg in messages:
            if not isinstance(msg, dict) or not isinstance(msg.get("content"), str):
                continue
            if len(msg["content"]) > biggest_len:
                biggest, biggest_len = msg, len(msg["content"])
        if biggest is None or biggest_len <= 600:
            break  # 더 깎으면 질문 자체가 사라진다
        biggest["content"] = _middle_truncate(biggest["content"], int(biggest_len * 0.7))

    after = total()
    if after < before:
        notes.append("prompt %d -> %d tok" % (before, after))


def _dump(payload, state):
    """나가는 요청을 파일로 떨군다. 무엇이 프롬프트를 부풀리는지 눈으로 보려는 용도."""
    if not DUMP_DIR:
        return
    try:
        os.makedirs(DUMP_DIR, exist_ok=True)
        path = os.path.join(DUMP_DIR, "last-payload.json")
        with open(path, "w", encoding="utf-8") as fh:
            _json.dump(payload, fh, ensure_ascii=False, indent=1)
        _banner("dumped request to %s" % path)
    except Exception as exc:
        _banner("dump failed (%s: %s)" % (type(exc).__name__, exc))


def _describe(payload):
    """메시지별 크기를 로그에 남긴다. 어느 메시지가 큰지 바로 보인다."""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        text = _message_text(msg)
        _banner("  msg[%d] %-9s %6d tok  %s"
                % (i, msg.get("role", "?"), _estimate_tokens(text),
                   text[:90].replace("\n", " ")))


def _is_ollama_call(payload):
    """/api/chat, /api/generate 요청인지."""
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("model"), str)
        and ("messages" in payload or "prompt" in payload)
    )


def _tune_payload(payload, state):
    """CPU 에서 응답이 제시간에 끝나도록 옵션을 박는다.

    벤더가 이미 정한 값은 존중하고, 비어 있는 것만 채운다.
    """
    notes = []

    # 입력이 크면 자른다. 옵션을 박기 전에 해야 아래 로그의 토큰 수가 최종값이 된다.
    _trim_payload(payload, notes)

    # 모델 로드 시간을 없앤다. 이게 없으면 5분만 쉬어도 다음 요청이 가중치를
    # 디스크에서 다시 읽느라 수십 초를 버린다.
    if KEEP_ALIVE and "keep_alive" not in payload:
        payload["keep_alive"] = KEEP_ALIVE
        notes.append("keep_alive=%s" % KEEP_ALIVE)

    if NUM_PREDICT > 0 or NUM_CTX > 0:
        options = payload.get("options")
        if not isinstance(options, dict):
            options = {}
            payload["options"] = options
        # 생성 길이 상한. CPU 에서 초당 5~10 토큰이라 이게 없으면 모델이
        # 장문을 쓰기로 하는 순간 타임아웃이 확정된다.
        if NUM_PREDICT > 0 and not options.get("num_predict"):
            options["num_predict"] = NUM_PREDICT
            notes.append("num_predict=%d" % NUM_PREDICT)
        # 컨텍스트를 필요 이상으로 키우면 KV 캐시가 커져 느려진다.
        if NUM_CTX > 0 and not options.get("num_ctx"):
            options["num_ctx"] = NUM_CTX
            notes.append("num_ctx=%d" % NUM_CTX)

    # 이미지를 다 뺐으면 비전 모델을 쓸 이유가 없다. 텍스트 모델이 대개 더 빠르다.
    if TEXT_MODEL and state["dropped"] and not any(
        m.get("images") for m in payload.get("messages", []) if isinstance(m, dict)
    ):
        if payload.get("model") != TEXT_MODEL:
            notes.append("model %s -> %s" % (payload.get("model"), TEXT_MODEL))
            payload["model"] = TEXT_MODEL

    return notes


def _rewrite(payload, where):
    if not isinstance(payload, (dict, list)):
        return payload

    state = {"seen": 0, "dropped": 0}
    try:
        before = len(_json.dumps(payload))
    except Exception:
        before = -1

    try:
        _walk(payload, state)
        is_ollama = _is_ollama_call(payload)
        notes = _tune_payload(payload, state) if is_ollama else []

        if is_ollama or state["seen"] or notes:
            try:
                after = len(_json.dumps(payload))
            except Exception:
                after = -1
            parts = ["%s %d KB -> %d KB" % (where, max(before, 0) // 1024,
                                            max(after, 0) // 1024)]
            # 실제로 모델에 들어가는 입력 크기. 여기가 크면 느리거나 거부당한다.
            est = 0
            if is_ollama:
                est = sum(_estimate_tokens(_message_text(m))
                          for m in payload.get("messages", []) if isinstance(m, dict))
                est = est or _estimate_tokens(payload.get("prompt"))
                parts.append("in~%d tok" % est)
            if state["seen"]:
                parts.append("images %d (dropped %d)" % (state["seen"], state["dropped"]))
            parts.extend(notes)
            _banner(" | ".join(parts))

            # 큰 요청만 메시지별로 풀어서 보여 준다. 무엇이 프롬프트를 부풀리는지
            # 알아야 어디를 손볼지 정할 수 있다.
            if is_ollama and (DUMP_DIR or est > _DESCRIBE_OVER):
                _describe(payload)
                _dump(payload, state)
    except Exception as exc:
        # 여기서 예외를 내면 요청 자체가 죽는다. 원본을 그대로 보낸다.
        _LOG.warning("payload pass skipped (%s: %s)", type(exc).__name__, exc)
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
        _banner("NO HOOK INSTALLED - requests are going out unchanged")
        return

    detail = ["vision=%s" % MODE]
    if MODE != "off":
        detail.append("max_edge=%d" % MAX_EDGE)
        detail.append("on_error=%s" % ON_ERROR)
    if NUM_PREDICT > 0:
        detail.append("num_predict=%d" % NUM_PREDICT)
    if NUM_CTX > 0:
        detail.append("num_ctx=%d" % NUM_CTX)
    if KEEP_ALIVE:
        detail.append("keep_alive=%s" % KEEP_ALIVE)
    if TEXT_MODEL:
        detail.append("text_model=%s" % TEXT_MODEL)
    if MAX_PROMPT_TOKENS > 0:
        detail.append("max_prompt=%d tok" % MAX_PROMPT_TOKENS)
    if MAX_HISTORY > 0:
        detail.append("max_history=%d" % MAX_HISTORY)
    if DUMP_DIR:
        detail.append("dump=%s" % DUMP_DIR)
    _banner("active: %s | hooks: %s" % (", ".join(detail), ", ".join(installed)))


_ANY_WORK = MODE == "off" or MAX_EDGE > 0 or NUM_PREDICT > 0 or NUM_CTX > 0 \
    or bool(KEEP_ALIVE) or bool(TEXT_MODEL) or MAX_PROMPT_TOKENS > 0 or bool(DUMP_DIR)

if _ANY_WORK:
    try:
        _install()
    except Exception as exc:
        # orchestrator 기동을 막지 않도록 조용히 넘어간다.
        _banner("not installed (%s: %s)" % (type(exc).__name__, exc))
else:
    _banner("disabled - nothing to do (COPILOT_* 가 전부 꺼져 있음)")
