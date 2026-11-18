# B 주문 모듈 구현 현황과 팀 연결 확인 요청

2026-11-16 · 공유용 1.0 · B 담당 이종혁

**공유 목적:** B-01~B-09의 구현 범위와 연결 위치를 전달하고, 실제 팀 코드 통합 전에 결정해야 할 계약을 확인한다. 이 문서의 “현재 구현”은 코드에서 확인한 사실이고, “제안”과 “확인 요청”은 아직 팀 합의가 아니다.

## 1. 먼저 확인할 내용

- B API 9개를 로컬 구현했다. Python 3.14.3·PostgreSQL 18.7에서 자동 검사 320개, 보조 SQLite에서도 320개 통과했다. 합성 카탈로그·C·D·E 결과를 사용한 실제 로컬 HTTP 흐름도 확인했다.
- 실제 팀 O·카탈로그·A·C·D·E 코드를 합친 상태는 아니다. 실제 D 승인 발급·소비, E 결제·환불, 프런트엔드는 검증하지 않았다.
- 우선 확인할 계약은 **D-04의 원본 해시 전달**, **O의 user_id·policy_version 전달**, **B-03의 수정·질문·최초 미완성 주문 범위**, **B-04의 최신 조회·실패 응답**이다. 확인 요청은 4절과 5절에 모았다.
- 기준 명세는 `API명세서_수정안_20261115.xlsx`다. 원문 A2에 “팀 합의 전 초안”으로 표시되어 있어 구현상의 빈 부분을 팀 확정 규칙으로 표현하지 않았다.

| 실행 기준 | 상태 |
| --- | --- |
| Python 3.14.x / PostgreSQL 18.x | B는 3.14.3 / 18.7에서 검증 |
| Java 25 / Spring Boot 4.1.x | E의 통합 목표. E 코드 빌드·실행은 미검증 |
| Node.js 26.x | 프런트엔드 통합 목표. 현재 B 작업에 UI 구현 없음 |

## 2. 구현한 API와 연결 흐름

경로 앞에는 `/ordering`을 사용한다. health를 제외한 HTTP 호출은 서비스 토큰과 `X-Request-ID`가 필요하다. 화면은 O만 호출하고, 같은 FastAPI 서버 안의 O→B는 아래 공개 함수를 사용한다.

| API | HTTP 경로 | OrderingAPI 공개 함수 | 구현한 결과 |
| --- | --- | --- | --- |
| B-01 | POST /ordering/order-plans | create_order_plan | 메뉴·옵션·수량 검증, 할인 전 견적. 생성 201 / 생성 불가 200 |
| B-02 | GET /ordering/order-plans/{order_plan_id} | get_order_plan | 현재·과거 견적 조회. 조회 중 재계산 없음 |
| B-03 | PATCH /ordering/order-plans/{order_plan_id} | update_order_plan | 보완 답변·메뉴·수량 수정, 새 버전·견적, 이전 B 대상 무효화 |
| B-04 | POST /ordering/order-plans/{order_plan_id}/revalidations | revalidate_order_plan | 최신 가격·품절 확인, 만료 갱신, 재견적 또는 재탐색 안내 |
| B-05 | POST /ordering/order-plans/{order_plan_id}/benefit-bindings | bind_benefit | C-05 계산 결과 연결, 할인 후 예산 판정 |
| B-06 | POST /ordering/order-plans/{order_plan_id}/approval-targets | prepare_approval_target | D 검증용 승인 대상 스냅샷 생성, 201 |
| B-07 | POST /ordering/order-plans/{order_plan_id}/execution-checks | check_execution | 현재 B 상태와 D-04 관측 대조, 결제 전달 자료 준비 |
| B-08 | POST /ordering/order-plans/{order_plan_id}/payment-events | apply_payment_event | E 결과를 원래 실행·견적에 연결, 중복·미확정·충돌 처리 |
| B-09 | GET /ordering/health | health | 무인증 200 UP / 503 DOWN. B 저장소 조회 가능 상태 확인 |

