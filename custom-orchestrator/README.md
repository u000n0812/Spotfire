# custom-orchestrator (계획 단계)

Spotfire Copilot을 자체 서비스로 대체하는 작업은 전부 이 폴더 안에서 진행한다.
저장소 루트의 파일들(`docker-compose.yml`, `.env`, `sitecustomize_image_shrink.py`,
`orchestrator_main.py` 등)은 **건드리지 않는다** — 그것들은 지금 실제로 Analyst가
쓰고 있는, 벤더 이미지 + 패치 기반의 동작하는 설정이다. 이 폴더의 작업이 벤더 설정과
기능이 같아질 때까지는 루트 설정이 그대로 운영 기준이다.

전체 배경과 단계별 계획은 기획안 문서 참고:
https://docs.google.com/document/d/1J745m4F57ORA9FA72tzeWqoPQKd2QEn3OWd4dzNo1rQ/edit

## 지금 상태

**계획만 있고 코드는 아직 없다.** Phase 0(막힌 두 기능 검증)이 루트 설정 위에서
진행 중이고, 그 결과가 나오기 전에는 여기서 실제 구현에 들어가지 않기로 했다 —
검증 결과가 SQL 생성에 어떤 모델/방식이 필요한지를 정하므로, 그 전에 서비스
코드부터 쓰면 다시 뜯어고칠 가능성이 크다.

## 이 폴더가 다루는 범위 (기획안의 Phase 1~3)

| Phase | 내용 | 이 폴더에서 시작 시점 |
|---|---|---|
| Phase 1 | API 계약 확정 | 지금 — `API_CONTRACT.md` 참고, 확인 안 된 부분부터 채워야 함 |
| Phase 2 | 이미 동작하는 기능(RAG, 화면 설명, 구조 질의)을 자체 서비스로 이식 | Phase 1 계약 확정 후 |
| Phase 3 | 값 조회 / Outlier 분석 정식 구현 | Phase 0 검증 결과 확정 후 |

Phase 4(시각화 생성/수정)는 기획안에서 보류 처리했으므로 이 폴더의 범위 밖이다.

## Phase 1 다음 작업 (미착수)

1. 지금 운영 중인 orchestrator 2.3.0의 실제 명세를 저장해 둔다:
   ```bash
   curl -s http://localhost:8081/openapi.json > custom-orchestrator/vendor-openapi-2.3.0.json
   ```
   `API_CONTRACT.md`의 "❓ 미확인" 항목 대부분이 이걸로 확정된다.
2. `diagnose.sh`는 2.0.0(`:latest`) 이미지만 대상으로 하드코딩돼 있다 — 2.3.0 대상
   조사는 위 openapi.json과 `docker exec copilot-orchestrator sh -c "grep -rn ... /app"`
   형태의 직접 조사로 대신한다 (`diagnose.sh` 자체는 루트 파일이라 수정하지 않는다).
3. 계약이 확정되면 최소 스캐폴드(인증 + `/orchestrator` 하나)부터 시작 — 전체 MVP를
   한 번에 쓰지 않는다.
