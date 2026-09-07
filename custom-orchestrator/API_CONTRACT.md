# API 계약 — Spotfire Analyst ↔ Copilot 백엔드

이 문서는 자체 orchestrator가 구현해야 할 계약을 정리한다. 항목마다 확인 상태를 표시한다.

- ✅ **확인됨** — 실제로 테스트했거나 소스에서 직접 읽은 내용. 근거를 같이 적는다.
- ❓ **미확인** — 지금까지의 조사로는 추정만 가능하다. Phase 1에서 반드시 밝혀야 한다.

버전 표기가 없으면 orchestrator **2.3.0** 기준이다 (`ORCHESTRATOR_TAG=2.3.0`, 현재 운영 버전).

## 1. 인증

### 1-1. Analyst 프론트엔드용 (client credentials)

✅ **엔드포인트**: `POST /client/token`
근거: `verify.sh` L3 테스트가 이 경로로 토큰을 발급받아 이후 모든 요청에 사용하고,
2.3.0에서 `PASS`로 통과함(2.0.0 시절 우리가 만든 `orchestrator_main.py`의 동명 패치와는
별개로, 2.3.0은 이 패치가 비활성 상태인데도 통과했으므로 벤더 이미지가 네이티브로
같은 경로를 제공하는 것으로 보인다).

```
POST /client/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id=<OAUTH2_CLIENT_ID>&client_secret=<평문>
```

응답: `{"access_token": "...", "token_type": "bearer"}`

❓ **미확인**: 이 네이티브 엔드포인트가 정말 `OAUTH2_CLIENT_ID`/`OAUTH2_CLIENT_SECRET_HASH`로
검증하는지, 아니면 다른 메커니즘인지는 소스를 직접 못 봐서 추정이다. `/openapi.json`의
`securitySchemes`로 흐름(flow) 종류까지는 확인 가능하지만 서버 쪽 검증 로직까지는
스키마만으로 알 수 없다.

### 1-2. 관리자용 (Swagger 문서 열람 등)

✅ **엔드포인트**: `POST /auth/jwt/login` (fastapi-users)
근거: 이번 세션에서 직접 curl로 200 OK 확인.

```
POST /auth/jwt/login
Content-Type: application/x-www-form-urlencoded

grant_type=password&username=<이메일>&password=<평문>
```

**주의**: 필드명이 `username`이지만 실제로 조회하는 값은 `users.email`이다 (fastapi-users의
기본 동작). `users.username` 컬럼("admin")은 화면 표시용일 뿐 로그인에 안 쓰인다.
계정을 못 찾은 경우와 비밀번호가 틀린 경우 모두 `{"detail":"LOGIN_BAD_CREDENTIALS"}`로
동일하게 응답해서 원인 구분이 안 된다.

Analyst ↔ Copilot 채팅 자체는 이 경로를 안 쓴다. 자체 서비스가 관리자 콘솔을
따로 만들 계획이 없다면 Phase 2~3에서는 구현 안 해도 된다.

## 2. 메인 채팅 엔드포인트

✅ **엔드포인트**: `POST /orchestrator`
근거: 실제 운영 중 계속 사용, `verify.sh` 전 구간.

### 요청 (확인된 필드)

| 필드 | 타입 | 비고 |
|---|---|---|
| `prompt` | string | 사용자 질문 |
| `user_intent` | string | 아래 3장 참고 |
| `image` | string, optional | base64. `data:image/png;base64,...` 형태와 순수 base64 둘 다 관찰됨 |
| `user_id` | string | 2.3.0 부터 필수 — 없으면 422 |
| `document_id` | string | 2.3.0 부터 필수 — 없으면 422 |

❓ **미확인**: 데이터 질문(값 조회, outlier 분석)에 필요한 **실제 테이블 데이터가 이 요청에
어떻게 실리는지**를 아직 못 밝혔다. 스크린샷·시각화 메타데이터는 확인됐지만, SQL 생성이
참조하는 스키마/행 데이터가 (a) 이 요청 본문에 통째로 포함되는지, (b) orchestrator가
별도로 Spotfire 데이터에 접근하는 경로가 있는지 확인이 안 됐다. **Phase 3 착수 전 반드시
확인해야 하는 항목** — `docker logs`로 실제 `/orchestrator` 요청 바디를 캡처해서 확인할 것.

### 응답

✅ 2.0.0(우리 패치)은 `{"result": "..."}`.
✅ 2.3.0은 `"status": "completed"`와 함께 `content`/`text`/`answer` 중 하나의 키에 본문이 실림
(근거: `verify.sh`의 `answered()`/`answer_text()`가 이 형태를 전제로 파싱하고 실제로 통과함).
❓ 정확히 어떤 키인지, 스트리밍 여부, 에러 시 스키마는 미확인 — `/openapi.json`의 응답
스키마 또는 실제 캡처로 확정할 것.

✅ RAG 응답은 `sources` 필드에 검색된 문서 출처가 채워짐 (우리가 `redis_retriever_plugin.py`
패치로 만들어 낸 동작 — 벤더 네이티브 동작인지 우리 패치 없이도 되는지는 미확인).

## 3. `user_intent` 값

✅ 관찰/확인된 값:

| 값 | 용도 |
|---|---|
| `User_Docs` | 적재된 문서 기반 질의응답 (RAG) |
| `Specific_Data_Question` / `Agent_Specific_Data_Question` | 데이터 값 조회 — SQL 생성 경로로 라우팅 |
| `InterpretPageData` / `Agent_InterpretPageData` | 화면 설명 (Explain Page) |
| `Interpret_Visual_Data` / `Agent_Interpret_Visual_Data` | 특정 시각화 설명 |
| `ImageAnalysis` | 이미지가 포함된 요청을 비전 모델로 보낼 때 내부적으로 쓰던 값 (2.0.0 패치 기준) |
| `HowTo` | Spotfire 사용법 질문 → `spotfiredocs` 인덱스 (현재 미적재, 항상 빈 답변) |
| `GeneralHelp` | 내장 분류기가 다른 인텐트에 안 맞을 때 붙이는 폴백 |

❓ 이 목록이 2.3.0 기준으로 전부인지 확실하지 않다. 내장 분류기(`classifier.py` 계열,
이미지 안에 있음)가 실제로 만들어내는 전체 어휘는 `diagnose.sh`의 2.0.0 대상 조사로만
확인했고 2.3.0은 아직 안 봤다.

## 4. 부가 엔드포인트

✅ `GET /agents/available`, `GET /threads` — Analyst 프론트엔드가 패널을 초기화할 때 호출.
2.0.0에는 없어서 404가 났고(그래서 처음엔 우리가 직접 만들어 넣었다), 2.3.0에는
네이티브로 존재.

❓ 정확한 응답 스키마(threads 목록 구조, agents 목록 구조)는 미확인 — Analyst가 이걸로
뭘 렌더링하는지도 아직 안 봤다.

## 5. 완전히 미확인인 영역

- **시각화 생성/수정**: 요청 스키마 자체를 못 봤다. 벤더 프롬프트도 플레이스홀더 상태로
  확인됐다 (Phase 4, 기획안에서 보류 처리).
- **데이터 접근 경로**: 위 2장 참고. Phase 3의 전제 조건.
- **`/openapi.json`의 전체 스키마**: 지금까지는 필요한 부분만 그때그때 확인했다. Phase 1
  착수 시 전체를 한 번에 받아서 (`README.md`의 절차 참고) 이 문서와 대조해야 한다.
