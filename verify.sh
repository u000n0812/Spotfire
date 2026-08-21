#!/usr/bin/env bash
# Spotfire Copilot 동작 검증 스크립트.
#
# Git Bash(Windows)에서 실행:  bash verify.sh
#
# diagnose.sh 가 "무엇이 설치돼 있는지"를 훑는다면, 이 스크립트는 계층별로
# "실제로 동작하는지"를 PASS/FAIL 로 판정함. 아래에서 FAIL 이 처음 나오는
# 계층이 문제의 시작점이므로, 그 위 계층은 볼 필요 없음.
#
# 계층: L1 컨테이너 -> L2 Ollama -> L3 인증 -> L4 벡터검색 -> L5 RAG 답변
#       -> L6 인텐트 분류 -> L7 멀티모달
#
# 사용자 눈에 보이는 최종 확인(L8)은 Analyst 에서 수동으로 해야 함 - 맨 아래 안내 참고.

set -u
export MSYS_NO_PATHCONV=1

BASE_URL="${BASE_URL:-http://localhost:8081}"
LOADER_URL="${LOADER_URL:-http://localhost:8082}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"

PASS=0
FAIL=0
WARN=0

ok()   { echo "  [PASS] $*"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $*"; WARN=$((WARN+1)); }
hdr()  { echo; echo "--- $* ---"; }

# .env 에서 값 읽기 (없으면 기본값)
envval() {
    local key="$1" def="${2:-}"
    local v
    v=$(grep -E "^${key}=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r')
    echo "${v:-$def}"
}

# 2.3.0 은 OAUTH2_CLIENT_ID / OAUTH2_CLIENT_SECRET_HASH 를 쓰고,
# 2.0.0 시절 자체 패치는 COPILOT_CLIENT_ID / _SECRET 을 썼다. 둘 다 지원.
# secret 은 .env 에 해시로만 있으므로 평문은 COPILOT_CLIENT_SECRET 에서 가져온다.
ORCH_VER=$(docker exec copilot-orchestrator env 2>/dev/null \
    | grep '^FASTAPI_APP_VERSION=' | cut -d= -f2 | tr -d '\r')
CLIENT_ID=$(envval OAUTH2_CLIENT_ID "$(envval COPILOT_CLIENT_ID spotfire)")
CLIENT_SECRET=$(envval COPILOT_CLIENT_SECRET spotfire)

# 2.3.0 은 /orchestrator 본문에 user_id / document_id 를 요구한다(없으면 422).
# 2.0.0 은 모르는 필드를 무시하므로 양쪽 버전에서 그대로 쓸 수 있다.
REQ_IDS=',"user_id":"verify-script","document_id":"verify-doc"'
EMB_MODEL=$(envval EMBEDDING_MODEL_NAME bge-m3)
MM_MODEL=$(envval MULTI_MODAL_MODEL_NAME llava)
CHAT_MODEL=$(envval CHAT_COMPLEX_MODEL_NAME qwen2.5)
SIMPLE_MODEL=$(envval CHAT_SIMPLE_MODEL_NAME qwen2.5)


# 응답에서 답변 텍스트를 꺼낸다. 버전마다 스키마가 달라서 여러 필드를 시도한다.
#   2.0.0 : {"result":"...","sources":[...]}
#   2.3.0 : {"id":...,"status":"completed","thread_id":...,"content"/"message":...}
answer_text() {
    echo "$1" | grep -oE '"(result|content|text|answer)"[[:space:]]*:[[:space:]]*"([^"\\]|\\.)*"' \
        | head -1 | sed -E 's/^"[a-z]+"[[:space:]]*:[[:space:]]*"//; s/"$//'
}
# 요청이 처리됐는지 판정 (2.0.0 의 result, 2.3.0 의 status=completed 둘 다 인정)
answered() {
    echo "$1" | grep -qE '"result"|"status"[[:space:]]*:[[:space:]]*"completed"'
}

echo "==================================================="
echo " Spotfire Copilot 동작 검증"
echo "==================================================="

# ---------------------------------------------------------------- L1
hdr "L1. 컨테이너"
for c in copilot-redis copilot-orchestrator copilot-data-loader; do
    state=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null)
    case "$state" in
        running) ok "$c 실행 중" ;;
        "")      bad "$c 없음 (docker compose up -d 필요)" ;;
        *)       bad "$c 상태=$state (docker logs $c 확인)" ;;
    esac