기본 주문 흐름은 다음과 같다. **O가 전체 순서를 조율**한다.

```text
A 조건 확정 → O가 D-01 등록 → 신뢰된 사용자·정책 문맥 구성
→ B-01 → O가 C-01 추천 → 사용자 결제수단 선택 → B-05
→ B-06 → O가 D-02 검증·D-03 사용자 승인 → B-07
→ O가 E-01 HTTP 호출 → E가 D-06 최종 검증·승인 사용
→ O가 E 결과를 변환해 B-08 전달 → B-02 조회
```

수정·재견적 성공은 `B-03 또는 B-04 → C-01 → B-05 → B-06 → D-02 재검증·사용자 승인 → B-07`로 이어간다. 새 견적의 혜택은 PENDING_BENEFIT이며 이전 혜택을 자동 복사하지 않는다.

B는 이전 **B 승인 대상**을 SUPERSEDED로 바꾼다. 실제 D 승인의 철회·발급은 O·D의 D-01/D-02 절차다. B-07의 ready=true는 B의 준비 여부이며 실제 결제 허가는 E→D-06이 담당한다. 기존 결제·환불은 주문 수정으로 취소되지 않는다.

O는 HTTP 200만 보고 다음 단계로 진행하면 안 된다. `next_action`, `ready`, `application_status`, 결제 상태를 함께 분기해야 한다. B-02의 기본 응답은 원문 예제 형식을 유지하며 `include_payment=true`와 `include_supplements=true`는 로컬 선택 확장이다. B-02가 실제 D의 현재 승인 상태까지 조회해 준다고 가정하면 안 된다.

## 3. 코드 연결 위치

아래 경로는 **backend 폴더 기준**이다. 팀 코드에서는 B의 ORM 내부 대신 공개 API·포트를 통해 연결한다.

| 연결 대상 | 파일·진입점 | 팀에서 연결할 내용 |
| --- | --- | --- |
| O→B | app/modules/ordering/api.py · OrderingAPI | 요청 DTO·CallContext·멱등 키로 공개 함수 호출 |
| 의존성 조립 | app/modules/ordering/bootstrap.py · build_ordering | 팀 세션 팩토리와 카탈로그·C·D 어댑터 주입 |
| 요청·응답 | app/modules/ordering/schemas.py | 필드명·타입·상태·누락 허용 여부를 이 DTO와 대조 |
| 공용 사용자 문맥 | app/core/context.py · CallContext | O의 인증 사용자와 D-01 정책 버전 전달 |
| 카탈로그 지정 판본 | app/modules/ordering/ports.py · CatalogReader.read_restaurant | 식당 ID·catalog_version에 해당하는 메뉴/옵션/가격 스냅샷 조회 |
| B-04 최신 카탈로그 | app/modules/ordering/ports.py · LatestCatalogReader.read_latest_restaurant / app/modules/ordering/adapters/latest_catalog.py | 호출 시점의 현재 스냅샷. CallableLatestCatalogReader에 팀 조회 함수 주입 |
| C-05 | app/modules/ordering/adapters/benefit.py · CallableBenefitCalculator | calculate(BenefitCalculationRequest) 입력·결과 변환. B는 할인 규칙을 직접 계산하지 않음 |
| D-04 | app/modules/ordering/adapters/delegation.py · CallableApprovalReader | read_approval(approval_id, context)로 새 상태 관측. D 원본 해시 전달 계약 필요 |
| O가 받은 E 결과 | app/modules/ordering/adapters/payment.py | payment_reference / payment_event_from_e / payment_event_for_timeout. 순수 변환이며 E HTTP 클라이언트는 O 담당 |
| 팀 FastAPI 앱 | app/modules/ordering/router.py · create_router | 필요 시 기존 앱에 B router 등록. B의 독립 main.py로 팀 main을 덮어쓰지 않음 |
| DB·오류·migration | app/core/database.py, app/core/errors.py, migrations/ | 팀 Base·세션·AppError와 통일하고 migration 이력 연결 |

