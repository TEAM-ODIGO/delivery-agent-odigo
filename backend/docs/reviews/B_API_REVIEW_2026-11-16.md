# B-01~B-09 명세·연결·팀 통합 검토

2026년 11월 16일. 원본 API 수정 제안과 현재 B 코드의 대조, 발견한 문제 수정, Python 3.14·PostgreSQL 18 검증 기록이다. 결과는 로컬 구현의 범위이며 실제 팀 코드의 통합 완료를 뜻하지 않는다.

## 결과와 근거

9개 API의 경로·주요 성공 상태·원문 요청/응답 예제를 확인했다. 상태 변경·동시성·재전송·과거 견적·결제 결과 연결을 검사하고 아래 7개 문제를 수정했다. 기존 284개에서 320개로 검사가 늘었고 PostgreSQL 18 및 보조 SQLite에서 각각 통과했다. 일부 명세 공백은 여전히 팀 결정이 필요하다. 특히 D-04 해시 전달은 실제 결제 준비 연결의 차단 항목이다.

- [보관 명세](../../../output/api/2026-11-16-b-mvp1/sources/API명세서_수정안_20261115.xlsx): Downloads의 사용자 원본과 SHA-256 일치 확인. 원본은 변경하지 않았다.
- SHA-256: `72f1157b63b243ed41517650d664ddb621864db2d88c2ba9f056537eac8b5889`.
- `API 명세!A2`: 팀 합의 전 수정 제안. 이를 팀 최종 합의로 취급하지 않았다.
- `API 명세!A26:L34`: B API 9개. `G40:J40`, `I49:J53`, `I58:K60`: C·D·E 연결 계약.
- `공통 규칙!B2:B4`, `B6:B12`, `B18`, `B21:B25`: 서버 구조·자료형·업무 결과·멱등·데이터 소유·조건 문맥.
- [원문 계약 fixture](../../tests/ordering/fixtures/api_spec_20261115.json)와 [계약 검사](../../tests/ordering/test_spec_contracts.py): 검증한 원본의 기본 예제를 고정했다. 원문의 B-07 축약 해시 `sha256:3f2a…`는 테스트에서만 유효한 길이의 합성 해시로 치환한다. 실제 해시를 발급·추정한 것은 아니다.
- 구현·조립 방법의 기준은 [연동 가이드 초안 10](../integration/B_MVP1_INTEGRATION_GUIDE.md), 실행 방법은 [README](../../README.md)다.

## API별 대조

| API·원문 위치 | 확인한 동작 | 결과·남은 범위 |
| --- | --- | --- |
| B-01, 26행 | 단일 식당·메뉴·옵션·수량 검증, 할인 전 견적, 생성 201/생성 불가 200, 중복 처리 | 로컬 통과. 실제 수집 카탈로그 판본 조회는 연결 필요. 최초 옵션 미완성 시 주문안 없음 유지 |
| B-02, 27행 | 저장된 현재/과거 견적 조회, 조회 중 재계산 없음, 소유·요청 범위 | 로컬 통과. 기본 원문 응답 유지. payment/supplements는 선택적 확장. 실제 D의 현재 승인 상태를 제공하는 API로 간주하면 안 됨 |
| B-03, 28행 | 보완 답변·메뉴/수량 수정, 새 버전·견적, B 대상 무효화 | 로컬 통과. 업무상 불가를 공통 규칙대로 200으로 수정. REPLACE_ITEMS·질문 발급·실패 본문은 로컬 계약. 최초 draft 완성은 미지원 |
| B-04, 29행 | 최신 가격/품절 재검증, 만료 갱신, 새 견적 또는 재탐색 안내 | 로컬 통과. 실패·복구 후 과거 INVALID 기록 보존 수정. 최신 조회·근거·실패 본문은 팀 확인 필요 |
| B-05, 30행 | C-05 계산 요청/결과 연결, 할인 후 예산 PASS/FAIL, 대상 무효화 | 로컬 통과. 실제 C-01 추천·규칙·사용자 문맥은 연결 필요. 새 견적에는 혜택을 다시 계산 |
| B-06, 31행 | 현재 견적·혜택을 D용 스냅샷으로 고정, 승인 대상 201, D-02 입력 구성 | 로컬 통과. policy_version 미저장 시 D 입력 거부. PREPARED는 사용자 승인 완료가 아님 |
| B-07, 32행 | 현재 B 상태와 새 D-04 관측 대조, ready와 E 전달 자료, 상태 변경 뒤 차단 | 합성 D로 통과. 실제 D-04 원문에는 proposal_hash가 없어 해당 응답만 연결하면 ready=false. 해시 원본 전달 계약 필요 |
| B-08, 33행 | E 결과를 원래 실행·견적에 연결, 중복·미확정·충돌·늦은 결과 | 합성 E로 통과. 충돌 뒤 무시된 이벤트에도 CHECK_PAYMENT_STATUS 안내 유지 수정. 상태 원본은 E, B가 결제·재시도·환불하지 않음 |
| B-09, 34행 | 무인증 200 UP/503 DOWN, B 저장소 조회 검사 | 로컬 통과. C·D·E 또는 전체 결제 가능 상태의 보장은 아님 |