done
# 재시작 반복 여부 - 크래시 루프 감지
for c in copilot-orchestrator copilot-data-loader; do
    rc=$(docker inspect -f '{{.RestartCount}}' "$c" 2>/dev/null || echo 0)
    [ "${rc:-0}" -gt 3 ] && warn "$c 재시작 ${rc}회 - 크래시 루프 의심"
done

# ---------------------------------------------------------------- L2
hdr "L2. Ollama 및 모델"
TAGS=$(curl -s --max-time 10 "$OLLAMA_URL/api/tags" 2>/dev/null)
if [ -z "$TAGS" ]; then
    bad "호스트에서 Ollama 응답 없음 ($OLLAMA_URL) - Ollama 가 실행 중인지 확인 (ollama serve)"
else
    ok "호스트에서 Ollama 응답함"
    for m in "$EMB_MODEL" "$CHAT_MODEL" "$SIMPLE_MODEL" "$MM_MODEL"; do
        base="${m%%:*}"
        if echo "$TAGS" | grep -q "\"$m\"" || echo "$TAGS" | grep -q "\"$base:"; then
            ok "모델 있음: $m"
        else
            bad "모델 없음: $m  (ollama pull $m)"
        fi
    done
fi

# 컨테이너에서의 도달 여부는 호스트 결과와 별개로 항상 확인한다.
# 호스트는 되는데 컨테이너만 안 되면 host.docker.internal 라우팅 문제이고,
# 둘 다 안 되면 Ollama 자체가 안 떠 있는 것 - 원인이 완전히 다르다.
CONTAINER_ERR=$(docker exec copilot-orchestrator python -c "
import os,urllib.request
urllib.request.urlopen(os.environ['OLLAMA_BASE_URL']+'/api/tags', timeout=10)
print('OK')
" 2>&1)
if echo "$CONTAINER_ERR" | grep -q "^OK"; then
    ok "orchestrator 컨테이너 -> Ollama 도달"
else
    bad "orchestrator 컨테이너 -> Ollama 도달 실패"
    echo "         $(echo "$CONTAINER_ERR" | tail -1)"
    if [ -n "$TAGS" ]; then
        echo "         호스트는 되는데 컨테이너만 실패 -> Docker 네트워크 문제."
        echo "         docker compose down && docker compose up -d 로 재생성해볼 것."
    else
        echo "         호스트/컨테이너 모두 실패 -> Ollama 가 실행 중이 아님."
    fi
fi

# 모델이 GPU 에 올라가는지 확인한다. CPU 가 섞이면 화면 해석이 수 분 걸려
# Analyst 쪽에서 "request took too long" 으로 끊긴다.
PS_OUT=$(ollama ps 2>/dev/null | tail -n +2)
if [ -n "$PS_OUT" ]; then
    echo "  로드된 모델:"
    echo "$PS_OUT" | sed 's/^/         /'
    if echo "$PS_OUT" | grep -qi "cpu"; then
        warn "일부 모델이 CPU 로 내려감 - VRAM 부족. num_ctx 를 낮춰 다시 만들 것:"
        echo "         NUM_CTX=8192 bash ollama-bigctx.sh"
    fi
fi

# ---------------------------------------------------------------- L3
hdr "L3. 인증"
TOKEN=$(curl -s --max-time 15 -X POST "$BASE_URL/client/token" \
    -d "grant_type=client_credentials&client_id=$CLIENT_ID&client_secret=$CLIENT_SECRET" \
    | sed -E 's/.*"access_token":"([^"]+)".*/\1/')
if [ -n "$TOKEN" ] && [ "${#TOKEN}" -gt 20 ]; then
    ok "/client/token 토큰 발급 (client_id=$CLIENT_ID)"
    AUTH="Authorization: Bearer $TOKEN"
else
    bad "/client/token 실패 - Analyst Preferences 의 Client ID/Secret 과 .env 가 일치하는지 확인"
    AUTH="Authorization: Bearer none"
fi
code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 \
    "$BASE_URL/system-prompt/user-intents" -H "$AUTH")