### 조립 예시

아래는 연결 위치를 보여 주는 코드다. 콜백은 실제 팀 함수에 맞게 작성해야 하며 현재 B DTO 형식으로 정규화한 값을 반환해야 한다. 지정 판본 조회의 실패도 공통 AppError로 변환한다.

```python
from app.modules.ordering.bootstrap import build_ordering
from app.modules.ordering.schemas import CatalogRestaurant
from app.modules.ordering.adapters.latest_catalog import CallableLatestCatalogReader
from app.modules.ordering.adapters.benefit import CallableBenefitCalculator
from app.modules.ordering.adapters.delegation import CallableApprovalReader

def assemble_b(team_sessions, settings, read_revision,
               read_latest, calculate_c05, read_d04):
    class TeamCatalogReader:
        def read_restaurant(self, restaurant_id, catalog_version):
            raw = read_revision(restaurant_id, catalog_version)
            return CatalogRestaurant.model_validate(raw)

    return build_ordering(
        team_sessions, settings,
        catalog=TeamCatalogReader(),
        latest_catalog=CallableLatestCatalogReader(read_latest),
        benefit_calculator=CallableBenefitCalculator(calculate_c05),
        approval_reader=CallableApprovalReader(read_d04),
    )
```

콜백 시그니처는 지정 판본 `(restaurant_id, catalog_version)`, 최신 조회 `(restaurant_id, evidence_ref)`, C-05 `(request)`, D-04 `(approval_id, context)`다. 현재는 동기 함수 기준이다. 팀 코드가 async면 호출 방식과 세션 경계를 함께 조정해야 한다.

최신 조회만 연결하고 지정 판본 조회는 구 판본만 지원하면 B-04 이후 B-03이 새 판본을 읽지 못한다. 두 조회가 같은 판본 체계를 사용해야 한다. 개발 fixture 역시 판본·금액을 명시적으로 준비하며 실제 연결 오류를 fixture 성공으로 대체하지 않는다.

## 4. 우선 확인: D-04 해시와 사용자·정책 문맥

### 4-1. D-04 proposal_hash

**원문 근거:** B-07 J32는 proposal_hash를 요구한다. D-02 J49에는 해시가 있고 D-04 J51 응답에는 없다.

**현재 구현:** ApprovalStatus는 D 원본 해시를 선택적으로 받는다. 해시가 없으면 B-07은 `ready=false`, `APPROVAL_HASH_UNAVAILABLE`로 멈춘다. B가 금액·메뉴를 해시해 임의의 승인 원본을 만들지 않는다. 실제 형식은 `sha256:` 뒤 64자리 16진수다. 원문 `sha256:3f2a…`는 축약 예시이므로 그대로 보낼 수 없다.

**제안:** D가 승인 생성 시 저장한 동일 원본 해시를 D-04에 포함한다. 다른 방식을 택한다면 O가 저장한 D-02 원본과 같은 approval_id의 새 D-04 상태를 어떻게 결합할지 명시한다. 상태 자체를 과거 응답으로 캐시해 철회·만료 확인을 생략하지 않는다. 이 대안 연결은 아직 구현·검증하지 않았다.

| 확인 요청 | 확인이 필요한 역할 | 답변 기록 |
| --- | --- | --- |
| D-04에 proposal_hash를 추가할지, D-02 원본 연결 방식을 쓸지 | O·D·B | 미정 |
| D-02와 D-04의 같은 approval_id가 동일 해시를 보장하는지 | D | 미정 |
| 해시가 없는 경우 현재의 ready=false 처리를 유지할지 | O·D·B | 미정 |

### 4-2. user_id·policy_version

