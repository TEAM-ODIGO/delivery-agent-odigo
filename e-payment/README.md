# E 모의결제 서버 (FastAPI, 별도 서버)

팀 API 명세(2026-11-15) E-01~E-11. 진짜 돈은 움직이지 않고 승인·거절·미확정을 흉내 낸다.

## 실행
```bash
uv sync
cp .env.example .env          # INTERNAL_SERVICE_TOKEN은 모놀리스와 같은 값
uv run --env-file .env uvicorn e_payment.main:app --port 8002
# http://localhost:8002/docs
```
모놀리스 쪽 `.env`: `E_PAYMENT_URL=http://localhost:8002` (O가 E-01·E-03을 부른다). E는 `D_URL`의 D-06을 부른다.
C가 E-11을 읽게 하려면 C 쪽에 `E_BASE_URL=http://localhost:8002`.

## 테스트
`uv run pytest` (33개). PostgreSQL로: `TEST_DATABASE_URL=postgresql+psycopg://... uv run pytest`

## API
| | 경로 | 비고 |
|---|---|---|
| E-01 | POST /payments | ①멱등(Idempotency-Key=execution_id) ②D-06 ③PENDING ④모의 기관 ⑤원장. 결과는 모두 200 (APPROVED/FAILED/UNKNOWN/BLOCKED) |
| E-02 | GET /payments/{payment_id} | 환불 목록 포함 |
| E-03 | GET /payments/executions/{execution_id} | 없으면 404 PAYMENT_NOT_FOUND |
| E-04 | GET /payments?user_id=&cursor=&limit= | 1순위, 최신순 |
| E-05·06 | POST/GET /payments/{id}/refunds | 1순위 |
| E-07·08·09 | /internal/fep/... | E 내부 모의 기관 |
| E-10 | POST /internal/reconciliations | 1순위, UNKNOWN 확정 |
| E-11 | GET /internal/payment-methods?user_id= | C가 읽는 원장 |
| | GET /health | 인증 없음 |

## 모의 기관 결과 고르기 (`E_MOCK_MODE`)
APPROVE 승인 · DECLINE 거절(FAILED, MOCK_DECLINED) · UNKNOWN 기관은 승인했지만 응답 유실(E-03 조회 때 APPROVED 확정) · LOST 조회도 안 됨(UNKNOWN 유지)

## 명세에 없어서 정한 것 (팀 확인 필요)
- 사유 코드: PAYMENT_METHOD_NOT_FOUND · PAYMENT_METHOD_INACTIVE · INSUFFICIENT_BALANCE (FAILED), MOCK_DECLINED, FEP_TIMEOUT, DELEGATION_UNAVAILABLE (BLOCKED)
- 지역화폐 잔액 부족은 기관 호출 전에 E가 FAILED 처리
- 승인되면 지역화폐 잔액 차감 / 카드 당월 실적 증가, 환불되면 되돌림
- E-02·E-03 읽을 때 UNKNOWN이면 E-08로 한 번 확인해 확정 (끄려면 E_RECONCILE_ON_READ=0)
- E-05는 접수 응답은 PENDING(202), 처리는 바로 끝내서 E-06에서 REFUNDED
- 같은 환불 키 재요청은 현재 상태를 돌려줌
- E-11에는 '이번 달 받은 혜택액'이 없음 (C가 임시 시드 사용 중)

## 분할결제 (로컬 확장, 팀 합의 전)
E-01에 선택 필드 `local_currency_payment_method_id`, `local_currency_charge_krw`를 받을 수 있다. 있으면 `charge_krw`는 전체 금액,
지역화폐 몫은 `local_currency_charge_krw`, 나머지는 `payment_method_id`(카드)로 본다. 원장은 몫별로 갱신하고 응답에 `payments[]`가 붙는다.
환불은 지역화폐 몫부터 돌려준다. 자세한 규칙: `../backend/docs/SPLIT_PAYMENT.md`. 기존 DB(`e.db`)는 컬럼이 추가돼 **지우고 다시 시작**해야 한다.