case "$code" in
    200)     ok "토큰으로 보호된 엔드포인트 접근 OK" ;;
    401|403) bad "보호된 엔드포인트 HTTP $code - 토큰이 거부됨(자격증명 불일치)" ;;
    404)     warn "보호된 엔드포인트 HTTP 404 - 이 버전엔 /system-prompt/user-intents 가 없음(인증 문제 아님)" ;;
    *)       bad "보호된 엔드포인트 HTTP $code" ;;
esac

# ---------------------------------------------------------------- L4
hdr "L4. 벡터 DB 적재 상태"
INDEXES=$(docker exec copilot-redis redis-cli FT._LIST 2>/dev/null | tr -d '\r')
if [ -z "$INDEXES" ]; then
    bad "Redis 인덱스가 하나도 없음 - data loader 로 문서를 적재하지 않았음"
else
    ok "인덱스 존재: $(echo "$INDEXES" | tr '\n' ' ')"
    COUNTS=""
    for idx in $INDEXES; do
        n=$(docker exec copilot-redis redis-cli FT.SEARCH "$idx" "*" LIMIT 0 0 2>/dev/null | head -1 | tr -d '\r')
        pfx=$(docker exec copilot-redis redis-cli FT.INFO "$idx" 2>/dev/null | tr -d '\r' \
              | grep -A2 -w prefixes | sed -n '2p')
        if [ "${n:-0}" -gt 0 ] 2>/dev/null; then
            ok "  $idx : 문서 ${n}건 (키 접두사: ${pfx:-?})"
        else
            bad "  $idx : 0건 (적재 실패했거나 비어 있음)"
        fi
        COUNTS="$COUNTS $n"
    done
    # 인덱스마다 건수가 똑같으면 키 접두사가 겹쳐 모두 같은 문서를 보고 있는 것.
    # RediSearch 인덱스는 "이 접두사로 시작하는 키"로 정의되므로, 접두사가 같으면
    # 인덱스를 나눠도 문서 집합이 분리되지 않는다.
    UNIQ=$(echo "$COUNTS" | tr ' ' '\n' | grep -v '^$' | sort -u | wc -l)
    TOTAL=$(echo "$COUNTS" | tr ' ' '\n' | grep -v '^$' | wc -l)
    if [ "$TOTAL" -gt 1 ] && [ "$UNIQ" -eq 1 ]; then
        bad "인덱스 ${TOTAL}개의 문서 수가 전부 동일 -> 키 접두사가 겹쳐 모든 인덱스가"
        echo "         같은 문서를 공유하고 있음. 문서 집합 분리가 안 되며, Spotfire 매뉴얼을"
        echo "         적재하면 업무 문서와 섞임. 최신 플러그인으로 재빌드 후 재적재 필요."
    fi

    # 적재 당시 임베딩 모델과 지금 검색에 쓰는 모델의 벡터 차원이 다르면
    # 오류 없이 검색 결과가 0건이 됨 -> 답변은 "문서에서 찾을 수 없습니다",
    # sources 는 빈 채로 나옴. nomic-embed-text=768, bge-m3=1024 로 서로 다르므로
    # 임베딩 모델을 바꾼 뒤 재적재하지 않았다면 여기서 걸린다.
    EMB_DIM=$(curl -s --max-time 10 "$OLLAMA_URL/api/show" \
        -d "{\"model\":\"$EMB_MODEL\"}" 2>/dev/null \
        | grep -oE '"[a-z0-9_]+\.embedding_length":[0-9]+' | head -1 | grep -oE '[0-9]+$')
    if [ -n "$EMB_DIM" ]; then
        for idx in $INDEXES; do
            IDX_DIM=$(docker exec copilot-redis redis-cli FT.INFO "$idx" 2>/dev/null \
                | tr -d '\r' | grep -A1 -wx "dim" | sed -n '2p')
            [ -z "$IDX_DIM" ] && continue
            if [ "$IDX_DIM" = "$EMB_DIM" ]; then
                ok "  $idx : 벡터 차원 $IDX_DIM = $EMB_MODEL($EMB_DIM) 일치"
            else
                bad "  $idx : 벡터 차원 $IDX_DIM 인데 현재 임베딩 $EMB_MODEL 은 $EMB_DIM"
                echo "         차원이 다르면 검색이 오류 없이 0건이 됨 -> 이 인덱스는 재적재 필요."
            fi
        done
    fi