**원문 근거:** C-05 I40·D-02 I49·E-01 I58은 사용자/정책 문맥을 요구한다. 공통 B21은 최신 policy_id·policy_version 전달을 요구하지만 B-01 I26에는 user_id·policy_version 필드가 없다.

**현재 구현:** 외부 B-01 body를 유지하고 같은 서버의 신뢰된 CallContext로 user_id·policy_version을 전달받아 저장한다. B-01의 policy_id와 문맥의 policy_version은 같은 D-01 결과여야 한다. 실제 O는 B-01부터 인증 사용자를 전달해야 한다.

```python
from app.core.context import CallContext

# 아래 이름은 O의 인증·D-01 응답에 연결할 값이며 실제 팀 객체 예시다.
ctx = CallContext(
    request_id=trusted_request_id,
    user_id=authenticated_user_id,
    policy_version=d01_result["policy_version"],
)
# B-01 body.policy_id에는 같은 d01_result["policy_id"]를 사용한다.
```

사용자가 없는 기존 주문에 임의 사용자를 채워 넣지 않는다. B-05 이후에는 저장된 사용자와 호출 사용자 일치가 필요하다. 저장된 policy_version이 없으면 `get_delegation_validation_request`가 POLICY_CONTEXT_REQUIRED로 D-02 입력 생성을 거부한다. 나중 문맥의 버전을 과거 주문에 자동 대입하지 않는다. 독립 HTTP 데모의 고정 사용자 설정만으로 정책 버전이 저장되지는 않는다.

O는 B-06 뒤 `get_delegation_validation_request(order_plan_id, approval_target_id, ctx)`로 D-02 입력을 받아 실제 D 함수에 전달한다. B가 D-02를 직접 호출하거나 승인을 발급하지 않는다.

E-01에는 B-07 execution_input에 없는 user_id가 추가로 필요하다. 인증 사용자가 있는 O가 ready=true일 때만 아래 body를 구성한다. 실제 E HTTP 호출과 예외 처리 코드는 O에 연결한다.

```python
if checked.ready and checked.execution_input is not None:
    e_body = {
        **checked.execution_input.model_dump(mode="json"),
        "user_id": ctx.user_id,
    }
    # O가 E-01 호출. E-01 Idempotency-Key는 execution_id다.
```

| 확인 요청 | 확인이 필요한 역할 | 답변 기록 |
| --- | --- | --- |
| 같은 서버의 CallContext 전달 방식을 공통으로 사용할지 | O·B·C·D | 미정 |
| B-01부터 인증 user_id와 D-01의 policy_id/version을 누가 조립할지 | O | 미정 |
| 조건 변경 시 D-01 재등록·새 B-01 입력·새 멱등 키 구성 | O·B·D | 미정 |
| E-01 body에 O가 user_id를 추가하는 방식 | O·E | 미정 |

현재 B-03·B-04는 조건·정책을 보존한다. 정책 버전이 바뀐 경우 기존 주문을 PATCH로 덮어쓰지 않는다. 같은 후보라도 조건·정책·카탈로그 판본이 바뀌면 기존 B-01 키를 재사용할 수 없으므로 새 키 규칙을 함께 정해야 한다.

## 5. 확인 요청: B-03·B-04 세부 계약

### 5-1. B-03 주문 변경

**원문 근거:** API 명세 28행은 ANSWER_SUPPLEMENT 예시와 “메뉴·수량 변경”을 기술하지만 일반 수정 body, 질문 발급·조회, 최초 미완성 주문 상태는 정의하지 않았다.

아래는 **현재의 로컬 계약**이다.

