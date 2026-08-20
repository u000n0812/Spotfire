#!/usr/bin/env bash
# 모델을 메모리에 미리 올려 두고 붙잡아 둔다.
#
# 호스트에서 실행:  bash ollama-preload.sh
#
# 왜 필요한가:
#   Ollama 는 마지막 요청 후 5분이 지나면 모델을 메모리에서 내린다. 그러면 다음
#   질문에서 7B 가중치를 디스크에서 다시 읽는데, GPU 없는 장비에서 이게 10~60초다.
#   Analyst 가 "The request took too long to complete" 를 뱉는 원인 중 하나가
#   실제 추론이 아니라 이 로딩 시간이다.
#
#   sitecustomize 패치가 요청마다 keep_alive 를 실어 주지만, 그건 "첫 요청이
#   끝난 뒤부터" 유지된다는 뜻이다. 컴퓨터를 켜고 처음 던지는 질문은 여전히
#   로딩을 기다린다. 이 스크립트는 그 첫 로딩을 미리 끝내 둔다.
#
#   Ollama 를 재시작했거나 부팅 직후라면 Spotfire 를 열기 전에 한 번 돌릴 것.

set -u
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
KEEP_ALIVE="${KEEP_ALIVE:-2h}"

# .env 의 OLLAMA_*_MODEL 에서 실제 쓰는 모델 이름을 뽑는다(중복 제거).
if [ -f .env ]; then
    MODELS=$(grep -E '^OLLAMA_[A-Z]+_MODEL=' .env | cut -d= -f2- | tr -d '\r' | sort -u)
else
    MODELS=""
fi
[ -z "$MODELS" ] && MODELS="qwen2.5vl-bigctx qwen2.5-bigctx"

for m in $MODELS; do
    [ -z "$m" ] && continue
    printf '%-24s ' "$m"
    start=$(date +%s)
    # prompt 를 비워 보내면 Ollama 는 로드만 하고 생성은 하지 않는다.
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 600 \
        "$OLLAMA_URL/api/generate" \
        -d "{\"model\":\"$m\",\"keep_alive\":\"$KEEP_ALIVE\"}")
    secs=$(( $(date +%s) - start ))
    if [ "$code" = "200" ]; then
        echo "로드됨 (${secs}초, ${KEEP_ALIVE} 유지)"
    else
        echo "실패 HTTP $code - 모델 이름 확인 (ollama list)"
    fi
done

echo
echo "=== 현재 메모리에 올라온 모델 ==="
ollama ps 2>/dev/null || curl -s "$OLLAMA_URL/api/ps"
echo
echo "PROCESSOR 열이 100% CPU 면 GPU 를 못 쓰고 있는 것이고,"
echo "그 상태에서는 화면(이미지) 판독이 Analyst 대기 시간을 넘긴다."
echo ".env 의 COPILOT_VISION_MODE=off 가 그 경우를 위한 설정이다."