fi
# prompts.py 가 기대하는 이름과 실제 이름이 맞는지
USER_IDX=$(envval COPILOT_USER_DOCS_INDEX petroleumreservoir)
if echo "$INDEXES" | grep -qx "$USER_IDX"; then
    ok "User_Docs 가 찾을 인덱스 '$USER_IDX' 존재"

    # 검색에 쓸 키 접두사가 실제 저장된 접두사와 다르면 오류 없이 0건이 된다.
    # (REDIS_KEY_PREFIX 를 비우면 코드가 인덱스 이름을 접두사로 계산함)
    WANT_PREFIX=$(envval REDIS_KEY_PREFIX "$USER_IDX")
    REAL_PREFIX=$(docker exec copilot-redis redis-cli FT.INFO "$USER_IDX" 2>/dev/null \
        | tr -d '\r' | grep -A2 -w prefixes | sed -n '2p')
    if [ -n "$REAL_PREFIX" ] && [ "$WANT_PREFIX" != "$REAL_PREFIX" ]; then
        bad "키 접두사 불일치: 검색은 '$WANT_PREFIX' 로 찾는데 저장된 키는 '$REAL_PREFIX'"
        echo "         이러면 오류 없이 검색 결과가 0건이 됨 -> .env 에 REDIS_KEY_PREFIX=$REAL_PREFIX"
    else
        ok "키 접두사 일치: '$WANT_PREFIX'"
    fi

    # 실제 저장된 문서의 필드 구성을 확인한다. redis_schema.yml 은 content/source/
    # page/content_vector 를 선언하는데, 적재 시 스키마가 달랐다면 검색이 0건이 되거나
    # sources 를 채울 메타데이터(source/page)가 아예 없다.
    SAMPLE_KEY=$(docker exec copilot-redis redis-cli \
        FT.SEARCH "$USER_IDX" "*" LIMIT 0 1 RETURN 0 2>/dev/null | tr -d '\r' | sed -n '2p')
    if [ -n "$SAMPLE_KEY" ]; then
        FIELDS=$(docker exec copilot-redis redis-cli --raw HKEYS "$SAMPLE_KEY" 2>/dev/null | tr '\n' ' ')
        ok "저장된 문서 예시: $SAMPLE_KEY"
        echo "         필드: $FIELDS"
        for want in content source page content_vector; do
            echo "$FIELDS" | grep -qw "$want" \
                || bad "  문서에 '$want' 필드가 없음 - redis_schema.yml 과 적재 결과가 다름"
        done
    fi
else
    bad "User_Docs 가 찾을 인덱스 '$USER_IDX' 없음 -> .env 에 COPILOT_USER_DOCS_INDEX 로 실제 이름 지정 필요"
fi

# ---------------------------------------------------------------- L5
hdr "L5. RAG 답변 (문서 기반 질의)"
# 문서 4994건에 대한 RAG 는 로컬 모델에서 수 분이 걸릴 수 있고, sources 를 채우려고
# 검색을 한 번 더 돌기까지 함. 타임아웃과 "빈 응답" 은 원인이 전혀 다르므로 구분한다.
RAG_START=$(date +%s)
RESP=$(curl -s --max-time 600 -X POST "$BASE_URL/orchestrator" \
    -H "$AUTH" -H "Content-Type: application/json" \
    --data-binary "{\"prompt\":\"What is this document about?\",\"user_intent\":\"User_Docs\"$REQ_IDS}")
CURL_RC=$?
RAG_SECS=$(( $(date +%s) - RAG_START ))
if [ "$CURL_RC" = "28" ]; then
    bad "RAG 응답 타임아웃 (${RAG_SECS}초 초과) - 오류가 아니라 너무 느린 것"
    echo "         문서 4994건 + topk 10 + num_ctx 16384 조합이면 로컬 모델에선 느릴 수 있음."
    echo "         index_topk 를 줄이거나 더 작은 chat 모델을 쓰면 빨라짐."
elif [ "$CURL_RC" != "0" ]; then
    bad "RAG 요청 실패 (curl exit=$CURL_RC)"
