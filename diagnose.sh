#!/usr/bin/env bash
# Spotfire Copilot 백엔드 진단 스크립트.
#
# Git Bash(Windows)에서 실행:  bash diagnose.sh
# 결과 전체를 그대로 복사해서 공유하면 됨. 비밀값은 출력하지 않음.
#
# 가장 중요한 항목은 [1] — 우리가 마운트한 main.py 가 이미지 원본에서 엔드포인트를
# 지워버렸는지 확인함. 벤더 가이드는 /register-client 와 /client/token 을 이미지
# 기본 제공으로 문서화하는데, 우리는 404 가 나서 직접 추가했음. 원본에 이미 있었다면
# 404 의 원인은 이미지가 아니라 우리 교체 파일임.

set -u
ORCH_IMAGE="public.ecr.aws/tds/llm-orchestrator:latest"
BASE_URL="${BASE_URL:-http://localhost:8081}"
OUT_DIR="${OUT_DIR:-./diagnose-out}"

mkdir -p "$OUT_DIR"
# Git Bash 가 컨테이너 내부 경로를 Windows 경로로 바꾸는 것을 막음
export MSYS_NO_PATHCONV=1

hr() { echo; echo "=================== $* ==================="; }

hr "[1] 이미지 원본 main.py vs 우리가 마운트한 main.py"
if docker run --rm --entrypoint cat "$ORCH_IMAGE" /app/main.py > "$OUT_DIR/original_main.py" 2>/dev/null; then
    echo "원본 main.py 를 $OUT_DIR/original_main.py 로 추출함 ($(wc -l < "$OUT_DIR/original_main.py") 줄)"
    echo
    echo "--- 원본에 정의된 엔드포인트 ---"
    grep -nE '^@app\.(get|post|put|patch|delete)' "$OUT_DIR/original_main.py" || echo "(없음)"
    echo
    echo "--- 우리 orchestrator_main.py 에 정의된 엔드포인트 ---"
    grep -nE '^@app\.(get|post|put|patch|delete)' ./orchestrator_main.py || echo "(없음)"
    echo
    echo "--- 프론트엔드가 호출하는데 원본에 있는지 여부 ---"
    for ep in "client/token" "register-client" "agents/available" "threads"; do
        if grep -q "$ep" "$OUT_DIR/original_main.py"; then
            echo "  원본에 있음   : /$ep   <-- 우리 파일이 이걸 덮어써서 없앴을 수 있음"
        else
            echo "  원본에 없음   : /$ep"
        fi
    done
else
    echo "원본 추출 실패 (이미지를 못 받았거나 docker 미실행)"
fi

hr "[2] 내장 분류기가 쓰는 인텐트 어휘"
docker run --rm --entrypoint cat "$ORCH_IMAGE" /app/classifier.py 2>/dev/null \
    > "$OUT_DIR/classifier.py" \
    && { echo "$OUT_DIR/classifier.py 로 저장함"; grep -nE '"[A-Za-z_]{4,}"' "$OUT_DIR/classifier.py" | head -60; } \
    || echo "classifier.py 없음 - 아래 [3] 의 파일 목록에서 실제 이름 확인"

hr "[3] 이미지 /app 파일 목록"
docker run --rm --entrypoint ls "$ORCH_IMAGE" -la /app 2>/dev/null || echo "실패"

hr "[4] 벤더가 준비해 둔 프롬프트 텍스트 파일 (UserIntent.txt 등)"
docker run --rm --entrypoint sh "$ORCH_IMAGE" -c 'find / -name "*.txt" -path "*prompt*" 2>/dev/null; find /app -name "*.txt" 2>/dev/null' || echo "실패"

hr "[5] 실행 중인 컨테이너 상태"
docker compose ps 2>/dev/null || docker ps --filter name=copilot

hr "[6] Redis 에 실제로 만들어진 인덱스"
echo "--- 인덱스 목록 (prompts.py 는 User_Docs=petroleumreservoir, HowTo=spotfiredocs 를 기대함) ---"
docker exec copilot-redis redis-cli FT._LIST 2>/dev/null || echo "실패"
echo "--- 저장된 키 개수 ---"
docker exec copilot-redis redis-cli DBSIZE 2>/dev/null || echo "실패"

hr "[7] 호스트 Ollama 에 받아둔 모델"
ollama list 2>/dev/null || curl -s http://localhost:11434/api/tags || echo "Ollama 접근 실패"

hr "[8] 엔드포인트 응답 확인"
TOKEN=$(curl -s -X POST "$BASE_URL/client/token" \
    -d "grant_type=client_credentials&client_id=${COPILOT_CLIENT_ID:-spotfire}&client_secret=${COPILOT_CLIENT_SECRET:-spotfire}" \
    | sed -E 's/.*"access_token":"([^"]+)".*/\1/')
if [ -n "$TOKEN" ] && [ "${#TOKEN}" -gt 20 ]; then
    echo "/client/token : OK (토큰 발급됨)"
else
    echo "/client/token : 실패 - 이후 검사 건너뜀"
    TOKEN=""
fi
for ep in "/agents/available" "/system-prompt/user-intents"; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL$ep" -H "Authorization: Bearer $TOKEN")
    echo "GET $ep -> HTTP $code"
done
echo "--- 영문 질의 테스트 ---"
curl -s -X POST "$BASE_URL/orchestrator" \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"prompt": "test question", "user_intent": "User_Docs"}' | head -c 600
echo

hr "완료"
echo "출력물 위치: $OUT_DIR"
