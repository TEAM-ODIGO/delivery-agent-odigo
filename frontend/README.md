# frontend — 배달 주문 AI Agent 화면 (와이어프레임 v0.3)

React 19 + TypeScript + Vite. 화면은 **O(오케스트레이터)만** 부른다 (`/orchestrator/...`).

## 실행

```bash
# 1) 백엔드 (backend 폴더에서)
uv run --env-file .env uvicorn app.main:app --reload --port 8000
#    관리자 화면을 쓰려면 backend/.env 에 ADMIN_ACCESS_TOKEN=아무값 을 넣어 둔다

# 2) 프론트 (이 폴더에서)
npm install
npm run dev            # → http://localhost:5173
```

- 개발 서버가 `/orchestrator` 요청을 `http://localhost:8000`으로 넘긴다 (`vite.config.ts`). 그래서 백엔드에 CORS 설정이 없어도 된다.
  백엔드 주소가 다르면 `.env`에 `VITE_API_TARGET=http://주소:포트`.
- 로그인은 MVP 1 임시 토큰 (`dev-token-U001` → U001). 사용자는 '내 주문' 화면 아래에서 바꾼다.
- 배포용 빌드: `npm run build` → `dist/`. 같은 도메인에서 `/orchestrator`를 백엔드로 넘기는 프록시(nginx 등) 뒤에 둔다.

## 화면 ↔ 와이어프레임 ↔ API

| 경로 | 화면 (v0.3) | O 상태 | 부르는 API |
| --- | --- | --- | --- |
| `/` | 01 주문 입력, 01-1 맡길 범위 | — | O-01, O-10 |
| `/runs/:id` | 02 조건 확인 | NEED_CLARIFICATION | O-03, O-02 CLARIFICATION |
| | 03 / 03b 후보 선택 | CANDIDATES_READY (+notice) | O-02 SELECT_CANDIDATE |
| | 04 결제수단 선택 | PAYMENT_METHOD_SELECTION | O-02 SELECT_PAYMENT_METHOD |
| | 05 승인 | AWAITING_APPROVAL | O-02 APPROVE |
| | 05b 진행 차단 | BLOCKED | — |
| | 06a 결제 완료 | COMPLETED | — |
| | 06b 결제 확인 중 | PAYING · PAYMENT_UNKNOWN | O-03 3초마다 |
| | 06c 실패 · 취소 | FAILED · CANCELLED | — |
| `/orders` | 07 내 주문 목록 | — | O-09 |
| `/admin` | 08 운영 기록 조회 | — | O-11, O-04, O-12 (관리자 토큰) |

- 대기 상태(02~05)에서는 언제든 취소(O-02 CANCEL). 결제가 시작되면 취소 버튼이 꺼진다.
- 모든 O-02에 `expected_state_version`과 새 `Idempotency-Key`를 보낸다. 409(상태가 바뀜)면 안내 후 다시 불러온다.
- O가 혼자 넘어가는 상태(VALIDATING, PAYING 등)는 화면이 1.5초마다 다시 조회한다.

## 시연 시나리오

주문 화면의 '시연 시나리오 넣기'를 누르면 문장 끝에 태그가 붙는다 (백엔드 `app/fakes/scenario.py`).
`#품절` `#모두품절` `#혜택불일치` `#결제거절` `#결제미확정` `#응답유실` `#A장애`.
예산 차단은 '맡길 범위'에서 최대 결제 금액을 1만원으로 두고 주문하면 된다.

## 폴더

```
src/
  api/        client.ts (O 호출·토큰·오류), types.ts (O 응답 모양)
  lib/        format.ts (금액·시각·사유 코드 문구), hooks.ts
  components/ ui.tsx (화면 틀, 진행 표시, 버튼, 태그)
  pages/      NewOrder(01·01-1), RunPage(상태별 분기), MyRuns(07), Admin(08)
  screens/    Clarify(02), Candidates(03·03b), PaymentSelect(04), Approval(05), Results(05b·06a·06b·06c)
```

사유 코드 → 한국어 문구는 `src/lib/format.ts` 한 곳에서 고친다.

## 분할결제 (로컬 확장)
04 결제수단 화면의 "지역화폐로 일부만 결제"를 켜고 금액을 입력하면, 고른 카드로 나머지를 결제한다 (`local_currency_amount_krw`).
승인 화면에 두 몫이 나오고, 완료 화면에 수단별 금액이 나온다. 팀 합의 전 로컬 확장이다 (`backend/docs/SPLIT_PAYMENT.md`).
