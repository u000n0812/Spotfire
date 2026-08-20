#!/usr/bin/env bash
# 컨텍스트를 키운 Ollama 모델 변형을 만든다.
#
# 호스트에서 실행:  bash ollama-bigctx.sh
#
# 왜 필요한가:
#   Spotfire 가 보내는 페이지 스크린샷은 4K 라 4700 토큰을 넘는데, Ollama 의
#   기본 컨텍스트는 4096 이라 화면 해석 요청이 이렇게 실패한다:
#     request (4779 tokens) exceeds the available context size (4096 tokens)
#
#   orchestrator 2.3.0 은 plugins/models/ollama_enhanced.py 를 쓰는데 이 플러그인은
#   num_ctx 를 지정하지 않고, 우리 CHAT_NUM_CTX 패치는 예전 경로(plugins/models/
#   ollama.py)에 마운트돼 있어 더 이상 로드되지 않는다. 그래서 모델 자체에
#   num_ctx 를 박아 둔다. 이 방식은 orchestrator 를 올려도 그대로 유지된다.
#
# 크기 정하기:
#   Explain Page 요청 하나에 들어가는 것 = 4K 스크린샷 + 프론트엔드가 보내는 긴
#   시스템 프롬프트(시각화 메타데이터 + 조작 JSON 규격) + 대화 히스토리(6턴).
#   16384 로도 모자라는 경우가 있어 기본값을 32768 로 둔다.
#   qwen2.5vl:7b 의 최대 컨텍스트는 128k 라 여유는 충분하다.
#
# 주의: 컨텍스트를 키우면 KV 캐시가 커져 VRAM 사용량이 늘어난다.
#   7B 모델 기준 대략 - 가중치 약 6GB + 컨텍스트당 캐시:
#     8192 -> +1GB 정도,  16384 -> +2GB 정도,  32768 -> +4GB 정도
#   VRAM 이 모자라면 Ollama 가 시스템 RAM 으로 흘려 아주 느려진다.
#   그럴 때는 NUM_CTX=16384 나 8192 로 낮춰 다시 만들 것:
#     NUM_CTX=16384 bash ollama-bigctx.sh

set -u
NUM_CTX="${NUM_CTX:-32768}"

# 원본 -> 새 이름
MODELS="qwen2.5vl:7b=qwen2.5vl-bigctx qwen2.5=qwen2.5-bigctx"

command -v ollama >/dev/null || { echo "ollama 명령을 찾을 수 없음"; exit 1; }

for pair in $MODELS; do
    src="${pair%%=*}"
    dst="${pair##*=}"

    if ! ollama list | grep -q "^${src%%:*}"; then
        echo "건너뜀: $src 이 없음 (ollama pull $src)"
        continue
    fi

    echo "=== $src -> $dst (num_ctx=$NUM_CTX) ==="
    tmp="$(mktemp)"
    # 원본 모델의 정의를 그대로 가져와 num_ctx 만 덧붙인다.
    # FROM 이 로컬 blob 경로로 나오는 경우가 있어 모델 이름으로 바꿔 준다.
    ollama show "$src" --modelfile 2>/dev/null \
        | sed -E "s|^FROM .*|FROM $src|" > "$tmp"
    if [ ! -s "$tmp" ]; then
        echo "  modelfile 을 가져오지 못함 - 최소 정의로 생성"
        printf 'FROM %s\n' "$src" > "$tmp"
    fi
    printf '\nPARAMETER num_ctx %s\n' "$NUM_CTX" >> "$tmp"

    ollama create "$dst" -f "$tmp" && echo "  생성됨: $dst"
    rm -f "$tmp"
done

echo
echo "=== 확인 ==="
ollama list | grep bigctx || echo "생성된 모델 없음"
echo
echo "다음으로 .env 를 아래처럼 바꾸고 docker compose up -d 를 실행할 것:"
echo "  OLLAMA_VISION_MODEL=qwen2.5vl-bigctx"
echo "  OLLAMA_FAST_MODEL=qwen2.5-bigctx"
echo "  OLLAMA_LARGE_MODEL=qwen2.5-bigctx"
echo "  OLLAMA_CODE_MODEL=qwen2.5-bigctx"
echo "  OLLAMA_REASONING_MODEL=qwen2.5-bigctx"