| 항목 | 현재 동작 |
| --- | --- |
| 일반 변경 | mode=REPLACE_ITEMS와 최종 전체 items. 빠진 항목은 제거되며 다른 mode의 answers와 혼용 불가 |
| 보완 답변 | mode=ANSWER_SUPPLEMENT, answers의 question_id·selected_values. 답한 옵션 그룹만 변경 |
| 질문 조회 | B-02 include_supplements=true. 저장된 질문을 읽으며 재계산 없음 |
| 질문 범위 | 견적·항목·그룹에 묶인 B 발급 ID. 새 견적의 질문 ID를 다시 받아야 함 |
| 성공 | 200, 새 order_version·quote_id, 혜택 초기화, 이전 B 대상 무효화, CALCULATE_BENEFITS |
| 업무상 불가 | 200 + RESEARCH_REQUIRED + issues. view는 이전 주문이며 견적·대상 유지. 해당 응답도 멱등 저장 |
| 입력 오류·충돌 | 잘못된 자료형·중복 옵션 422, 오래된 버전/질문 409. 만료 견적은 B-04로 재검증 |
| 최초 미완성 주문 | B-01이 생성 실패하면 주문안이 없어 B-03으로 이어질 대상이 없음. 현재는 유효한 기존 주문 수정만 지원 |

일반 수정 예시의 ID는 합성 값이며 실제 조회 값으로 교체한다.

```json
{
  "base_order_version": 1,
  "mode": "REPLACE_ITEMS",
  "items": [{"menu_id": "MENU011", "option_ids": ["OPT001"], "quantity": 2}]
}
```

**확인할 결정:** REPLACE_ITEMS 이름·전체 교체 방식, 질문 DTO와 B 발급 주체, 업무 실패 200 본문, 최초 draft 지원 필요 여부를 정한다. 최초 필수 옵션 누락도 B-03으로 완성해야 한다면 B-01/조회/혜택/승인에서 사용할 미완성 상태를 추가 설계해야 한다. 현재 지원한다고 가정하면 연결되지 않는다.

**통합 제안:** 우선 기존 유효 주문의 수정 범위를 합의하고, 최초 draft는 별도 상태 계약을 정한 뒤 확장한다. 최종 결정은 미정이다.

### 5-2. B-04 최신 데이터 재검증

**원문 근거:** API 명세 29행은 최신 가격·품절·만료 재검증과 실패 시 재탐색을 요구한다. reason/evidence_ref의 전체 규칙, 최신 조회 함수, 실패 응답의 세부 필드는 정의하지 않았다.

아래는 **현재의 로컬 계약**이다.

| 항목 | 현재 동작 |
| --- | --- |
| 입력 | base_order_version·현재 quote_id·reason. reason은 PRICE_CHANGED/SOLD_OUT/QUOTE_EXPIRED, evidence_ref는 선택 |
| 최신 조회 | LatestCatalogReader의 현재 권위 있는 스냅샷. evidence_ref는 근거 힌트이며 과거 판본 선택자가 아님 |
| 성공 | 200, 새 버전·견적·만료·질문, PENDING_BENEFIT, 이전 B 대상 무효화. 가격이 같아도 새 견적 생성 |
| 주문 불가 | 200 + INVALID + RESEARCH_REQUIRED + issues + research_request. 이전 버전·가격·혜택·결제 보존 |
| 재탐색 정보 | research_request에 reason·restaurant_id·menu_ids·current_price_krw. 영업 불가는 RESTAURANT_CLOSED, 나머지는 SOLD_OUT으로 요약. 선택 메뉴 전체, 금액은 null |
| 실패 뒤 진행 | B-03·B-05·B-06 차단, B-07 ready=false. O가 A-07 재탐색 또는 새 B-04로 정상 상태 확인 |
| 복구 | 새 키 재검증 성공 시 새 견적 생성. 과거 INVALID는 유지하며 이전 대상을 되살리지 않음 |
| 조회 오류 | 최신 자료를 확인하지 못하면 503 CATALOG_UNAVAILABLE, 기존 상태 보존. 확인 불가를 폐점으로 추정하지 않음 |
| 재전송·동시성 | 같은 키는 당시 결과 재생. 새 관측은 새 키. 오래된 버전/견적/동시 검사 결과는 409 |

