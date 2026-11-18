# A·B 합치기 보고서 (2026-11-16)

## 한 일
- A(discovery-service) → `app/modules/discovery/`, B(shinhan-b-ordering) → `app/modules/ordering/` 로 복사하고 import만 바꿨다. 업무 로직은 수정하지 않았다.
- O가 부르는 입구(dict ↔ 각 모듈의 pydantic)만 새로 만들었다: `discovery/facade.py`, `ordering/facade.py`.
- 식당·메뉴 CSV 적재기(`discovery/loader.py`)와 B용 읽기 어댑터(`ordering/adapters/db_catalog.py`)를 추가했다.
- `orchestrator/wiring.py`: A·B 진짜 연결(`USE_FAKE_A/B=1`이면 가짜).

## 확인 결과
- `uv run pytest` 534개 통과 (SQLite). A 30 · B 320대 · C 56 · D · O 포함.
- 진짜 A+B+C+D+O 서버에서 `demo_orchestrator.py` 11/11 통과 — SQLite와 PostgreSQL 16 둘 다.
- PostgreSQL 16에서 A·D 시험 127개 통과. O 시험(`tests/orchestrator`)은 PG에서 테이블 삭제 중 멈춤(앞 시험의 세션이 안 닫힘) → 원본 O 시험 구조 문제로 보이며 합치기와 무관, SQLite로는 전부 통과.

## 합치면서 바꾼 것 (원본과 달라진 점)
| 대상 | 원본 | 합친 뒤 | 이유 |
|---|---|---|---|
| A DB | PostgreSQL 전용(JSONB·ARRAY) | JSON 타입(PG에선 JSONB)으로 SQLite도 동작 | 팀 공통 기본이 SQLite |
| A·B 에러 | A: `AppError(status_code,…)`, B: `AppError(code,…,status_code=)` | 팀 공통 `AppError(status,code,message,details)` | O가 `exc.status`로 판단. B 내부는 B 클래스 유지 후 facade에서 변환 |
| A 설정 | pydantic-settings, `.env` | 환경변수 직접 (`LLM_ENABLED` 등) | 팀 설정은 환경변수 방식 |
| B DB | 자체 Base·엔진·Alembic | 팀 공통 Base·세션. 마이그레이션은 `docs/b_migrations/` 보관 | 팀은 `create_all` 사용 중 |
| B 테스트 1건 | 시계를 안 넘겨 날짜가 지나면 실패 | 가짜 시계 전달 | 합친 것과 무관한 원본 결함 |

