# backend — 모듈형 모놀리스 (FastAPI)

O·A·B·C·D 기능이 한 서버 안의 모듈로 들어간다. E(결제, Spring)만 별도 서버.
현재 구현: **D (delegation) MVP 1**, **O (orchestrator) MVP 1**, **C (benefit)** 11개 API. A·B·E는 가짜(`app/fakes`).
C를 가짜로 되돌리려면 `.env`에 `USE_FAKE_C=1`.

## 실행

```bash
uv sync                                   # Python 3.14 + 라이브러리 설치 (uv.lock 기준)
cp .env.example .env                      # 설정 (DB 주소, 서비스 키)
uv run uvicorn app.main:app --reload --port 8000
# → http://localhost:8000/docs (Swagger)
```

PostgreSQL로 실행하려면 `docker compose up -d` 후 `.env`의 `DATABASE_URL`을 PostgreSQL 줄로 바꾼다.

## 테스트

```bash
uv run pytest                 # SQLite로 전체 테스트
uv run pytest -k s5 -v        # 이름으로 골라 실행
TEST_DATABASE_URL=postgresql+psycopg://delivery:delivery@localhost:5432/delivery uv run pytest
uv run ruff check . && uv run ruff format .
```

## 시나리오 직접 보기

서버를 켠 상태에서 **다른 터미널**을 열고 (backend 폴더 안에서):

```bash
uv run --env-file .env python scripts/demo_scenarios.py          # 시나리오 9개 전부
uv run --env-file .env python scripts/demo_scenarios.py s5 s7    # 골라서
uv run --env-file .env python scripts/demo_scenarios.py --list   # 목록
```

각 단계 결과(✅/⛔, 사유 코드, 비교값)와 그 주문의 D 감사 로그, 마지막에 요약과 로그 사슬 검증 결과가 나온다.
로그 장애(S10)와 만료는 pytest로 본다: `uv run pytest -v -k "s10 or expire"`

## O(오케스트레이터) 시나리오 보기

서버를 켠 상태에서 다른 터미널 (backend 폴더 안에서). 화면처럼 O-01·O-02·O-03만 부른다:

```bash
uv run --env-file .env python scripts/demo_orchestrator.py          # 시나리오 11개 전부
uv run --env-file .env python scripts/demo_orchestrator.py o3 o7    # 골라서
uv run --env-file .env python scripts/demo_orchestrator.py --list   # 목록
```

- 실패 상황은 주문 문장의 태그로 고른다: `#품절` `#모두품절` `#혜택불일치` `#결제거절` `#결제미확정` `#응답유실` `#B08장애` `#A장애` (`app/fakes/scenario.py`)
- 별도 `e-payment`를 쓰면서 `#결제거절`·`#결제미확정`을 주문별로 시연하려면 백엔드 `.env`에 `O_DEMO_E_OVERRIDE=1`, E의 `.env`에 `E_ALLOW_MOCK_OVERRIDE=1`을 설정하고 두 서버를 재시작한다. 이 설정은 시연·테스트 전용이며 일반 요청에는 `X-Mock-Outcome` 헤더를 보내지 않는다.
- `#응답유실`은 시연 모드에서 O가 E의 응답만 유실된 것으로 처리한다. E의 결제 기록은 남고 O는 E-03으로 상태를 확인한다.
- `.env`에 `ADMIN_ACCESS_TOKEN`을 넣으면 주문마다 O-04 호출 순서도 보여 준다.
- E를 별도 서버로 띄워 D-06을 HTTP로 확인하려면:
  ```bash
  uv run --env-file .env uvicorn scripts.fake_e_server:app --port 8002   # 가짜 E (D_URL 기본 localhost:8000)
  # .env에 E_PAYMENT_URL=http://localhost:8002 넣고 모놀리스 재시작. 이 서버는 FAKE_E_MODE=APPROVE|DECLINE|UNKNOWN만 지원한다.
  ```

## 요기요 장바구니 담기 (O-13 · O-14)

승인 화면과 결제 완료 화면의 **요기요 장바구니에 담기** 버튼이 B 견적과 같은 메뉴를 요기요 장바구니에 담고,
금액을 견적과 맞춰 본 뒤 캡처를 보여 준다. **요기요의 주문하기·결제는 절대 누르지 않는다.**