```json
{
  "base_order_version": 1,
  "quote_id": "CURRENT_QUOTE_ID",
  "reason": "PRICE_CHANGED",
  "evidence_ref": "CAT-V2"
}
```

**확인할 결정:** 최신 판본 선택 주체, evidence_ref 의미, reason 목록, INVALID/실패 본문 채택, A-07 reason 매핑·메뉴 배제 범위를 정한다. 최신 판본과 지정 판본을 두 조회가 모두 지원해야 한다.

실패한 현재 주문은 B-03으로 바로 편집할 수 없다. 이 차단 정책을 유지할지, 최신 판본을 기반으로 수정할 수 있게 별도 흐름을 만들지도 확인이 필요하다. 현재 구현은 재탐색 또는 새 재검증 성공으로 복구한다.

## 6. DB·공용 앱을 합칠 때

- B 소유 테이블은 b_order_plans·b_quotes·b_benefit_bindings·b_approval_targets·b_execution_checks·b_payment_events·b_idempotency_records의 7개다. 다른 모듈 테이블을 직접 조인해 사용자·승인·결제 원본을 가져오지 않는다. 공용 수집 카탈로그는 읽기 포트로 연결한다.
- 독립 개발 migration 순서는 b01 → b06 → b07 → b03 → b04 → **20261116_review**다. 기존 팀 DB에 독립 루트 b01을 그대로 덮어 적용하지 말고 공용 이력 다음으로 연결한다. 마지막 revision은 견적별 재검증 기록과 과거 INVALID를 보존하며 금액·결제를 변경하지 않는다.
- 세션은 열린 Session 한 개 대신 **session factory**를 주입한다. B는 짧은 자체 트랜잭션을 사용한다. 공용 Base·AppError·CallContext를 팀 구현과 하나로 통일하고 sync/async 사용도 확인한다.
- B migration 검사는 B 모델 7개만 포함하도록 수정했으며 공용 Base에 추가한 팀 모델을 제외하는 검사를 통과했다. 실제 팀 schema·migration 이력까지 합친 검증은 별도다.
- .env.example은 참고이며 자동 로딩하지 않는다. 실제 설정·토큰은 공용 설정에서 주입한다. 문서에 실제 토큰을 넣지 않는다. 개발 fixture는 명시적으로 선택하며 production에서는 금지된다.

## 7. 결제 결과와 멱등 키 약속

O는 B-07 성공 뒤 `payment_reference(check_request, checked)`로 원래 견적·대상·실행 연결을 보관하고, 받은 E 결과를 `payment_event_from_e(...)`로 변환해 B-08에 전달한다. BLOCKED는 변환 결과 None이므로 B-08에 보내지 않는다. E 응답 유실을 관측했을 때만 payment_event_for_timeout으로 UNKNOWN 이벤트를 만든다.

| 대상 | 키·처리 |
| --- | --- |
| B-01 | 원문은 request_id:b01:candidate_id. 조건/판본 변경을 구분하는 새 키 규칙은 추가 합의 필요 |
| B-03·B-04 | 사용자 수정·새 관측마다 새 키. 재전송은 기존 키와 같은 본문 사용 |
| B-05 | 원문 키에 주문 버전·수단 포함. A→B→A 재선택에는 회차를 구분하는 새 키 필요 |
| B-06 | 주문 버전·혜택 회차 구분 |
| E-01 | execution_id |
| B-08 | event_id와 Idempotency-Key가 같아야 함. 새 관측은 새 event_id, 같은 이벤트 재전송은 같은 본문 |

UNKNOWN을 실패로 단정하거나 새 결제로 재시도하지 않는다. 원칙은 E-03 조회다. 원문의 E-03 K60에는 404 후 동일 execution_id 재요청 설명도 있어 공통 “재시도 금지”와 충돌한다. O·D·E가 규칙을 확정해야 하며 B에는 자동 재결제 로직을 추가하지 않았다.