elif answered "$RESP"; then
    ok "응답 수신 (${RAG_SECS}초)"
fi
if answered "$RESP"; then
    RESULT=$(answer_text "$RESP" | head -c 200)
    if [ -n "$RESULT" ]; then
        ok "답변 생성됨: ${RESULT:0:120}..."
    else
        warn "답변 텍스트를 못 찾음 - 스키마가 다를 수 있음: $(echo "$RESP" | head -c 200)"
    fi
    if ! echo "$RESP" | grep -q '"sources"'; then
        : # 2.3.0 응답에는 sources 필드가 없다 - 이 버전에선 판정 대상 아님
    elif echo "$RESP" | grep -q '"sources":\[\]'; then
        warn "sources 가 비어 있음 - 검색이 0건이거나 메타데이터(source/page) 누락"
        # 출처 수집 패치는 실패해도 WARNING 으로만 남아 "오류 없음" 검사에 안 걸린다.
        # 검색이 왜 0건인지는 여기에 그대로 찍히므로 반드시 확인해야 함.
        SRC_ERR=$(docker logs --tail 400 copilot-orchestrator 2>&1 \
            | grep "Failed to build sources" | tail -2)
        if [ -n "$SRC_ERR" ]; then
            echo "         검색 실패 원인:"
            echo "$SRC_ERR" | sed 's/^/           /'
        else
            echo "         검색 자체는 오류 없이 0건 반환 (질의와 문서가 안 맞거나 인덱스가 빈 것)"
        fi
    else
        ok "sources 채워짐 (출처 추적 동작)"
    fi
else
    bad "응답이 처리되지 않음: $(echo "$RESP" | head -c 200)"
fi

# ---------------------------------------------------------------- L6
hdr "L6. 인텐트 분류 (Analyst 에러의 직접 원인 지점)"
CLS=$(curl -s --max-time 120 -X POST "$BASE_URL/orchestrator" \
    -H "$AUTH" -H "Content-Type: application/json" \
    --data-binary "{\"prompt\":\"Classify this question: how many rows are in the table\",\"request_tag\":\"UserIntent\"$REQ_IDS}")
LABEL=$(answer_text "$CLS")
if [ -n "$LABEL" ]; then
    ok "분류 라벨 반환: '$LABEL'"
    echo "         -> Analyst 가 이 라벨을 거부하면 'Error determining intent' 가 뜸."
    echo "            거부되면 .env 의 COPILOT_INTENT_LABELS 로 이름을 교정해야 함."
    # 라벨에 공백/문장이 섞이면 정해진 라벨이 아니라 자유형식 답변이 온 것.
    # 다만 2.0.0 과 2.3.x 는 분류 방식이 다르다. 2.3.x 에는 스레드/에이전트 개념이
    # 있어 프론트엔드가 이 경로로 분류하지 않을 수 있으므로, 여기서 자유형식이
    # 나와도 Analyst 가 실패한다고 단정할 수 없다. 판정은 Analyst 에서 해야 함.
    case "$LABEL" in
        *" "*)
            case "$ORCH_VER" in
                2.0.*|"") bad "라벨에 공백 포함 - 자유형식 문장이 반환됨 (분류 실패)" ;;
                *)        warn "자유형식 문장이 반환됨. ${ORCH_VER} 에서는 프론트엔드가"
                          echo "         이 경로로 분류하지 않을 수 있으므로 Analyst 에서 직접 확인할 것"
                          echo "         (L8-4 데이터 질문에서 'Error determining intent' 가 뜨는지)" ;;
            esac ;;
    esac
elif answered "$CLS"; then
    warn "분류는 처리됐으나 라벨을 못 읽음: $(echo "$CLS" | head -c 200)"
else
    bad "분류 응답 이상: $(echo "$CLS" | head -c 200)"
fi

# ---------------------------------------------------------------- L7
hdr "L7. 멀티모달 (대시보드 화면 읽기)"
case "$MM_MODEL" in
    llava*) warn "MULTI_MODAL_MODEL_NAME=$MM_MODEL - 대시보드의 축/범례/숫자 판독에 부적합. qwen2.5vl:7b 권장" ;;
    *)      ok "멀티모달 모델: $MM_MODEL" ;;
