# 배달 주문 AI Agent (2조)

| 폴더 | 내용 | 포트 |
|---|---|---|
| `backend/` | 모듈형 모놀리스: O·A·B·C·D (FastAPI) | 8000 |
| `e-payment/` | E 모의결제 (FastAPI, 별도 서버) | 8002 |
| `frontend/` | 화면 (Vite·React 19·TS, O만 호출) | 5173 |

## 전체 실행 (터미널 2개)
```bash
# 1) E
cd e-payment && uv sync && cp .env.example .env
#    .env의 INTERNAL_SERVICE_TOKEN을 backend와 같은 값으로
uv run --env-file .env uvicorn e_payment.main:app --port 8002

# 2) 모놀리스
cd backend && uv sync && cp .env.example .env
#    .env: INTERNAL_SERVICE_TOKEN(위와 같은 값), ADMIN_ACCESS_TOKEN=adm,
#          E_PAYMENT_URL=http://localhost:8002, E_BASE_URL=http://localhost:8002
uv run --env-file .env uvicorn app.main:app --port 8000

# 3) 시나리오 확인 (backend 폴더, 새 터미널)
uv run --env-file .env python scripts/demo_orchestrator.py

# 4) 화면 (frontend 폴더, 새 터미널)
npm install && npm run dev        # → http://localhost:5173
```
E의 결제 결과는 `e-payment/.env`의 `E_MOCK_MODE`(APPROVE/DECLINE/UNKNOWN/LOST)로 고른다. 바꾸면 E와 모놀리스를 같이 재시작.
E 없이 돌리려면 `E_PAYMENT_URL`을 비운다(가짜 E 사용, `#결제거절` 같은 태그로 시나리오 선택).

자세한 내용: `backend/README.md`, `e-payment/README.md`, `backend/docs/MERGE_REPORT.md`, `backend/CONTRIBUTING.md`

## 분할결제 (로컬 확장 — 팀 합의 전)
지역화폐로 낼 금액을 고객이 먼저 정하고, 나머지는 카드로 결제한다 (화면 04의 "지역화폐로 일부만 결제").
명세에 없는 **선택 필드**만 더했고, 필드를 안 보내면 기존 동작과 같다. 필드 목록·합의 때 확인할 점은 `backend/docs/SPLIT_PAYMENT.md`.
시연: `scripts/demo_orchestrator.py o11`.

## 내 카드 추가 · 모의 마이데이터 (로컬 확장 — 팀 합의 전)
첫 화면 "맡길 범위 → 내 카드 관리"에서 카드를 **직접 입력**하거나 **모의 마이데이터**로 불러와 내 결제수단에 넣는다. 진짜 마이데이터가 아니라 시연용 고정 목록이다 (카드 번호 전체는 받지 않는다).
배달 할인 규칙이 있는 카드(신한카드 3종, KB국민 노리2, 요기요 삼성카드)는 추천에 쓰이고, 규칙이 없는 카드(적립형 현대카드 ZERO Up, 정보를 모르는 카드)는 목록과 혜택 화면에만 보인다. **E를 연결해야** 추가·빼기가 된다(`E_PAYMENT_URL`·`E_BASE_URL`).
새 API만 더했고, 기존 동작이 바뀌는 곳(C-01에서 규칙 없는 카드 제외 등)과 시연 순서는 `backend/docs/MY_CARDS.md`.

## 예산 초과 확인 (로컬 확장 — 팀 합의 전)
예산 안의 후보가 하나도 없을 때 화면이 "지정 금액에 해당하는 메뉴와 가게가 없어서 예산 초과의 가게를 추천합니다. 계속 주문하시겠습니까?"를 묻는다.
**예**면 그 주문에 한해 D의 예산 한도를 이번 금액까지 올려 결제까지 진행하고, **아니오**면 주문을 취소하고 처음 화면으로 돌아간다.
명세에 없는 **선택 필드**(`allow_over_budget`)만 더했고, 안 보내면 기존처럼 D가 예산 초과를 막는다. 규칙·합의 때 확인할 점은 `backend/docs/OVER_BUDGET_CONSENT.md`.
시연: `scripts/demo_orchestrator.py o13`.