- `POST /orchestrator/runs/{request_id}/yogiyo-cart` (O-13, 202): 담기 작업 시작. 승인 대기·결제 중·완료 상태에서만 된다 (그 외 409 `CART_NOT_AVAILABLE`). 진행 중이면 같은 작업을 돌려준다.
- `GET /orchestrator/runs/{request_id}/yogiyo-cart` (O-14): 최근 작업. `QUEUED`·`RUNNING` → `CART_READY`(견적과 같음) / `CART_MISMATCH`(다름, `mismatches`) / `FAILED`(`error`). 없으면 `{"status": "NONE"}`.
- 모드는 `.env`의 `YOGIYO_CART_MODE`로 고른다 (`.env.example` 참고).
  - `fake`(기본): Playwright 없이 견적대로 담긴 것처럼 응답. 주문 문장에 `#요기요불일치`·`#요기요실패`를 넣으면 그 결과를 시연한다.
  - `real`: 진짜 요기요를 연다. 따로 설치할 것은 없다: Playwright는 `uv sync`로 설치되고, 크로미움 브라우저는 첫 담기 때 자동으로 받는다 (처음 한 번 1~2분). 설치된 Chrome을 쓰려면 `YOGIYO_CART_BROWSER=chrome`. 요기요 창이 뜨고(`YOGIYO_CART_HEADLESS=0`, 기본), 새 주문을 시작할 때까지 열려 있다 (`YOGIYO_CART_KEEP_BROWSER=1`, 기본). 다시 담기를 누르면 창을 새로 열어 처음부터 담는다.
  - `off`: 버튼을 누르면 503 `CART_DISABLED`.
- 창 없는(headless) 브라우저는 요기요가 "Access Denied"로 막는다. 그래서 창을 띄우는 모드만 쓰고, 막히면 그 이유를 실패 사유로 알려 준다 (우회하지 않음).
- 서버 없이 Playwright만 확인: `uv run python scripts/yogiyo_cart_try.py --find-open` (지금 열린 식당을 찾아 담기). 식당을 정하려면 `--restaurant`·`--item`, 열리는지만 보려면 `--check-only`.
- 지금 요기요 주문을 받지 않는 가게("요기요 서비스가 제공되지 않습니다")는 바로 실패로 알려 준다.
- 담긴 장바구니 원본은 캡처 옆 `{작업id}-cart.json`에 남는다 (요기요 형식이 바뀌었을 때 확인용).
- 요기요 장바구니는 그 브라우저 탭 안에만 있다. 창을 닫으면 사라지므로 화면에는 캡처로 남긴다.

## 요기요 실시간 식당 확인 (O-15 · O-16)

후보를 보여 줄 때(CANDIDATES_READY) 상위 후보가 지금 요기요에서 주문 가능한지 백그라운드로 확인한다.
장바구니에는 담지 않고 식당 페이지만 읽는다. 후보 화면은 확인을 기다리지 않는다.

- A-05에서 후보를 `LIVE_CHECK_TOP_N`(6)곳 받고, 순위대로 확인하다 통과가 `LIVE_CHECK_WANT`(3)곳이면 멈춘다.
- 판정: `OK` · `CHANGED`(가격·배달비·최소 주문 금액·메뉴 번호만 다름, 고를 수 있음) · `UNAVAILABLE`(서비스 미제공·메뉴 없음·품절·페이지 안 열림, 고를 수 없음) · `BLOCKED` · `ERROR` · `CHECKING` · `SKIPPED`
- `GET /orchestrator/runs/{request_id}/live-check` (O-15): 후보별 결과와 진행률. `visible`은 화면에 보일 후보(고를 수 있는 후보가 `WANT`곳 나올 때까지 순위대로), `selectable`은 고를 수 있는지. 주문 불가 후보는 숨기지 않고 사유와 함께 비활성으로 보인다.
- `POST /orchestrator/runs/{request_id}/live-check` (O-16): 다시 확인 (후보 선택 단계에서만).
- 후보 선택(O-02 `SELECT_CANDIDATE`)에서 주문 불가로 확인된 후보는 409 `CANDIDATE_UNAVAILABLE`. 확인 중이면 그 식당 확인을 잠깐 기다린다. 판정은 주문 이벤트(O-04)에 `LIVE_CHECKED`로 남는다.
- 결과는 식당당 한 행(`restaurant_live_status`)에 덮어쓴다. 주문마다 쌓지 않는다.
- 모드는 `.env`의 `LIVE_CHECK_MODE`로 고른다. `fake`(기본)는 주문 문장 태그 `#요기요닫힘` `#요기요가격변경` `#요기요전부닫힘`으로 시연한다.
- 서버 없이 확인 도구만 돌리기: `uv run python scripts/yogiyo_cart_try.py --check 20 --want 20`

### 로컬에서 진짜 요기요로 시연하기 (real 모드)

`.env`에 두 줄을 넣고 백엔드를 재시작한다. 따로 설치할 것은 없다 (크로미움은 첫 실행 때 자동 설치).

```
LIVE_CHECK_MODE=real
YOGIYO_CART_MODE=real
```