esac
# 1x1 PNG 를 실어 vision 경로가 살아 있는지만 확인 (내용 판독이 아니라 경로 검증)
PNG1PX="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
# 화면 해석이 Analyst 에서 타임아웃으로 실패하는 일이 잦아, 얼마나 걸리는지 잰다.
# 1x1 PNG 로도 느리면 모델/하드웨어 문제이고, 여기선 빠른데 Analyst 에서만
# 느리면 실제 4K 스크린샷 크기가 원인이다.
VIS_START=$(date +%s)
VIS=$(curl -s --max-time 300 -X POST "$BASE_URL/orchestrator" \
    -H "$AUTH" -H "Content-Type: application/json" \
    --data-binary "{\"prompt\":\"Describe this image.\",\"user_intent\":\"InterpretPageData\",\"image\":\"$PNG1PX\"$REQ_IDS}")
VIS_SECS=$(( $(date +%s) - VIS_START ))
if answered "$VIS"; then
    ok "이미지 포함 요청이 오류 없이 처리됨 (${VIS_SECS}초, vision 경로 살아 있음)"
    if [ "$VIS_SECS" -gt 30 ]; then
        warn "1x1 PNG 한 장에 ${VIS_SECS}초 - 실제 4K 스크린샷은 훨씬 오래 걸려 Analyst 가 타임아웃함"
        echo "         ollama ps 로 GPU 로 도는지 확인하고, CPU 가 섞이면 num_ctx 를 낮출 것"
    fi
else
    bad "이미지 요청 실패: $(echo "$VIS" | head -c 200)"
fi
NUM_CTX=$(envval CHAT_NUM_CTX 8192)
[ "${NUM_CTX:-8192}" -lt 16384 ] 2>/dev/null && \
    warn "CHAT_NUM_CTX=$NUM_CTX - 스크린샷은 토큰을 많이 먹음. 16384 권장"

# 이미지 축소 패치(sitecustomize)가 실제로 로드됐는지.
# 로드 안 되면 4K 스크린샷이 그대로 나가 Analyst 가 타임아웃한다.
PATCH_LOG=$(docker logs copilot-orchestrator 2>&1 | grep "copilot-image-patch" | tail -5)
if [ -z "$PATCH_LOG" ]; then
    bad "이미지 패치가 로드되지 않음 - sitecustomize 가 임포트되지 않았음"
    echo "         확인: docker exec copilot-orchestrator cat /opt/copilot-patch/sitecustomize.py | head -1"
    echo "         확인: docker exec copilot-orchestrator printenv PYTHONPATH   (=/opt/copilot-patch 여야 함)"
elif echo "$PATCH_LOG" | grep -q "NO HOOK INSTALLED"; then
    bad "이미지 패치는 로드됐으나 후킹 실패 - requests/httpx 를 찾지 못함"
    echo "$PATCH_LOG" | sed 's/^/         /'
else
    ok "이미지 패치 로드됨: $(docker logs copilot-orchestrator 2>&1 | grep -m1 "active:" | sed 's/.*active: //')"
    HANDLED=$(docker logs copilot-orchestrator 2>&1 | grep -c "images [0-9]")
    if [ "${HANDLED:-0}" -gt 0 ]; then
        ok "  이미지가 실제로 처리됨 (${HANDLED}회)"
        docker logs copilot-orchestrator 2>&1 | grep "images [0-9]" | tail -2 | sed 's/^/         /'
    else
        warn "  아직 처리된 이미지가 없음 - Analyst 에서 Explain Page 를 한 번 실행한 뒤 다시 확인"
    fi
    docker logs copilot-orchestrator 2>&1 | grep "image shrink failed" | tail -2 | sed 's/^/         /'
fi
VIS_MODE=$(envval COPILOT_VISION_MODE shrink)
[ "$VIS_MODE" = "off" ] && \
    warn "COPILOT_VISION_MODE=off - 스크린샷을 보내지 않고 시각화 메타데이터만으로 답함 (GPU 없는 환경용)"

