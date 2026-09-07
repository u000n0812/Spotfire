#!/usr/bin/env bash
# GPU 없는 장비용 Ollama 준비. 모델을 받고, 메모리에 올리고, 설정을 점검한다.
#
# 호스트에서 실행:  bash ollama-setup.sh
#
# 이 스크립트가 하는 일과 이유:
#
#   1) 모델을 하나로 통일한다.
#      전에는 텍스트용 qwen2.5(7B)와 비전용 qwen2.5vl(7B)을 따로 썼다. Ollama 는
#      요청이 오는 모델마다 러너(모델 프로세스)를 띄우는데, CPU 장비에서 7B 두 개를
#      번갈아 쓰면 질문할 때마다 4~5GB 를 다시 올린다. 그 시간이 추론보다 길다.
#      비전 모델은 텍스트도 처리하므로, 비전 모델 하나로 다 쓰면 교체가 사라진다.
#
#   2) 7B 대신 3B 를 쓴다.
#      CPU 에서 7B 는 프리필 초당 30~50 토큰, 생성 초당 5~8 토큰이다.
#      3B 는 대략 2.5배 빠르고 메모리는 절반이다.
#
#   3) num_ctx 를 모델에 굽지 않는다.
#      예전 ollama-bigctx.sh 는 num_ctx 를 박은 모델 변형을 만들었다. 이제는
#      sitecustomize 패치가 요청마다 num_ctx 를 실어 보내고, 모든 호출에 같은 값을
#      쓰도록 강제한다. 값이 호출마다 다르면 Ollama 가 러너를 새로 띄우기 때문이다.
#      그래서 -bigctx 변형은 더 이상 필요 없다.
#
#   4) 서버 설정을 점검한다. 아래 '호스트 환경변수' 항목 참고.

set -u
MODEL="${MODEL:-qwen2.5vl:3b}"
EMBED="${EMBED:-bge-m3}"
KEEP_ALIVE="${KEEP_ALIVE:-24h}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"

# .env 가 code/reasoning 에 별도 모델(예: qwen2.5-coder:3b 실험)을 쓰고 있으면 같이 받는다.
CODE_MODEL=""
if [ -f .env ]; then
    CODE_MODEL=$(grep -E '^OLLAMA_CODE_MODEL=' .env | head -1 | cut -d= -f2- | tr -d '\r')
fi
[ -n "$CODE_MODEL" ] && [ "$CODE_MODEL" != "$MODEL" ] && echo "code/reasoning 전용 모델 감지: $CODE_MODEL (같이 받음)"

command -v ollama >/dev/null || { echo "ollama 명령을 찾을 수 없음"; exit 1; }

echo "=== 1. 모델 받기 ==="
for m in "$MODEL" "$EMBED" $CODE_MODEL; do
    if ollama list | awk '{print $1}' | grep -qx "$m"; then
        echo "이미 있음: $m"
    else
        echo "받는 중: $m"
        ollama pull "$m" || { echo "  실패: $m - 이름과 네트워크를 확인할 것"; exit 1; }
    fi
done

echo
echo "=== 2. 메모리에 올리기 ==="
# keep_alive 는 첫 요청이 끝난 뒤부터 유지된다. 부팅 직후 첫 질문이 로딩을
# 기다리지 않도록 여기서 미리 올려 둔다.
for m in "$MODEL" "$EMBED" $CODE_MODEL; do
    printf '%-20s ' "$m"
    start=$(date +%s)
    if [ "$m" = "$EMBED" ]; then
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 600 \
            "$OLLAMA_URL/api/embed" \
            -d "{\"model\":\"$m\",\"input\":\"warmup\",\"keep_alive\":\"$KEEP_ALIVE\"}")
    else
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 600 \
            "$OLLAMA_URL/api/generate" \
            -d "{\"model\":\"$m\",\"keep_alive\":\"$KEEP_ALIVE\"}")
    fi
    secs=$(( $(date +%s) - start ))
    [ "$code" = "200" ] && echo "로드됨 (${secs}초)" || echo "실패 HTTP $code"
done

echo
echo "=== 3. 현재 상태 ==="
ollama ps

echo
echo "=== 4. 호스트 환경변수 점검 ==="
# 이 값들은 컨테이너가 아니라 'ollama serve' 가 도는 쪽에서 읽는다.
# Windows 라면 시스템 환경변수에 넣고 Ollama 를 재시작해야 적용된다.
check() {
    cur="$(printenv "$1" 2>/dev/null || true)"
    if [ "$cur" = "$2" ]; then
        echo "  OK   $1=$cur"
    else
        echo "  권장 $1=$2   (지금: ${cur:-미설정})   # $3"
    fi
}
check OLLAMA_NUM_PARALLEL 1 "동시 처리 수. 기본값은 컨텍스트를 쪼개 나눠 쓰므로 CPU 에서는 손해"
check OLLAMA_MAX_LOADED_MODELS 2 "채팅 모델 + 임베딩 모델 둘만 상주. 더 늘리면 메모리를 밀어낸다"
check OLLAMA_KEEP_ALIVE "$KEEP_ALIVE" "기본 5분이면 잠깐 쉬어도 다음 질문이 재적재부터 기다린다"

cat <<EOF

=== 5. .env 확인 ===
다섯 카테고리가 모두 같은 모델이어야 러너 교체가 없다:
  OLLAMA_FAST_MODEL=$MODEL
  OLLAMA_LARGE_MODEL=$MODEL
  OLLAMA_VISION_MODEL=$MODEL
  OLLAMA_CODE_MODEL=$MODEL
  OLLAMA_REASONING_MODEL=$MODEL

이후: docker compose up -d
EOF