미해결 결제 충돌은 CHECK_PAYMENT_STATUS와 B-02의 requires_reconciliation으로 알린다. 늦은 결과는 원래 견적에 남으며 새 견적의 결과로 옮기지 않는다. 반대 확정 결과의 교정·원장 대조 절차는 E와 합의해야 한다.

## 8. 팀 답변과 연결 순서

아래 역할은 확인이 필요한 상대를 표시한 것이며 담당자·기한을 새로 확정한 것은 아니다. 답변을 받으면 연동 기준 문서에 결정 근거와 판본을 반영한다.

| 순서 | 팀에서 확인할 답변·자료 | 연결 완료 판정 |
| --- | --- | --- |
| 1 | O·D: 해시 전달 방식, 인증 사용자/정책 문맥 | 실제 D-04 관측과 D 원본 해시로 B-07 준비 가능. 누락/철회/만료는 차단 |
| 2 | O·UI·B: B-03 수정·질문·최초 draft 범위 | 질문 ID 조회→답변→새 견적, 업무 불가/입력 오류 분기 확인 |
| 3 | 카탈로그·O·A·B: B-04 최신 조회·근거·실패/재탐색 계약 | 최신 재견적→같은 판본 수정, 품절→차단→재탐색/복구 확인 |
| 4 | 공용 앱 담당: import·오류·세션·migration·sync/async 방식 | 팀 DB에서 B 외 테이블 보존, B router/공개 API 호출 확인 |
| 5 | C·D·E·O: 실제 계산·승인·결제 결과 및 UNKNOWN 복구 | 실제 어댑터로 같은 시나리오 재실행. 최종 E→D-06·원장까지 별도 확인 |

## 9. 검증 근거와 참고 파일

이번 문서는 기존 검증 결과와 현재 코드 연결점을 대조해 작성했다. 문서 작성 때문에 전체 테스트를 다시 실행한 것은 아니다. 320개 통과 기록과 실제 HTTP 결과는 로컬 data 폴더에 있으며 Git 제외 파일이다. Markdown/HTML만 공유하면 실행 증거 파일·원본 Excel·코드가 자동 포함되지는 않는다.

| 자료 | backend 기준 위치 |
| --- | --- |
| 실행 방법·어댑터 조립 | README.md |
| 상세 연동 기준·미정 항목 D01~D14 | docs/integration/B_MVP1_INTEGRATION_GUIDE.md |
| 전체 검토·수정 근거 | docs/reviews/B_API_REVIEW_2026-11-16.md |
| 원문 예제 계약 | tests/ordering/fixtures/api_spec_20261115.json / tests/ordering/test_spec_contracts.py |
| 연결·동시성·복구 검사 | tests/ordering/test_revalidations.py 및 각 API 테스트 |
| 실제 로컬 HTTP 재현 | scripts/smoke_http.py |
| DB 테스트 증거 | data/pytest-postgresql18.xml / data/pytest-sqlite.xml |
| HTTP 실행 증거 | data/smoke-http-result.json |

독립 개발 환경에서 재현할 때는 README의 PostgreSQL 18 설치·테스트 DB 설정을 먼저 따른다. 핵심 확인 명령은 다음과 같다. 팀 운영 DB를 대상으로 개발용 smoke를 실행하지 않는다.

```powershell
uv sync --locked --python 3.14
uv run alembic upgrade head
# 테스트 전용 PostgreSQL URL을 ORDERING_TEST_POSTGRES_URL에 설정한 뒤 실행
uv run pytest -q
uv run alembic check
uv run python scripts/smoke_http.py
```

검증한 원본 Excel SHA-256은 `72f1157b63b243ed41517650d664ddb621864db2d88c2ba9f056537eac8b5889`다. 이 문서는 코드 연결·결정 요청용 인계 자료이며 명세나 팀 합의를 대신하지 않는다.