- 후보 화면이 뜨면 요기요 창이 열려 탭 최대 3개로 후보 식당을 확인하고, 확인용 창은 끝나면 닫힌다.
- 실시간 확인과 장바구니 담기는 같은 브라우저를 쓴다. 요기요 창이 겹쳐 뜨지 않고, 열어 둔 장바구니 창은 그대로 둔다.
- 15분 안에 같은 식당을 다시 추천하면 요기요를 다시 열지 않고 최근 확인을 쓴다. 후보 화면의 "다시 확인"은 이 캐시를 무시한다.
- O-15 응답의 `seconds`(전체 확인 시간), 후보별 `seconds`·`cached`로 걸린 시간과 캐시 사용 여부를 볼 수 있다.
- 창 없는(headless) 브라우저는 요기요가 막는다. `YOGIYO_CART_HEADLESS=0`(기본)을 바꾸지 않는다.

## 영업시간으로 닫힌 식당 미리 빼기 (A-05)

A-05는 `data/1_식당.csv`의 `영업시간` 열(예: `17:00 ~ 03:00`)을 보고, 주문 시각에 영업하지 않는 식당을 후보에서 뺀다.
요기요를 열기 전에 거르므로 실시간 확인 대상과 시간이 줄어든다.

- `00:00 ~ 00:00`은 24시간 영업, 닫는 시각이 여는 시각보다 이르면 자정을 넘겨 영업한다.
- 영업시간을 모르는 식당은 지금처럼 후보에 넣고 실시간 확인에 맡긴다. 브레이크타임·휴무일은 요기요 데이터에 없어 실시간 확인이 거른다.
- 영업시간 모으기 (요기요 식당 페이지의 영업시간, 158곳에 1~2분):
  ```bash
  uv run python scripts/collect_opening_hours.py --collect          # 미리 보기 (CSV 안 바꿈)
  uv run python scripts/collect_opening_hours.py --collect --write  # data/1_식당.csv에 씀
  ```
  서버를 다시 켜면 CSV의 영업시간이 DB(`restaurant.opening_hours`)에 반영된다. 예전 DB에는 열이 자동으로 추가된다.
- 시간과 상관없이 시연하려면 `.env`에 `DISCOVERY_OPENING_HOURS_FILTER=0` (테스트는 기본으로 끔).

## 시연 전 데이터 새로 고치기 (식당·메뉴 재수집)

`data/1_식당.csv`, `data/2_메뉴.csv`는 11/11 수집본이다. 시연 전에 요기요에서 가격·품절·배달비·최소 주문 금액·영업시간을 다시 읽어 맞춘다.
창이 뜨는 브라우저로 식당 158곳을 연다 (탭 3개, 2~3분). 장바구니에는 담지 않는다.

```bash
uv run python scripts/recollect_catalog.py --collect --limit 5    # 시험: 5곳만 읽고 보고서만
uv run python scripts/recollect_catalog.py --collect              # 전부 읽고 바뀐 점만 보기 (CSV 안 바꿈)
uv run python scripts/recollect_catalog.py --from latest --write  # 방금 읽은 결과를 CSV에 씀 (다시 안 읽음)
```

- 결과: `cart_runs/recollect-20261121-1155.json` 같은 파일(읽은 원본), `cart_runs/recollect-날짜-report.md`(식당별 바뀐 점).
- 식당을 80% 미만 읽었으면 쓰지 않는다. 못 읽은 식당은 기존 줄을 그대로 둔다.
- 사라진 메뉴는 지우지 않고 품절로 표시한다. 같은 이름이 새 메뉴 번호로 생겼으면 번호만 바꾼다.
- 새 메뉴는 유형(본메뉴·사이드)·맵기 같은 분류 값이 없어 추가하지 않고 보고서에만 남긴다.
- 쓰기 전 원래 CSV는 `cart_runs/catalog-backup-날짜/`에 복사된다. 바뀐 줄만 바뀐다 (`git diff data/`로 확인).
- 서버를 다시 켜면 CSV의 수집시각이 DB보다 새로우므로 DB가 CSV에 맞춰진다 (C-08이 쓰는 지역화폐 열은 그대로).

## C(혜택)와 함께 확인

```bash
uv run uvicorn scripts.fake_c_server:app --port 8001                  # (선택) 명세 예시대로 답하는 가짜 C
uv run python scripts/check_with_c.py --c-url http://localhost:8001   # C·D 연동 점검 (C 서버 주소)
uv run python scripts/check_with_c.py --c-module <C 모듈>              # 모놀리스로 합친 뒤
```

D 서버를 C에 붙이려면 `.env`에 `C_BENEFIT_URL=http://localhost:8001`. 비우면 D 임시 규칙을 쓴다.

