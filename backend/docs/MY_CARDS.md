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
| E | `GET /internal/card-catalog` | 직접 입력으로 고를 수 있는 "혜택 정보를 아는 카드" 6장 (`products[]`, 카드마다 `has_benefit_rule`) |
| E | `POST /internal/mock-mydata/consents` | 모의 마이데이터 정보제공 동의 (10분 유효) → `consent_id` |
| E | `GET /internal/mock-mydata/cards?user_id&consent_id` | 가진 카드 목록 (`has_card_info`, `has_benefit_rule`, `already_added` 포함) |
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
- 직접 입력, 혜택 정보를 아는 카드: `{"source":"MANUAL","product_id":"PRD-CARD-B"}`
- 직접 입력, 그 밖의 카드: `{"source":"MANUAL","name":"롯데 LOCA 카드","issuer":"롯데카드","last4":"1234"}` (`issuer`·`last4`는 선택)
- 모의 마이데이터: `{"source":"MYDATA","consent_id":"MDC…","mydata_card_id":"MD-CARD-004"}`

## 규칙
- **혜택 정보를 아는 카드**(`PRD-CARD-A/B/C/D/E`)는 한 사람이 같은 상품을 **한 장만** 가진다. 이미 있으면 409 `ALREADY_REGISTERED`. 처음부터 있던 카드(시드)도 센다.
  U001은 신한카드 세 장을 이미 갖고 있어서, 시연에서 새로 넣을 카드가 있는 쪽은 **U002**다.
- **그 밖의 카드**(정보를 모르는 카드, 예: 롯데 LOCA)는 상품 ID `PRD-EXT-CARD`로 들어간다. 같은 카드(이름+끝 4자리, 또는 마이데이터 카드 ID)는 두 번 넣지 못한다.
- 마이데이터로 넣은 카드의 "이번 달 이용 금액"이 이번 달 실적(`monthly_performance_krw`)이 된다. 직접 입력은 0원이다.
- 빼기는 **직접 추가한 카드만** 된다 (처음부터 있던 카드는 409 `NOT_REMOVABLE`). **결제 내역이 있으면** 장부가 어긋나지 않게 409 `HAS_PAYMENTS`.
- 동의 오류: 없거나 남의 동의 404 `CONSENT_NOT_FOUND` (남의 동의는 있는지도 알려 주지 않는다), 시간이 지나면 410 `CONSENT_EXPIRED`.
- 알 수 없는 필드·카드 번호 전체(숫자 4자리가 아닌 `last4`)는 400 `VALIDATION_ERROR`.
- E(`E_PAYMENT_URL`·`E_BASE_URL`)를 연결하지 않으면(가짜 E) 목록은 보이지만 추가·빼기는 503 `MY_CARDS_UNAVAILABLE`.

## 카드 혜택 정보 (신한카드 3장 말고 세 장)
| 상품 | 카드 | 배달 즉시할인 규칙 | 화면에 보이는 것 |
|---|---|---|---|
| `PRD-CARD-D` | KB국민 노리2 체크카드 (KB Pay) | 있음 — `RULE-CARD-D-DELIVERY`: 건당 1만원 이상 결제 시 **1,000원** 할인, 전월 이용실적 **20만원** 이상, 월 한도 1,000원(= 월 1회) | 배달앱 1,000원 할인, 커피·모바일·공연 10%, 뷰티·편의점 5%, KB Pay 2% 추가 |
| `PRD-CARD-E` | 현대카드 ZERO Up (포인트형) | **없음** — 적립형이라 결제 금액(`charge_krw`)이 줄지 않는다 | 온라인몰·대형마트·교육·주유·이동통신 요금 2.4% 적립, 국내외 가맹점 1.2% 적립 |
| `PRD-CARD-F` | 요기요 삼성카드 | 있음 — `RULE-CARD-F-DELIVERY`: 요기요 **10%** 결제일할인, 전월 이용금액 **30만원** 이상, 월 한도 **6,000원**(30만원대 구간) | 요기요 10%(구간별 월 한도 6천·1.2만·2만원), 요기요 1%(30만원 미만, 월 5천원), 커피전문점·편의점·생활잡화 5%(통합 월 한도 3천·6천·1만원) |
- 두 카드 정보는 신한카드 3장처럼 공식 페이지를 직접 확인한 게 아니라 **팀에서 가져온 카드 안내문**을 그대로 정리했다 (2026-11-24, `products.py`). 연회비·발급 조건은 안내문에 없어 비워 뒀다.
- 요기요 삼성카드는 **결제일할인**(카드 대금 청구 때 빠지는 할인)이다. 오디고는 다른 카드와 같이 "바로 줄어드는 할인"으로 계산하고, 화면 안내(`notes`)에 적어 뒀다.
  규칙 모델이 구간별 월 한도·전월 실적 상한을 못 다뤄서 **30만원 이상 구간(10%, 월 6,000원)만 규칙으로 넣었다.** 60만·90만원 구간 한도와 30만원 미만 1% 할인은 화면 정보로만 보인다
  (전월 실적이 30만원 미만이면 추천에서 `PERFORMANCE_NOT_MET`로 빠진다). 발급월+1개월 혜택 유예도 반영하지 않았다.
