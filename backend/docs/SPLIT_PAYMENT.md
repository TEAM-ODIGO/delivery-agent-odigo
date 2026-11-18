# 분할결제 (로컬 확장, 팀 합의 전)

지역화폐로 낼 금액을 **고객이 먼저 정하고**, 나머지는 다른 결제수단(카드)으로 낸다.
명세에 없는 선택 필드만 더했다. **필드를 안 보내면 지금 명세와 완전히 같게 동작**한다.

## 용어
- 전체 금액 = 메뉴 합계 + 배달비 (할인 전)
- `L` = 고객이 정한 지역화폐 몫 (할인 전 금액, 1 ≤ L < 전체 금액)
- 나머지 몫 = 전체 금액 − L → 카드로 결제 (최상위 `payment_method_id`는 이 카드)
- 혜택 규칙: **자격(최소주문금액·실적·한도)은 주문 전체 금액으로, 할인은 각 몫에 대해** 계산한다 (C `share_krw`).

## 추가한 필드
| API | 방향 | 필드 |
|---|---|---|
| O-02 `SELECT_PAYMENT_METHOD` | 요청 | `local_currency_amount_krw` (있으면 `payment_method_id`는 나머지를 낼 카드) |
| C-01 | 요청 | `local_currency_amount_krw` |
| C-01 | 응답 option | `split.local_currency` / `split.remainder` = `{payment_method_id, amount_krw, instant_discount_krw, charge_krw}` |
| C-05 | 요청 | `share_krw` (이 결제수단이 낼 몫, 없으면 전체) |
| B-05 | 요청 | `local_currency_payment_method_id`, `local_currency_amount_krw` (둘 다 있거나 둘 다 없음) |
| B-05·B-06 | 응답 | `local_currency_payment_method_id`, `local_currency_amount_krw`, `local_currency_charge_krw` (지역화폐가 실제로 빠지는 금액 = 몫 − 지역화폐 할인) |
| B-07 | 응답 `execution_input` | `local_currency_payment_method_id`, `local_currency_charge_krw` |
| D-02 `quote` | 요청 | 위 두 필드 (`local_currency_payment_method_id`, `local_currency_charge_krw`) |
| D-06 | 요청 | 위 두 필드 (있을 때만 보냄) |
| E-01 | 요청 | 위 두 필드. `charge_krw`는 **전체** 결제금액 |
| E-01·E-03 | 응답 | `payments: [{payment_method_id, charge_krw}]` (지역화폐 먼저) |

금액 의미: `instant_discount_krw`·`charge_krw`(최상위)는 두 몫을 **합친 값**이다. 카드 몫 = `charge_krw − local_currency_charge_krw`.

## 처리 규칙
- B: `L ≥ 전체 금액`이면 409 `SPLIT_AMOUNT_INVALID`. B-05 멱등 키에 `:{지역화폐수단}:{L}`이 붙는다.
- D: 승인 대상 견적에 분할이 있으면 D-02가 C로 두 몫을 다시 계산해 맞는지 확인하고, D-06도 지역화폐 몫을 같이 대조한다 (불일치 → 차단).
- E: 한 건의 결제로 기관(FEP)에 전체 금액을 승인받고, 원장은 몫별로 갱신한다 (지역화폐 잔액 −몫, 카드 당월 실적 +몫). 잔액 부족이면 카드 몫도 결제하지 않는다 (FAILED `INSUFFICIENT_BALANCE`).
- 환불: **지역화폐 몫부터** 돌려준다 (누적 환불액 기준). 합의 때 바꿀 수 있다.

## 합의 때 정할 것
1. 필드 이름·위치 (특히 E-01의 전체 금액 `charge_krw` + 지역화폐 몫 방식이 맞는지, 아니면 `payments[]` 배열로 받을지).
2. 환불 순서 (지역화폐 우선 / 비례 / 카드 우선).
3. 지역화폐 몫에도 즉시할인을 적용하는지 (지금은 적용: PRD-LOCAL-A 5%).
4. 카드 한 장 + 지역화폐만 허용 (카드 두 장 분할은 안 됨).
5. 승인 화면에 두 몫을 보여 주는 문구.