## 재현하고 수정한 문제

| 문제·실제 영향 | 적용한 수정 | 근거·검사 |
| --- | --- | --- |
| B-03 품절·필수 옵션 등 업무 불가를 422로 반환해 입력 오류와 혼동 | 정상 업무 결과 200 + RESEARCH_REQUIRED + issues. 이전 견적·대상 유지, 응답 멱등 저장. 잘못된 자료형·중복 옵션은 422 유지 | 공통 B7, B-01 J26. test_order_updates의 업무/입력 분리 및 실제 HTTP |
| B-04 실패 후 복구하면 과거 견적이 VALID로 다시 보임 | 견적별 revalidation_snapshot 보존. 현재 주문의 마지막 검사 ID는 동시성 확인에 계속 사용 | 과거 견적 조회·복구 검사. 원본 B-02 읽기 전용 및 B-08 원래 견적 연결 |
| 최신 카탈로그 TimeoutError/ConnectionError가 HTTP 500으로 올라옴 | CallableLatestCatalogReader에서 CATALOG_UNAVAILABLE 503, CHECK_DATA, retryable=true로 변환. 내부 접속 내용 비노출 | B-04 K29·공통 B7. 두 통신 오류 재현 및 DB 불변 검사 |
| 팀 모델을 공용 ORM Base에 넣으면 B Alembic 검사가 팀 테이블 생성까지 제안 | 명시적 B 모델 7개의 metadata만 복사해 검사. 공용 모델·기존 팀 테이블 제외 | 공통 B18. 미생성 팀 모델 추가 후 alembic check, 기존 팀 테이블 보존 검사 |
| B-08의 충돌 후 UNKNOWN이 IGNORED되면 next_action이 NONE으로 바뀜 | 해결되지 않은 충돌이 있으면 IGNORED 응답도 CHECK_PAYMENT_STATUS 유지. 채택 상태 재확인 후 해소하는 기존 로컬 정책 유지 | B-08 G33·로컬 D10. 충돌→무시→재확인 및 B-02 요약 일치 검사 |
| B-04 changes의 만료 시각만 UTC로 반환 | before/after도 +09:00로 통일 | 공통 B6. 실제 만료 갱신의 변경 시각 검사 |
| 배달비만 바뀌어도 메뉴 가격 변경으로 기록하고 동일한 시각도 변경으로 표시 | 배달비 단독 변화는 DELIVERY_FEE_CHANGED, 같은 before/after는 생략 | B-04의 변경 사유 의미. 배달비 단독·즉시 재검증 검사 |

저장 변경은 `20261116_review_quote_validation.py`에 추가했다. 기존 b04 뒤의 새 revision이며 테이블 수는 7개다. 최근 주문 검사뿐 아니라 과거 B04 실패 응답에서도 해당 견적의 근거를 찾아 복구한다. 저장된 근거가 없는 사건을 추정해서 만들지는 않는다. 과거 가격·혜택·결제는 수정하지 않는다. downgrade/upgrade 및 이미 복구된 이전 견적도 검사했다.

## 연결 검증

아래 흐름을 합성 카탈로그·C·D·E 결과로 실행했다. O·A·C·D·E 서비스 자체를 구현하거나 호출한 검증은 아니다.

1. B-01 → B-05 → B-06 → D-02 입력 구성 및 합성 D 승인 → B-07 → 임시 E 결과 → B-08 → B-02 결제 조회.
2. B-04 최신 판본 재견적 → B-03에서 같은 최신 판본 수정 → B-05 재계산 → B-06 새 대상 → 새 합성 D 관측의 B-07 → B-08 새 결과.
3. B-03/B-04 변경 뒤 과거 B 대상 차단, 늦은 E 결과는 원래 견적에 보존.
4. B-04 품절 실패 → B-03/05/06/07 차단 → 새 키 재검증으로 복구 → 과거 INVALID와 결제 기록 유지.
5. 중복 키·변경된 같은 키·동시 수정/재검증·진행 중 C/D 관측·저장 실패 롤백·B-09 무인증 상태 확인.

최신 판본을 재견적한 뒤 B-03으로 수정하려면 팀 CatalogReader도 그 판본을 읽을 수 있어야 한다. 최신 조회만 주입하고 지정 판본 조회가 구 판본만 지원하면 여기에서 503이 난다. 연결 검사에서 두 포트를 함께 제공해 확인했다. 개발 예제 파일의 판본/금액 역시 테스트할 흐름에 맞게 명시적으로 준비해야 하며 누락 데이터를 자동 성공으로 대체하지 않는다.