- 노리2 규칙은 "배달의민족·요기요 모두"가 아니라 **주문 전체에 적용**한다. 오디고가 다루는 주문이 요기요라서 앱을 따로 가르지 않았다.
- 노리2의 "월 1회"는 월 한도(1,000원)로 표현한다. 결제할 때마다 받은 할인이 "이번 달 받은 할인"에 쌓이므로(아래 "이번 달 남은 혜택") 한 번 받으면 남은 혜택이 0원이 되어 같은 달 두 번째 결제는 할인이 없다.
- 카드 혜택 화면(`has_benefit_rule`)이 `false`면 "실적 미달" 대신 **이번 달 이용액**과 "배달 할인 계산에 쓰이지 않아요" 안내를 보여 준다.
- 예전에 마이데이터로 넣은 `KB국민 노리2 체크카드 (1209)`·`현대카드 ZERO (3340)`는 정보 없는 카드(`PRD-EXT-CARD`)로 들어가 있어서, 내 카드 시트에서 빼고 다시 추가해야 새 정보가 붙는다.

## 기존 동작이 바뀌는 곳 (팀 합의 때 확인할 점)
1. **C-01 추천: 혜택 규칙이 없는 카드(`RULE_NOT_FOUND`)를 "제외" 목록으로 보낸다.**
   전에는 "할인 0원 선택지"로 남았는데, C-05는 규칙이 없으면 404라서 D·B가 결제를 막는다. 처음부터 선택지로 보이지 않게 했다.
   (`MIN_ORDER_CHECK.md`의 "남은 불일치" 중 하나였다.) 시드 카드는 모두 규칙이 있다. 규칙이 없는 건 정보를 모르는 카드와 현대카드 ZERO Up뿐이다.
   바꾼 곳: `benefit/domain.py`의 `EXCLUDE_CODES`. 화면 문구: `lib/format.ts`의 `EXCLUDE_REASON`.
2. **C-02 응답 항목에 `has_benefit_rule`이 늘었다.** 선택 필드라 기존 소비자(D 등)는 영향이 없다. O-10 응답은 그대로다.
3. **결제가 승인되면 C의 "이번 달 받은 할인"이 늘고, 환불이 끝나면 줄어든다.** (아래 "이번 달 남은 혜택") 전에는 고정 시드라서 결제해도 남은 혜택이 그대로였다.
   한도를 다 쓰면 다음 C-01·C-05 계산은 그 카드를 `BENEFIT_LIMIT_EXCEEDED`(할인 0원)로 본다. 바꾼 곳: `benefit/service.py`(`record_benefit_usage`·`reverse_benefit_usage`), `benefit/facade.py`, `orchestrator/wiring.py`, `orchestrator/service.py`.

## 화면
- 첫 화면 → "Agent에게 맡길 범위 · 변경" → "쓸 수 있는 결제수단" 옆 **내 카드 관리 ›**
- 내 카드 시트: 카드 목록(어떻게 추가됐는지, 혜택 규칙 없음 표시, 빼기) → **+ 카드 추가**
  (마이데이터 목록의 표시: "배달 할인 계산에 쓰여요" / "혜택 정보만 보여요" / "혜택 정보 없음")
  → ① 마이데이터로 불러오기(동의 → 카드 고르기) ② 직접 입력
- 새로 넣은 카드는 맡길 범위 목록에 바로 나온다. 카드 이미지는 상품별로 쓴다 (`frontend/public/cards/`: 신한 3종은 `CARD001~003`, 노리2·ZERO Up·요기요 삼성카드는 `PRD-CARD-D·E·F.png`).
  결제수단 선택 화면에서도 같은 이미지가 보인다. 정보 없는 카드는 이름이 적힌 기본 카드 모양이다.