## 명세·연결 불일치 (팀 협의 필요)
1. **B-01 body에 user_id 없음** — C-05·B 본인 확인에 필요. 지금은 O가 `user_id`를 같이 넘기고 facade가 호출 문맥(CallContext)으로만 전달한다. 명세에 추가 필요.
2. **D-04 응답에 proposal_hash 없음** — 진짜 B는 hash가 없으면 B-07을 `ready=false`로 막는다(`APPROVAL_HASH_UNAVAILABLE`). 지금은 연결부가 D가 저장한 hash를 읽기 전용으로 붙여 준다. D-04 명세에 추가 필요(D 담당 확인).
3. **배달비 구간** — A는 주문금액 구간표로 배달비를 계산하는데 B의 카탈로그 DTO에는 고정 배달비 하나뿐. B-01에서는 메뉴 합계를 힌트로 넘겨 A와 같은 구간을 쓰지만, B-04 재검증은 기본 배달비를 쓴다 → 구간이 다른 식당은 재검증에서 금액이 달라질 수 있다.
4. **카탈로그 판본** — 수집 DB에 판본 이력이 없어 현재 적재본을 `CAT-V1` 하나로 취급. O가 `CAT-V1`을 고정값으로 보낸다. 데이터를 다시 적재해도 판본이 안 바뀐다.
5. **옵션** — 옵션 테이블이 없어 B의 option_groups는 비어 있고 A는 option_ids를 비워 보낸다(필수옵션은 `필수옵션포함가`에 포함).
6. **C-05 `mock: true`** — B는 응답에 mock 필드가 없으면 거부한다(엄격 검사). C 함수 호출에도 붙이도록 `benefit/facade.calculate`를 고쳤다.
7. **A 예산 기준** — A는 예산에 배달비 포함, 후보 금액은 할인 전 금액. B·C의 `POST_FINANCIAL`(할인 후 금액으로 예산 비교)과 다르다. 지금은 B-05에서 최종 비교.
8. **시나리오 태그(#결제거절 등)** — 진짜 A는 태그를 모른다. 연결부가 태그를 떼어 가짜 E용 장부에만 적는다. `#품절`·`#B08장애`는 가짜 B에서만 효과가 있다.
9. **B의 PostgreSQL 18 고정** — B 문서는 PG18 기준. 여기서는 SQLite와 PG16으로만 확인했다. B 원본의 PG18 전용 시험은 돌리지 못했다.

## E 연결 (2026-11-17)
- `../e-payment`(FastAPI 별도 서버, 포트 8002)를 합쳤다. 모놀리스는 `E_PAYMENT_URL`로 E-01·E-03을, C는 `E_BASE_URL`로 E-11을 부른다. E는 `D_URL`의 D-06을 부른다.
- 확인: 진짜 A·B·C·D·O·E로 O 시연 통과(승인 모드 6개, 거절 모드 o8, 미확정 모드 o5·o5b·o7). 결제 후 C가 E-11로 읽은 카드 당월 실적이 350,000 → 367,500원으로 늘어난 것까지 확인.
- E를 다시 띄우면 모놀리스도 같이 재시작할 것 (끊긴 연결이 남아 결제가 미확정으로 보임).

---

# 2차 합치기 (2026-11-17 오후): 새 B · 프론트 · 분할결제

## 한 일
- **B 교체**: 김민지님의 새 B(`b-ordering-service`)로 `app/modules/ordering/`를 바꿨다 (이전 B 담당 이종혁 → 김민지). 이전 B는 보관만 하고 쓰지 않는다 (`docs/archive/ordering_v1_migrations`는 이전 마이그레이션).
  - O가 부르는 입구는 `ordering/facade.py`(dict ↔ pydantic, 같은 함수 이름), 연결 어댑터는 `orchestrator/wiring.py`(`_BenefitForB`, `_ApprovalForB`)에 둔다.
  - 새 B는 식당·메뉴 표를 직접 읽고 배달비 구간을 계산한다 → 위 "명세·연결 불일치" 3번(재검증 때 배달비 구간 차이)은 **새 B에서는 해소**됐다. 1·2·4·8번은 그대로다 (4번: 새 B가 `CATALOG_VERSION_CHANGED`를 INFO로 알린다. O는 `CAT-V1` 고정). 5·6·7·9번은 새 B 기준으로 다시 확인하지 않았다.
  - 새 B 테스트 25개를 `tests/ordering/`에 맞춰 넣었다. B-04·B-10(MVP 2)은 새 B에 아직 없다.
  - 견적 유효시간은 모놀리스에서 기본 1800초로 둔다 (`ORDERING_QUOTE_TTL_SECONDS`, 새 B 단독 기본은 600초).
- **프론트 합치기**: `../frontend`(v0.3)를 넣었다. 화면이 기대하는데 O에 없던 것을 채웠다.
  - O-09 내 주문 목록 · O-10 결제수단 목록 · O-11 전체 주문(관리자) · O-12 주문별 D 감사 로그(관리자).
  - 후보 화면(가게 별점·배달시간·지역화폐 여부·품목·위반 사유), 결제수단 화면(이름·종류·제외된 수단·주문 요약), 승인·완료 화면(주문 요약)이 쓰는 필드.
- **분할결제**(`docs/SPLIT_PAYMENT.md`): C·B·D·O·E(진짜·가짜)·프론트까지 연결. 모두 선택 필드.

## 확인 결과
- `backend`: `pytest` 249개 통과 (SQLite). `e-payment`: 40개 통과.
- 진짜 A·B·C·D·O + 진짜 E 서버: `demo_orchestrator.py` 10/12 — 실패 o5·o8은 `#결제미확정`·`#결제거절` 태그가 **가짜 E에서만** 동작하기 때문 (E의 `E_MOCK_MODE`로 같은 결과를 낸다). `E_PAYMENT_URL`을 비운 가짜 E에서는 12/12.
- 분할결제 실제 확인: 주문 19,500원 → 지역화폐 8,000원 + CARD001. 결제 17,100원 (지역화폐 7,600 + 카드 9,500). E 원장: LOCAL001 120,000 → 112,400, CARD001 당월 실적 +9,500.
- 브라우저(Playwright)로 주문 입력 → 후보 → 분할 입력 → 승인 → 완료까지 화면 확인.

## 알아 둘 것
- 개발용 `dev.db`가 있으면 **지우고 다시 시작**한다 (새 B 표·컬럼이 달라 `create_all`로는 안 바뀐다). E의 `e.db`도 같다 (분할결제 컬럼 추가).
- 명세 엑셀의 B 담당자 칸은 아직 이종혁이다 → 김민지로 고칠 것.
- 분할결제 필드는 명세에 없는 로컬 확장이다. 팀 합의 후 명세에 올리거나 걷어내야 한다.
- 가짜 B·가짜 C는 분할을 모른다 (`USE_FAKE_B/C=1`에서 분할을 고르면 409 `SPLIT_NOT_AVAILABLE`).