# 모델이 메모리에 올라가 있는지. 내려가 있으면 다음 질문이 로딩부터 기다린다.
if command -v ollama >/dev/null 2>&1; then
    LOADED=$(ollama ps 2>/dev/null | tail -n +2 | grep -c .)
    if [ "${LOADED:-0}" -eq 0 ]; then
        warn "메모리에 올라온 모델 없음 - 다음 질문이 모델 로딩부터 기다림 (bash ollama-setup.sh)"
    else
        ok "모델이 메모리에 올라와 있음 (${LOADED}개)"
        ollama ps 2>/dev/null | grep -qi "100% CPU" \
            && warn "  100% CPU 로 실행 중 - GPU 미사용. 해상도(COPILOT_IMAGE_MAX_EDGE)를 낮게 유지할 것"
    fi
fi

# 다섯 카테고리가 서로 다른 모델을 가리키면 질문 하나에 여러 러너가 뜬다.
# CPU 장비에서는 그 재적재 시간이 추론보다 길다.
CATS="FAST LARGE VISION CODE REASONING"
CAT_MODELS=""
for c in $CATS; do
    v=$(envval "OLLAMA_${c}_MODEL" "")
    [ -n "$v" ] && CAT_MODELS="$CAT_MODELS $v"
done
UNIQ=$(echo "$CAT_MODELS" | tr ' ' '\n' | grep -c . 2>/dev/null)
DISTINCT=$(echo "$CAT_MODELS" | tr ' ' '\n' | grep . | sort -u | tr '\n' ' ')
DISTINCT_N=$(echo "$DISTINCT" | tr ' ' '\n' | grep -c .)
if [ "${UNIQ:-0}" -lt 5 ]; then
    bad "모델 카테고리가 5개 미만으로 설정됨 - 빠진 카테고리는 벤더 폴백(llama3.2:3b)으로 가서 404"
elif [ "${DISTINCT_N:-0}" -eq 1 ]; then
    ok "다섯 카테고리가 모두 같은 모델: $DISTINCT (러너 교체 없음)"
else
    warn "카테고리가 서로 다른 모델을 가리킴: $DISTINCT"
    echo "         질문 하나가 여러 번 호출되며 모델을 번갈아 올린다. 하나로 통일 권장 (bash ollama-setup.sh)"
fi

# ---------------------------------------------------------------- 최근 오류
hdr "최근 orchestrator 오류 로그"
ERRS=$(docker logs --tail 300 copilot-orchestrator 2>&1 | grep -iE "error|exception|traceback|failed" | grep -viE "langsmith|LangSmith" | tail -10)
[ -n "$ERRS" ] && { echo "$ERRS"; warn "위 오류 확인 필요"; } || ok "최근 로그에 눈에 띄는 오류 없음 (LangSmith 경고 제외)"

# ---------------------------------------------------------------- 요약
echo
echo "==================================================="
echo " 결과:  PASS=$PASS   FAIL=$FAIL   WARN=$WARN"
echo "==================================================="
if [ "$FAIL" -gt 0 ]; then
    echo " 위에서 [FAIL] 이 처음 나온 계층이 문제의 시작점임."
else
    echo " 백엔드 계층은 모두 통과. 남은 것은 Analyst 쪽 수동 확인(L8)."
fi
cat <<'EOF'

--- L8. Analyst 에서 직접 확인 (자동화 불가) ---
아래 5가지를 순서대로 해보고, 각각 성공/실패를 기록하면 어디까지 되는지 확정됨.

  1) 패널이 열리는가            : Copilot 패널 자체가 뜨는지
  2) 사용법 질문                : "How do I create a bar chart?"
                                  -> HowTo 경로. spotfiredocs 인덱스 필요
  3) 문서 질문                  : 적재한 PDF 내용을 묻기
                                  -> User_Docs 경로. 답변에 출처가 표시되는지도 확인
  4) 데이터 질문                : 로드된 테이블의 특정 값 묻기
                                  -> Specific_Data_Question 경로. 지금 실패하는 지점
  5) 화면 해석                  : "Explain the current page"
                                  -> InterpretPageData 경로. 멀티모달 필요

  실패 시 함께 확인:
    docker logs -f copilot-orchestrator | grep -E "Classified question|user_intent|image="
  image=None 이면 Analyst 가 스크린샷을 안 보내는 것이고,
  Classified question 의 라벨이 거부되면 인텐트 이름 불일치임.
EOF