## 시연 순서 (U002)
1. U002로 로그인 → 맡길 범위 → 내 카드 관리 → 카드 추가 → 마이데이터로 불러오기 → 동의
2. `신한카드 구독 좋아요`, `KB국민 노리2 체크카드`(배달 1,000원 할인), `요기요 삼성카드`(요기요 10%), `현대카드 ZERO Up`(적립형, 정보만) 추가
3. 맡길 범위에서 새 카드를 체크하고 주문 → 결제수단 추천에 **구독 좋아요**·**노리2**·**요기요 삼성카드**가 나오고 ZERO Up은 제외된다
   (`RULE_NOT_FOUND`: "혜택 규칙 없는 카드"). 설정 화면에서 카드 ⋮ 를 누르면 두 카드의 혜택 정보를 볼 수 있다
4. 처음 시연으로 되돌리려면 내 카드 시트에서 추가한 카드를 빼거나 `e.db`를 지운다 (결제한 카드는 못 뺀다)

## 시험
- E: `e-payment/tests/test_cards.py` · O: `backend/tests/orchestrator/test_my_cards.py` · C: `tests/benefit/test_domain.py`·`test_api.py`의 규칙 없는 카드·노리2·ZERO Up 시험
- 남은 혜택 차감: C `tests/benefit/test_usage.py` · O `tests/orchestrator/test_benefit_usage.py` (진짜 C로만: `TEST_REAL_C=1 pytest tests/orchestrator/test_benefit_usage.py`)

## 이번 달 남은 혜택 (로컬 확장)
화면의 "이번 달 남은 혜택" = **규칙의 월 한도 − 이번 달 이미 받은 할인**이다.
- 월 한도: C 규칙(`monthly_cap_krw`). 신한 요기요 6,000원 · 구독 좋아요 2,000원 · Deep On 10,000원 · 노리2 1,000원 · 요기요 삼성 6,000원. 서울페이·적립형 카드는 한도가 없어 표시하지 않는다.
- 이미 받은 할인: C의 `seed.BENEFIT_USED_KRW`(결제수단별 합계). 시드는 신한 요기요 체크(`CARD001`) 2,000원이고 나머지는 0원이다.
- **결제가 승인되면** O가 `C.record_usage(payment_id, 카드, 할인액)`을 불러 그만큼 더한다 (`_apply_payment_result`의 APPROVED). 같은 `payment_id`는 한 번만 반영되므로 reconcile이 같은 결과를 다시 알려 줘도 두 번 깎이지 않는다.
- **환불이 `REFUNDED`로 끝나면** O가 `C.reverse_usage(payment_id)`로 그 결제로 받았던 할인만 되돌린다 (카드 혜택 화면의 환불 버튼은 남은 금액 전부를 환불한다). 접수만 된 `PENDING`은 되돌리지 않는다.
- 분할결제의 할인은 카드 몫에 붙는다 (충전식 서울페이는 할인이 없다). C가 이 기능을 모르거나 실패해도 결제는 그대로 완료되고, 실패는 `C-USAGE` 서비스 오류로 기록된다.
- **한계**: C는 아직 메모리 저장이라 **서버를 다시 켜면 시드값으로 돌아간다** (E의 실적·잔액은 `e.db`에 남는다). 달이 바뀌어도 자동으로 비우지 않는다. 진짜로 쓰려면 E-11 원장이 카드별 이번 달 받은 할인을 내려 주는 쪽이 맞다 (팀 협의 사항).
- 시연을 처음으로 되돌리려면 백엔드를 다시 켠다.

## 서울페이는 충전식 (할인 없음)
`PRD-LOCAL-A`(서울페이, 서울사랑상품권)는 **할인·적립 없이 충전한 잔액에서 쓴 만큼 차감**한다 (파주페이 같은 충전식).
- C 규칙 `RULE-LOCAL-A-PREPAID`는 할인 0원이다. 규칙을 아예 없애면 `RULE_NOT_FOUND`로 추천에서 빠지고 C-05가 404라서 0원 규칙을 둔다.
- 추천 사유는 "충전한 잔액에서 사용한 만큼 차감"이고, 월 할인 한도(`monthly_benefit_remaining_krw`)는 없다 (null).
- 그대로 두는 것: 가맹점 확인(`LOCAL_CURRENCY_NOT_ACCEPTED`·`UNVERIFIED`), 잔액 부족 제외(`LOCAL_CURRENCY_INSUFFICIENT_BALANCE`), 분할결제, E의 잔액 차감.
- 명세 예시(C-01 지역화폐 5% 즉시할인 1,000원)와 달라졌으므로 팀 합의 때 알려야 한다.
- 이 앱에는 **잔액을 채우는 충전 기능이 없다**. 잔액은 E 시드(U001 12만원, U002 1천원)에서 시작해 쓴 만큼 줄고, 처음으로 되돌리려면 `e.db`를 지운다.
