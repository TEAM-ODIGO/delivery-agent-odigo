# 내 카드 추가 · 모의 마이데이터 (로컬 확장, 팀 합의 전)

사용자가 자기 카드를 **직접 추가**하거나 **모의 마이데이터**로 불러와 내 결제수단에 넣는다.
명세에 없는 **새 API만 더했고**, 기존 API(E-01~E-11, C-01~C-07, O-01~O-14)의 요청·응답 모양은 그대로다.
(예외 두 가지는 아래 "기존 동작이 바뀌는 곳".)

## 진짜 마이데이터가 아니다
- 진짜 마이데이터 연동은 **본인신용정보관리업 허가**(자본금 5억 원 이상 등)를 받은 사업자만 할 수 있다. 이 프로젝트는 하지 않는다.
- "동의 → 카드 목록 → 선택" 흐름만 흉내 낸다. 카드 목록은 E의 고정 목록(`e_payment/cards.py`의 `MYDATA_CARDS`, 5장)이고 누구에게나 같다.
- 카드 번호 전체·유효기간·CVC는 **받지도 저장하지도 않는다.** 끝 4자리(`last4`)만 쓴다.

## 흐름
```
화면 ─ O ─┬─ C-02  (내 결제수단 목록 읽기. 새 카드는 E 원장에 있으니 바로 보인다)
          └─ E     (모의 마이데이터 · 카드 등록·삭제 · 등록 정보)
```
- 새 카드는 E의 결제수단 원장(`e_payment_method`)에 들어간다. C·D·B는 원래 하던 대로 E-11을 읽는다.
- 어떻게 추가됐는지는 새 표 `e_card_registration`(카드 등록 기록)과 `e_mydata_consent`(동의)에 따로 적는다. `e_payment_method`는 바꾸지 않아서 **기존 `e.db`를 지울 필요가 없다** (새 표만 자동으로 생긴다).

## 추가한 API
| 서버 | API | 설명 |
|---|---|---|
| E | `GET /internal/card-catalog` | 직접 입력으로 고를 수 있는 "혜택 규칙이 있는 카드" 3장 |
| E | `POST /internal/mock-mydata/consents` | 모의 마이데이터 정보제공 동의 (10분 유효) → `consent_id` |
| E | `GET /internal/mock-mydata/cards?user_id&consent_id` | 가진 카드 목록 (`supported`, `already_added` 포함) |
| E | `POST /internal/payment-methods` | 카드 추가 (`source`: `MANUAL` / `MYDATA`) → 201 |
| E | `GET /internal/card-registrations?user_id` | 직접 추가한 카드 목록 (화면이 "뺄 수 있는 카드"를 가리는 데 씀) |
| E | `DELETE /internal/payment-methods/{id}?user_id` | 직접 추가한 카드 빼기 |
| O | `GET /orchestrator/my-cards` | C-02 + 등록 정보. 카드마다 `has_benefit_rule`, `source`, `last4`, `removable`, 응답에 `can_add` |
| O | `GET /orchestrator/my-cards/catalog` | E 카탈로그 그대로 |
| O | `POST /orchestrator/mydata/consents` | 동의 (본문 없음) |
| O | `GET /orchestrator/mydata/cards?consent_id=` | 카드 목록 |
| O | `POST /orchestrator/my-cards` | 카드 추가 (본문에 `user_id`를 받지 않는다. 토큰의 사용자) |
| O | `DELETE /orchestrator/my-cards/{id}` | 카드 빼기 |
| C | C-02 응답 항목 | 선택 필드 `has_benefit_rule` 추가 |

카드 추가 본문 (E는 `user_id`가 더 붙는다):
- 직접 입력, 혜택 규칙 있는 카드: `{"source":"MANUAL","product_id":"PRD-CARD-B"}`
- 직접 입력, 그 밖의 카드: `{"source":"MANUAL","name":"삼성 iD ON 카드","issuer":"삼성카드","last4":"1234"}` (`issuer`·`last4`는 선택)
- 모의 마이데이터: `{"source":"MYDATA","consent_id":"MDC…","mydata_card_id":"MD-CARD-004"}`

