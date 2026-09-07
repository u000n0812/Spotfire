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

❓ **부분 확인**: 데이터 질문(값 조회, outlier 분석)에 필요한 **실제 테이블 데이터가 이 요청에
어떻게 실리는지**는 아직 절반만 밝혔다. 아래 3-1의 SQL 생성·재시도 루프 자체는 확인됐지만
(모델이 실제로 `[DoctorNames].[HospitalName]` 같은 실제 컬럼명으로 SQL을 쓴 걸 보면 스키마는
분명히 어딘가에서 모델에 전달된다), **최초 SQL 생성 요청**(스키마가 처음 실리는 순간)은
아직 캡처하지 못했다 — 지금까지 잡은 건 실패 후 재시도 요청과 에러 요약 요청뿐이다.
**Phase 3 착수 전 반드시 캡처할 것**: `COPILOT_DUMP_DIR`이 재시도/요약 요청에 덮어써지기
전에 첫 시도만 남기도록 `sitecustomize_image_shrink.py`의 `_dump()`를 일시적으로 넘버링
저장으로 바꾸거나, `docker logs`를 실시간으로 걸어두고 데이터 질문을 던질 것.

### 응답

✅ 2.0.0(우리 패치)은 `{"result": "..."}`.
✅ 2.3.0은 `"status": "completed"`와 함께 `content`/`text`/`answer` 중 하나의 키에 본문이 실림
(근거: `verify.sh`의 `answered()`/`answer_text()`가 이 형태를 전제로 파싱하고 실제로 통과함).
❓ 정확히 어떤 키인지, 스트리밍 여부, 에러 시 스키마는 미확인 — `/openapi.json`의 응답
스키마 또는 실제 캡처로 확정할 것.

✅ RAG 응답은 `sources` 필드에 검색된 문서 출처가 채워짐 (우리가 `redis_retriever_plugin.py`
패치로 만들어 낸 동작 — 벤더 네이티브 동작인지 우리 패치 없이도 되는지는 미확인).

### 2-1. SQL 생성·재시도 루프 (`Specific_Data_Question`)

✅ **모델 카테고리는 `large`다.** `code`/`reasoning`이 아니다.
근거: 실제 요청 로그를 캡처해서 `model_category='large'`를 직접 확인함
(처음엔 `code`/`reasoning`일 것으로 추정하고 그쪽을 바꿨는데 틀렸다 — 실측으로 정정).

✅ **벤더가 자체 재시도 루프를 갖고 있다.** 생성된 SQL이 유효하지 않으면 정확한 에러
메시지와 직전 SQL을 모델에게 다시 보여주고 "이번이 N번째 시도, 완전히 다른 구조로
다시 써라"라고 명시적으로 지시하며 재생성을 시킨다. `request_tag` 값으로 구분됨:
`SpecificDataQuestionRetry`(재시도), `SqlErrorSummary`(전부 실패했을 때 사용자에게 보여줄
사과 메시지 생성 — 이건 `fast` 카테고리로 감).

✅ **Spotfire SQL은 독자 방언이다.** 표준 SQL이 아니다. 재시도 프롬프트 안에 전체 규칙과
함수 목록이 들어있고, 확인된 주요 제약:
  - 세미콜론(`;`)으로 끝내면 `unexpected char: ';'` 에러 (벤더의 12개 규칙에는 이 규칙이
    빠져 있어서 표준 SQL 습관대로 계속 세미콜론을 붙이는 실패가 관찰됨 — `sitecustomize`
    패치에서 규칙을 주입해 대응함)
  - `HAVING`, `BETWEEN`, `UNION`, `LIKE`, `PARTITION BY` 사용 불가
  - `LIMIT` 대신 `TOP(n)`
  - 상관 서브쿼리(correlated subquery) 불가 — JOIN으로 대체해야 함
  - 식별자는 항상 대괄호: `[TableName].[ColumnName]`
  - 타입 변환은 SQL `CAST`가 아니라 Spotfire 함수(`Integer()`, `Real()`, `ParseDate()` 등)

✅ **모델의 학습 언어가 결과를 좌우한다 — 크기보다 중요할 수 있다.** `qwen2.5-coder:3b`로
바꿔서 세미콜론 문제는 사라졌지만, 대신 한글 식별자(`의사명`, `병원`)를 의미가 비슷한
한자(`医生名`, `医院`)로 바꿔 쓰는 새 실패가 나타났다 — 스키마의 리터럴 이름을 그대로
못 쓰고 "번역"해버림. 대괄호 규칙도 안 지켰다. code 특화 모델이 중국어/영어 코드 위주로
튜닝된 결과로 보인다. `qwen2.5vl`을 애초에 선택한 이유가 "llama3.1/llava보다 한국어가
우수"였던 것과 정확히 대비된다 — 그래서 `large`도 `qwen2.5vl:3b`로 되돌렸다.
**한글 스키마를 다루는 한 코드 특화 모델 계열은 후보에서 제외.**

⚠️ **Analyst 클라이언트의 실패 처리가 균일하지 않다.** 벤더의 SQL 재시도가 전부
실패하면 대개 `SqlErrorSummary`로 정중한 사과 메시지를 만들어 돌려주지만(위 참고),
한 번은 이 경로를 타지 않고 잘못된 SQL 텍스트가 그대로 응답에 실려 Analyst까지 전달됐다.
Analyst 쪽 `OrchestratorClient.ExecuteUserIntentQuery`가 이걸 파싱하다 예외를 던지며
패널 전체가 죽었고(`General error occurred. Error determining intent.`), 대화 히스토리를
지워야 복구됐다. **Phase 3에서 자체 서비스를 만들 때는 이 경로를 절대 재현하면 안 된다** —
무슨 일이 있어도 `/orchestrator` 응답은 항상 클라이언트가 파싱 가능한 형태여야 하고,
내부 실패는 반드시 `SqlErrorSummary` 류의 정상 응답으로 감싸서 내보내야 한다.

❓ 작은 모델(3B)이 이 정도로 복잡한 방언 규칙 + 재시도 피드백을 실제로 소화할 수 있는지는
아직 결론이 안 났다. 지금까지의 실패는 규칙을 "이해하고 다르게 시도"한 게 아니라 동일한
쿼리를 반복하거나(세미콜론 사례) 스키마를 임의로 바꿔치기하는(한자 치환 사례) 패턴이었다.

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
