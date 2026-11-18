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