## 팀 합치기 전에 결정할 항목

| 항목·원문 근거 | 현재 사용 제한 | 다음 행동 |
| --- | --- | --- |
| D-04 해시 누락: B-07 J32·D-02 J49·D-04 J51 | 실제 D-04 원문만 연결하면 준비 완료 불가. B가 임의 해시를 만들지 않음 | D-04에 D 원본 해시를 포함하거나, D-02의 같은 승인 원본 해시를 연결하는 계약 확정 |
| user_id·policy_version 입력 공백: C-05 I40·D-02 I49·E-01 I58·공통 B21 대 B-01 I26 | B-01부터 신뢰된 CallContext 필요. E-01에는 O가 user_id를 추가해야 함. B-07 execution_input을 그대로 E body로 보내면 불완전 | O 인증·D-01 결과를 공용 문맥에 연결. HTTP 임의 사용자 헤더로 대체하지 않음 |
| 공용 코드·함수 sync/async·DB migration 이력 | 로컬 AppError/CallContext/Base/세션·sync 포트 기준. 실제 팀 import·트랜잭션과의 일치는 미확인 | 공용 타입 하나로 통일하고 B router만 등록. 루트 Alembic을 덮어쓰지 말고 팀 이력에 B revision 연결 |
| B-02 상태·B-03 질문/draft·B-04 최신 근거·실패 형식: D09·D13·D14 | 로컬 확장으로 연결됐지만 팀 최종 계약은 아님. B-01 생성 실패에서 B-03으로 이어지는 최초 draft 흐름은 없음 | O·UI·카탈로그 담당과 DTO·next_action·질문 원본·최신 판본·최초 draft 필요 여부 확정 |
| 결제 UNKNOWN·이벤트 순서·확정 충돌·키: D05·D08·D10 | O는 HTTP 200만으로 적용/승인 완료를 판단하면 안 됨. body의 next_action·ready·application_status도 분기 | E 응답/조회 원본과 이벤트 ID·관측 시각·수동 교정 절차 합의. 새로운 재선택/관측에 새 키 사용 |
| E-03 재시도 설명 충돌: 공통 B11·E-03 G60 대 K60 | 공통·설명은 재시도 금지, K60은 404 후 같은 execution_id 재요청 허용. B에는 재결제 로직 없음 | O·D·E가 어느 규칙을 채택할지 명시. 이번 검토에서 임의 재시도는 추가하지 않음 |

각 역할의 확정 담당자·기한은 이 검토에서 새로 지정하지 않았다. 위 항목의 최종 결정과 실제 팀 코드가 제공되면 해당 어댑터로 같은 검사 시나리오를 다시 실행한다. Java 25·Spring Boot 4.1·Node 26은 통합 기준이며 해당 코드의 실행·빌드 검증은 하지 않았다.

## 검증 결과와 재현

- Python 3.14.3·PostgreSQL 18.7: 전체 **320개 통과**. 원문 계약 26개, 회귀·migration·공용 Base·새 연결 검사 10개를 추가했다.
- 보조 SQLite: 같은 전체 **320개 통과**. PostgreSQL 호환성의 대체 증거로 취급하지 않는다.
- 실제 로컬 Uvicorn HTTP: 기본 B 흐름, 결제 응답 유실→확정, 주문 수정·보완, 재견적·품절·복구, B-03 업무 불가 200, 과거 INVALID·결제 보존 통과. 실제 E 결제 전송 없음.
- Ruff 검사·형식, uv lock 확인, Alembic head·check 확인. 새 head는 `20261116_review`다.
- 로컬 생성 증거는 `backend/data/pytest-postgresql18.xml`, `pytest-sqlite.xml`, `smoke-http-result.json`이다. Git 제외 파일이며 문서만 공유하면 실행 증거 파일은 따라가지 않는다.

backend 폴더에서 실행한다.

```powershell
uv run alembic upgrade head
$env:ORDERING_TEST_POSTGRES_URL = 'postgresql+psycopg://ordering:ordering-local-only@127.0.0.1:55432/ordering'
uv run pytest -q
uv run ruff check app migrations tests scripts
uv run ruff format --check app migrations tests scripts
uv run alembic check
uv run python scripts/smoke_http.py
```

위 계정은 compose의 공개된 로컬 개발 전용 값이다. 보조 SQLite는 ORDERING_TEST_POSTGRES_URL을 제거한 별도 세션에서 실행한다. 원격 저장소·PR·배포·팀 메시지 전송은 수행하지 않았다. 테스트 수는 검증한 시나리오의 수이며 실제 팀 통합·운영 품질 전체를 보증하지 않는다.