## 규칙
- **혜택 규칙이 있는 카드**(`PRD-CARD-A/B/C`)는 한 사람이 같은 상품을 **한 장만** 가진다. 이미 있으면 409 `ALREADY_REGISTERED`. 처음부터 있던 카드(시드)도 센다.
  U001은 세 장을 이미 갖고 있어서, 시연에서 새로 넣을 카드가 있는 쪽은 **U002**다.
- **그 밖의 카드**는 상품 ID `PRD-EXT-CARD`로 들어간다. 같은 카드(이름+끝 4자리, 또는 마이데이터 카드 ID)는 두 번 넣지 못한다.
- 마이데이터로 넣은 카드의 "이번 달 이용 금액"이 이번 달 실적(`monthly_performance_krw`)이 된다. 직접 입력은 0원이다.
- 빼기는 **직접 추가한 카드만** 된다 (처음부터 있던 카드는 409 `NOT_REMOVABLE`). **결제 내역이 있으면** 장부가 어긋나지 않게 409 `HAS_PAYMENTS`.
- 동의 오류: 없거나 남의 동의 404 `CONSENT_NOT_FOUND` (남의 동의는 있는지도 알려 주지 않는다), 시간이 지나면 410 `CONSENT_EXPIRED`.
- 알 수 없는 필드·카드 번호 전체(숫자 4자리가 아닌 `last4`)는 400 `VALIDATION_ERROR`.
- E(`E_PAYMENT_URL`·`E_BASE_URL`)를 연결하지 않으면(가짜 E) 목록은 보이지만 추가·빼기는 503 `MY_CARDS_UNAVAILABLE`.

## 기존 동작이 바뀌는 곳 (팀 합의 때 확인할 점)
1. **C-01 추천: 혜택 규칙이 없는 카드(`RULE_NOT_FOUND`)를 "제외" 목록으로 보낸다.**
   전에는 "할인 0원 선택지"로 남았는데, C-05는 규칙이 없으면 404라서 D·B가 결제를 막는다. 처음부터 선택지로 보이지 않게 했다.
   (`MIN_ORDER_CHECK.md`의 "남은 불일치" 중 하나였다.) 시드 카드는 모두 규칙이 있어서 지금 시연에는 영향이 없다.
   바꾼 곳: `benefit/domain.py`의 `EXCLUDE_CODES`. 화면 문구: `lib/format.ts`의 `EXCLUDE_REASON`.
2. **C-02 응답 항목에 `has_benefit_rule`이 늘었다.** 선택 필드라 기존 소비자(D 등)는 영향이 없다. O-10 응답은 그대로다.

## 화면
- 첫 화면 → "Agent에게 맡길 범위 · 변경" → "쓸 수 있는 결제수단" 옆 **내 카드 관리 ›**
- 내 카드 시트: 카드 목록(어떻게 추가됐는지, 혜택 규칙 없음 표시, 빼기) → **+ 카드 추가**
  → ① 마이데이터로 불러오기(동의 → 카드 고르기) ② 직접 입력
- 새로 넣은 카드는 맡길 범위 목록에 바로 나온다. 혜택 규칙 있는 카드는 같은 상품의 카드 이미지를 쓴다.

## 시연 순서 (U002)
1. U002로 로그인 → 맡길 범위 → 내 카드 관리 → 카드 추가 → 마이데이터로 불러오기 → 동의
2. `신한카드 구독 좋아요` 추가 (혜택 규칙 있음), `KB국민 노리2 체크카드` 추가 (혜택 규칙 없음)
3. 맡길 범위에서 새 카드를 체크하고 주문 → 결제수단 추천에 **구독 좋아요**만 나오고 KB 카드는 제외된다
4. 처음 시연으로 되돌리려면 내 카드 시트에서 추가한 카드를 빼거나 `e.db`를 지운다 (결제한 카드는 못 뺀다)

## 시험
- E: `e-payment/tests/test_cards.py` · O: `backend/tests/orchestrator/test_my_cards.py` · C: `tests/benefit/test_domain.py`·`test_api.py`의 규칙 없는 카드 시험