## C(혜택) 모듈

- HTTP: `/benefit/...` 11개 (C-01~C-11). Swagger `/docs`의 `C · 혜택` 그룹. 서비스 API는 `INTERNAL_SERVICE_TOKEN`, 관리자 API(C-03·06·07·08·10·11)는 `ADMIN_ACCESS_TOKEN`.
- 함수: `app/modules/benefit/facade.py` — O → `recommend`(C-01), B·D → `calculate`(C-05), D → `list_payment_methods`(C-02).
- 식당 지역화폐 정보는 팀 공용 식당 데이터(`app/fakes/catalog.py`)를 읽는다 (`benefit/catalog_source.py`). 수집 DB가 생기면 이 파일만 바꾼다.
- 아직 가짜인 부분: 결제수단 원장(E-11 대신 mock), 가맹점 공공데이터(C-08은 mock 소스), A-09 LLM(C-10은 503). 연결 지점은 `benefit/ledger.py`, `benefit/sources.py`.
- C 테스트만: `uv run pytest tests/benefit`. O 테스트를 진짜 C로: `TEST_REAL_C=1 uv run pytest tests/orchestrator`.

## 구조

```
app/
├── main.py                    # 서버 시작점, 모듈 router 연결
├── core/                      # 모든 모듈 공용: 설정, DB, 시간, 에러 형식
└── modules/
    ├── orchestrator/          # O (화면이 부르는 유일한 모듈)
    │   ├── service.py         # 상태 머신: A→D-01→A→B→C→B→D-02→D-03→B→E, 사용자 대기 지점에서 멈춤
    │   ├── router.py          # O-01~O-05 HTTP 입구 (사용자 토큰)
    │   ├── models.py          # orchestrator_run · run_event · idempotency 테이블
    │   ├── ports.py           # A·B·C·E에 기대하는 모양 + E HTTP 어댑터
    │   └── wiring.py          # 지금 쓸 A·B·C·D·E 고르기 (진짜 모듈이 생기면 여기서 교체)
    ├── benefit/               # C (facade.py = 함수 입구, router.py = HTTP 입구)
    └── delegation/            # D
        ├── constants.py       # 고정 값 (상태, 사유 코드, 이벤트 종류)
        ├── schemas.py         # 요청·응답 형식 = API 명세
        ├── rules.py           # 검증 규칙 (순수 함수)
        ├── hashing.py         # 주문안 해시
        ├── benefits.py        # C에게 물어보는 연결 지점 (임시 규칙 / C 서버 HTTP / C 함수)
        ├── audit.py           # 감사 로그 (마스킹, 해시 사슬)
        ├── models.py          # DB 테이블
        ├── service.py         # 유일한 입구 (O가 직접 호출)
        └── router.py          # HTTP 입구 (E의 D-06 등)
    ├── discovery/             # A (탐색·추천): facade.py(O가 호출) · router.py(/discovery) · loader.py(식당 CSV 적재)
    ├── ordering/              # B (주문안·견적·승인 대상): facade.py(O가 호출) · api.py · router.py(/ordering)
    └── benefit/               # C (혜택·결제수단 추천)
app/fakes/                     # 가짜 A·B·C·E (명세 예시 모양, 메모리 저장, #태그로 실패 상황)
tests/delegation/              # 규칙 · 해시/로그 · 시나리오 · HTTP/동시성
tests/benefit/                 # C: 계산 · API · 가맹점 매칭 · 규칙 활성화
tests/orchestrator/            # 흐름 · 중복/동시 제출 · 일시 장애 · 결제 미확정 · HTTP
```

해설: `../docs/08_D_MVP1_코드해설.md`

## A·B·C 합친 구성 (2026-11-16)

- 식당·메뉴: `data/1_식당.csv`, `data/2_메뉴.csv` → 서버 시작 때 `restaurant`·`menu` 테이블이 비어 있으면 자동 적재.
  A(탐색)·B(가격·품절)·C(지역화폐)가 같은 테이블을 읽는다. 직접 적재: `uv run python -m app.modules.discovery.loader`
- DB: 기본 SQLite. PostgreSQL이면 `.env`의 `DATABASE_URL`만 바꾸면 된다 (표는 `create_all`로 생성).
  B가 원래 쓰던 Alembic 마이그레이션은 `docs/b_migrations/`에 보관 (팀 공통 마이그레이션으로 합칠 때 사용).
- 테스트: `uv run pytest` (A·B·C·D·O 전부). B 원본 마이그레이션 시험은 `tests/ordering/b_root/`.
- 자세한 불일치·협의 목록: `docs/MERGE_REPORT.md`
