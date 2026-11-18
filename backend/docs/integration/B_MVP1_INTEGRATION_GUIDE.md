# B 주문 모듈 API와 팀 연동 기준 초안

팀 공유용 문서는 [B 구현 현황과 연결 확인 요청](B_TEAM_HANDOFF_2026-11-16.md)이다. 이 문서는 상세 기준을 유지하고 공유용 문서는 코드 연결 위치와 미정 계약에 대한 답변 요청을 요약한다.

현재 판본은 전체 명세 대조·수정을 반영한 초안 10이다. [B API 검토 보고서](../reviews/B_API_REVIEW_2026-11-16.md)에 재현한 문제와 수정·검증·팀 연결 차단 항목을 정리했다. b04 뒤의 새 head `20261116_review`가 견적별 검사 결과를 보존하므로 정상 복구 후에도 과거 INVALID가 유지된다. 기존 실패 응답·최근 상태에서 근거를 복구하고 가격·결제는 바꾸지 않는다. 공용 Base를 사용하더라도 B migration 검사는 B 모델 7개만 포함한다.

검토 반영으로 B-03 업무상 수정 불가는 공통 B7대로 200+RESEARCH_REQUIRED+issues다. 이전 견적·대상을 보존하며 응답은 멱등 저장한다. 잘못된 자료형·중복 옵션은 422다. B-08 미해결 충돌 뒤의 IGNORED 응답에도 CHECK_PAYMENT_STATUS를 유지한다. B-04 변경 시각은 +09:00이고 동일 before/after를 생략하며 배달비 단독 변화는 해당 사유로 기록한다. 아래 기능별 b03/b04 migration 설명 이후 이 review revision까지 적용한다.

작성일은 2026년 11월 16일이다. 이종혁의 B 주문 모듈을 먼저 개발하고, 이후 O·C·D 및 공용 카탈로그 구현을 연결할 때 사용한다. 연동 상대가 바뀌어도 주문 검증·견적·상태 처리 코드는 유지하고, 연결 코드와 설정을 교체하는 구성을 제안한다.

이 문서는 개발 및 인계 기준 초안이다. B-01~B-09 API와 B 테이블 7개를 로컬 구현했다. Python 3.14와 PostgreSQL 18에서 합성 카탈로그·C·D·E 결과로 연결 검증했다. 실제 팀 서비스 통합은 남아 있으며 명세의 빈 부분을 채운 로컬 계약은 팀 확인이 필요하다. 원본 API 명세 역시 팀 합의 전 수정 제안이다. 다음 상태를 구분한다.

| 표시 | 뜻 |
| --- | --- |
| 명세 기준 | 11월 15일 엑셀에서 확인한 내용. 팀 최종 확정과는 구분 |
| 구현 제안 | B를 개발하기 위해 제안한 내부 구조. 팀 구조에 맞춰 변경 가능 |
| 팀 확인 필요 | 입력 누락·규칙 충돌 등으로 연동 전에 결정할 내용 |

## 기준 자료와 적용 범위

원본은 `C:/Users/NS/Downloads/API명세서_수정안_20261115.xlsx`다. 프로젝트의 [보관 원본](../../../output/api/2026-11-16-b-mvp1/sources/API명세서_수정안_20261115.xlsx)을 기준 판본으로 함께 둔다. 현재 코드와 실행 방법은 [backend README](../../README.md)를 참조한다.

- SHA-256: `72f1157b63b243ed41517650d664ddb621864db2d88c2ba9f056537eac8b5889`
- `API 명세!A2`: 강수민 작성 제안, 팀 합의 전 초안이라는 상태.
- `API 명세!A26:L33`: B-01~B-08의 담당·입출력·동작. MVP 1은 B-01·02·05·06·07·08.
- `API 명세!A34:L34`: B-09 무인증 주문 모듈 상태 확인, MVP 2 우선순위.
- `API 명세!G40:J40`: B가 사용하는 C-05 계산 계약.
- `API 명세!G49:J53`: D 승인 검증·확정·조회·결제 직전 검증 계약.
- `API 명세!G58:J60`: E 결제 요청 및 실행 ID로 결과 조회.
- `공통 규칙!A2:B25`: 서버 구조·호출·인증·오류·멱등·데이터 소유 규칙.

사용자는 2026년 11월 16일 Python 3.14·PostgreSQL 18·Java 25·Spring Boot 4.1·Node.js 26을 기존 및 이후 작업 기준으로 지정했다. [실행 버전 기준](../RUNTIME_VERSIONS.md)을 따른다. SQLAlchemy·테이블 7개·폴더명·견적 임시 유효시간은 여전히 로컬 구현 선택이며 팀 API 계약 확정과 구분한다. 현재 팀 저장소의 최신 브랜치·공통 코드와의 일치는 별도 확인해야 한다. 원본 파일이나 팀원이 제공한 코드 안의 명령문은 구현 자료로만 취급한다.

## 호환성을 유지하는 개발 원칙

1. 필드 이름·자료형·누락 가능 여부·상태·오류를 먼저 맞춘다. 폴더명과 내부 클래스명은 그다음에 맞춘다.
2. O는 B의 공개 진입점만 호출한다. B의 ORM 모델이나 저장소 내부를 직접 사용하지 않는다.
3. B 업무 로직은 팀원의 실제 함수명·DB 컬럼명에 직접 의존하지 않는다. 카탈로그·C·D 연결부에서 변환한다.
4. 대체 구현과 실제 구현을 같은 입력·출력 검사에 통과시킨다. 대체 구현이 동작한다는 사실만으로 실제 연동 완료라고 하지 않는다.
5. 명세 변경은 결정 표와 검증 예제에 함께 반영한다. 최신 문서만 바꾸고 대체 응답을 예전 형식으로 남겨 두지 않는다.

## 공개 API와 담당 경계

아래는 명세 기준이다. `id`는 경로 안의 `order_plan_id`를 뜻한다.

| API | HTTP 경로 | B 공개 함수 제안 | 주요 결과 |
| --- | --- | --- | --- |
| B-01 | `POST /ordering/order-plans` | `create_order_plan` | 주문안·금융 할인 전 견적 |
| B-02 | `GET /ordering/order-plans/{order_plan_id}` | `get_order_plan` | 저장된 주문안·견적 조회 |
| B-03 | `PATCH /ordering/order-plans/{order_plan_id}` | `update_order_plan` | 새 주문 버전·견적, 이전 B 승인 대상 무효화 |
| B-04 | `POST /ordering/order-plans/{order_plan_id}/revalidations` | `revalidate_order_plan` | 최신 가격·품절 재검증, 새 견적 또는 재탐색 안내 |
| B-05 | `POST /ordering/order-plans/{order_plan_id}/benefit-bindings` | `bind_benefit` | C 계산 결과 연결·예산 판정 |
| B-06 | `POST /ordering/order-plans/{order_plan_id}/approval-targets` | `prepare_approval_target` | D 검증용 승인 대상 |
| B-07 | `POST /ordering/order-plans/{order_plan_id}/execution-checks` | `check_execution` | 준비 여부·E 전달 자료 |
| B-08 | `POST /ordering/order-plans/{order_plan_id}/payment-events` | `apply_payment_event` | 원래 견적에 결제 결과 반영 |
| B-09 | `GET /ordering/health` | `health` | 주문 모듈 UP/DOWN |

MVP 1·2의 B API 9개를 사용자 요청으로 구현했다. 기존 견적과 결제 기록은 주문 버전별로 보존하며 실제 팀 코드의 업무 통합 완료와 구분한다.

| 연결 | 명세가 요구하는 경계 |
| --- | --- |
| O → B | 같은 FastAPI 서버 안 함수 호출. O가 전체 순서를 진행 |
| B → 카탈로그 | 공용 restaurant·menu·option 읽기. B가 수집 데이터 수정 안 함 |
| B → C-05 | 같은 서버 안 계산 호출. 할인 규칙은 C가 계산 |
| B → D-04 | 같은 서버 안 승인 상태 조회. B가 승인 발급·소비 처리 안 함 |
| O → E | O가 HTTP로 결제 요청. B는 E에 결제 요청 안 함 |
| E → D-06 | E가 최종 결제 권한 검증·선점. B-07 ready는 이를 대체하지 않음 |
| O → B-08 | E 결과 전달. E가 B를 직접 호출하지 않음 |

B-07의 실행 입력은 현재 E-01 요청과 완전히 같지 않다. E-01에는 `user_id`가 추가로 필요하다. O가 인증에서 얻은 값을 보완해 E에 전달하는 안을 팀과 맞춘다.

## 공통 입출력과 오류

다음은 명세 기준이며 임의 변경하지 않는다.

- JSON 필드는 snake_case, 금액은 `_krw` 이름의 정수다. 숫자로 보이는 ID도 문자열이다.
- ID는 64자 이하, 영문·숫자·밑줄·하이픈·콜론·마침표를 사용한다.
- `request_id`는 O가 발급한다. B가 다른 ID로 재발급하지 않는다.
- HTTP에서는 `X-Request-ID`를 전달한다. B 내부 직접 호출에서도 같은 값을 호출 문맥으로 전달한다.
- 서버 HTTP 인증은 서비스 토큰, health는 무인증이다. 토큰 값은 문서·예제·로그에 넣지 않는다.
- 같은 서버 안 호출은 공통 `AppError(code, message, details)`로 오류를 전달한다. HTTP 어댑터가 공통 오류 응답으로 바꾼다.
- 업무 판단과 통신·입력 오류를 구분한다. B-01 생성 불가는 200과 빈 주문안, 잘못된 수량은 422, 버전 충돌은 409다.
- B-01 정상 생성은 201, B-06 정상 생성은 201, B-02·05·07·08 정상 응답은 200이다.
- 결제 결과 상태는 `PENDING`, `APPROVED`, `FAILED`, `UNKNOWN`이다. `BLOCKED`는 E에 결제가 생성되지 않은 경우로 O가 B-08에 보내지 않는다.

HTTP 오류 예제는 다음과 같다. 직접 호출에서는 이 봉투를 반환하는 대신 같은 code·message·details를 가진 공통 예외를 사용한다.

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "현재 주문 버전을 다시 조회해 주세요.",
    "details": {
      "current_order_version": 2,
      "current_benefit_revision": null,
      "current_approval_target_id": null,
      "next_action": "NONE",
      "retryable": false
    }
  },
  "request_id": "REQ001"
}
```

구현 제안으로, 공개 함수는 요청 DTO와 호출 문맥을 받고 응답 DTO를 반환한다. DTO는 합의한 필드와 자료형을 검증하는 데이터 객체다. ORM 모델·HTTP Response·DB 세션을 응답으로 내보내지 않는다. HTTP 성공 응답에 별도의 `success/data` 봉투를 추가하지 않는다.

## 팀 코드가 들어올 때 교체할 위치

아래는 경로 제안이다. 팀 저장소에 이미 공통 DB·설정·오류 코드가 있으면 그 코드를 사용한다. 별도 `main.py`나 공용 테이블을 중복 생성하지 않는다.

```text
app/modules/ordering/
  api.py                    O가 사용하는 공개 진입점
  router.py                 HTTP 경로와 DTO 변환
  schemas.py                공개 요청 응답과 스냅샷 스키마
  service.py                주문 검증 견적 혜택 연결 상태 처리
  benefits.py               B-05 계산 연결과 원자적 저장
  approval_targets.py       B-06 승인 대상 저장과 O용 D-02 입력 구성
  execution_checks.py       B-07 현재 상태 대조와 준비 결과 기록
  payment_events.py         B-08 원래 실행에 결과 연결과 상태 투영
  health.py                 B-09 B 테이블·매핑 컬럼 조회 가능 여부 검사
  order_updates.py           B-03 검증·견적 추가·이전 B 승인 대상 무효화
  supplements.py            견적별 질문 발급과 옵션 그룹별 답변 적용
  revalidations.py          B-04 최신 조회·재견적 또는 주문 불가 기록
  revalidation_state.py     재검증 실패 상태의 공통 진행 차단
  models.py                 B 소유 ORM 모델
  repository.py             B 소유 데이터 저장과 조회
  pricing.py                메뉴 옵션 배달비 합산
  validation.py             버전 만료 메뉴 옵션 검증
  idempotency.py            중복 요청과 기존 응답 처리
  ports.py                  B가 카탈로그 C D에 요구하는 호출 인터페이스
  bootstrap.py              실제 구현 또는 테스트 대체 구현 주입
  adapters/
    catalog.py              실제 카탈로그 조회와 필드 변환
    benefit.py              실제 C 함수와 입력 출력 오류 변환
    delegation.py           실제 D 함수와 입력 출력 오류 변환
    payment.py              O가 받은 E 결과의 순수 데이터 변환
    latest_catalog.py       B-04 최신 스냅샷 조회와 오류 변환

tests/ordering/
  fakes.py                  카탈로그 C D의 테스트 대체 구현
  fixtures/api_spec_20261115.json  원문 9개 API의 기본 요청·응답 예제
  test_spec_contracts.py     원문 경로·상태·DTO 기본 필드 검사
  test_revalidations.py      재견적→수정→혜택→승인 준비→결제 결과 연결
  test_idempotency.py        중복과 충돌 검증
  test_payment_events.py     미확정 중복 순서 충돌 검증
```

`ports.py`의 함수명과 DTO 이름은 제안이며 팀원의 실제 함수명과 같을 필요는 없다. C·D 서비스는 구현 상세를 감싸는 어댑터를 통해 호출한다. 같은 서버 안 호출을 위해 localhost HTTP를 새로 만들지 않는다.

| 연결 인터페이스 제안 | 받아야 할 정보 | 보장할 결과 |
| --- | --- | --- |
| `CatalogReader` | 식당·메뉴·옵션 ID, 필요한 카탈로그 판본 | 소속 관계·가격·품절·필수 옵션·원본 판본을 가진 B용 조회 DTO |
| `LatestCatalogReader` | restaurant_id, 선택 evidence_ref 힌트 | 호출 시점의 현재 CatalogRestaurant 스냅샷. 과거 판본 선택·문자열 정렬 추정 없음 |
| `BenefitCalculator` | C-05 요청 필드 전체 | C-05 응답 필드 전체와 공통 오류 |
| `ApprovalReader` | approval_id와 호출 문맥 | D-04 응답 전체와 공통 오류. proposal_hash는 결정 항목 D02 참조 |

카탈로그가 `price`를 문자열로 주고 B가 정수 원화를 필요로 한다면 `adapters/catalog.py`에서 검증·변환한다. 단위가 불명확한 값이나 누락 가격을 0으로 바꾸지 않는다. C 또는 D 응답 필드가 빠지면 성공으로 간주하지 않고 계약 불일치로 드러낸다.

개발 중 대체 구현 선택은 테스트 설정에만 둔다. 통합·배포 설정에서 실제 연결이 없으면 시작 또는 호출을 실패시킨다. 실제 C·D 오류가 발생했을 때 대체 할인이나 승인 응답으로 자동 전환하지 않는다.

## DB 소유와 이전 채팅 설계의 적용 방법

명세상 B는 `b_` 테이블만 관리한다. 다른 모듈의 자기 테이블을 직접 읽거나 쓰지 않는다. C·D 구현을 합칠 때 그 테이블에 조인해서 할인·승인 상태를 가져오는 방식으로 바꾸지 않는다. 공용 식당·메뉴·옵션은 예외적으로 직접 읽을 수 있다.

아래 7개를 PostgreSQL 18 개발 DB에 생성했다. 팀 통합 시 스키마명·공통 ORM Base·세션·마이그레이션 이력을 맞춘다. SQLite는 명시적 보조 테스트로만 유지한다. B-05는 기존 테이블을 사용하므로 추가 migration 없이 동작한다.

| B 테이블 제안 | 저장할 핵심 정보 | 통합 때 지킬 조건 |
| --- | --- | --- |
| `b_order_plans` | request·candidate·user·restaurant ID, 현재 주문 버전, 마지막 재검증 상태 JSON | request_id만으로 UNIQUE를 걸지 않음. 과거 검사 응답은 멱등 기록에서 보존 |
| `b_quotes` | 주문 버전, 조건·메뉴 가격 스냅샷, catalog·policy 버전, 금액, 만료 | 과거 견적 보존, 동일 주문의 주문 버전 중복 차단 |
| `b_benefit_bindings` | quote_id, benefit_revision, 수단·규칙 버전, 할인·결제액, 예산 판정 | 견적과 혜택 회차를 함께 식별, 이전 결과 보존 |
| `b_approval_targets` | 주문·견적·혜택 연결과 승인 대상 전체 스냅샷 | B의 대상 ID와 D의 승인 ID 구분 |
| `b_execution_checks` | 승인·실행·견적 연결, ready·reasons·확인 시각 | 반복 확인 허용, 결제 상태 원본으로 사용하지 않음 |
| `b_payment_events` | event·execution·payment·quote ID, 원본 상태, 수신·반영 결과 | 이벤트 중복 차단, 과거 견적 결과가 현재 견적을 덮어쓰지 않음 |
| `b_idempotency_records` | 작업·리소스·키·요청 해시·원래 응답 | 업무 저장과 응답 기록을 같은 트랜잭션에서 처리 |

외부 user·policy·approval·payment ID는 B 안에서 참조 값으로 보관한다. 다른 모듈 테이블에 대한 DB 외래 키나 삭제 전파를 기본으로 추가하지 않는다. B 내부의 주문·견적·혜택·대상 연결은 외래 키와 서비스 검증으로 일관성을 지킨다.

주문 금액·조건은 생성 당시 스냅샷으로 고정한다. B-02 조회에서 최신 가격으로 다시 계산하지 않는다. 새 견적·혜택은 새 버전·회차로 연결한다. 결제수단·혜택 변경 뒤 이전 승인 철회는 O가 새 대상을 D-02로 전달하는 흐름과 맞춘다.

트랜잭션 소유는 팀 확인이 필요하다. 제안은 B 공개 함수 한 번이 B 변경을 원자적으로 저장하고, 어댑터가 전달받은 B 세션을 임의로 commit하지 않는 방식이다. O가 승인 대기·결제 HTTP 호출까지 하나의 긴 DB 트랜잭션으로 묶지 않게 한다. C·D 호출 전후 동시 변경 가능성은 저장 직전 버전 재확인으로 처리한다.

팀 저장소에 합칠 때는 기존 마이그레이션 다음 revision으로 B 변경을 추가한다. 공용 초기 migration·다른 모듈 테이블·팀원의 migration 파일을 덮어쓰지 않는다. 빈 DB 생성과 기존 팀 DB 업그레이드를 각각 확인한다.

## 팀이 결정해야 할 연결 항목

아래 API 연결 항목은 팀 합의가 필요하다. 사용자의 실행 버전 지정은 적용했으며 D12의 함수·공용 코드 연결 방식 등은 남아 있다. 확인할 역할은 제안이며 합의·담당 확정·기한 지정이 아니다. 팀 합의 없이 외부 요청 필드를 임의로 추가하지 않는다. 현재 로컬 선택은 아래 B-05 구현 상태에 구분해 기록한다.

| 번호 | 원문 근거와 문제 | 제안 또는 필요한 결정 | 확인할 역할 | 연동 영향 |
| --- | --- | --- | --- | --- |
| D01 | C-05 `I40`은 user_id 필수, B-01 `I26`·B-05 `I30`에는 없음 | O가 신뢰된 호출 문맥으로 전달하고 B가 소유 정보를 저장하는 안. HTTP 노출 방식도 함께 결정 | O·B·C | B-05 실제 계산 |
| D02 | B-07 `J32`는 proposal_hash 필요, `I32`·D-04 `J51`에는 없음 | 로컬 ApprovalStatus의 선택 필드로 수용. 누락은 ready=false·APPROVAL_HASH_UNAVAILABLE. D가 원본 해시를 제공하는 방식은 팀 확인, B 자체 생성 없음 | O·B·D | 결제 전달 자료 |
| D03 | E-01 `I58`은 user_id 필요, B-07 `J32` 실행 입력에는 없음 | O가 신뢰된 user_id를 보완해 E 요청을 구성하는 안 | O·B·E | E 요청 변환 |
| D04 | 공통 규칙 `B21`은 최신 policy_id·policy_version을 B-01에 전달, `I26`은 policy_version 없음 | B-01 계약의 policy_version 포함 여부 확정 | O·B·D | 조건 변경과 승인 연결 |
| D05 | B-08 `G33`은 응답 유실 UNKNOWN, `I33`은 payment_id가 있는 예시뿐 | 로컬 PENDING·UNKNOWN은 ID null 허용, APPROVED·FAILED는 ID 필수. ready 실행과 연결하며 팀 E 계약 확인 필요 | O·B·E | 결제 응답 유실 복구 |
| D06 | 견적 expires_at 예시만 있고 B 견적 TTL 규칙 없음 | 설정값·기준 시각·만료 경계 확정. D의 승인 10분 규칙과 B 견적 TTL을 구분 | O·B·D | 만료 검증 |
| D07 | B-01 키 `H26`에는 candidate만, 공통 `B10`은 결과를 바꾸는 값 모두 포함 | 같은 요청·후보의 조건·catalog 버전이 바뀔 때 새 키 구성 결정 | O·B | 재탐색과 조건 변경 |
| D08 | B-05 키 `H30`은 주문 버전·수단만 포함 | 카드 A→B→A 재선택 때 이전 응답 재생과 현재 선택이 어긋나지 않도록 선택 회차·키 구성 결정. 규칙 버전 변경도 검토 | O·B·C | 결제수단 재선택 |
| D09 | B-02 `G27`은 혜택·승인·결제 상태 조회, `J27` 예시는 일부만 포함 | include_payment=true에서만 payment.executions를 추가하는 로컬 확장. 기본 응답 유지, 팀 UI·O와 최종 형태 확인 | O·B | 진행 상태 조회 |
| D10 | 공통 규칙 `B11`은 재결제 금지이나 결제 이벤트 순서 정책은 없음 | 로컬 확정 상태 유지·충돌 기록·CHECK_PAYMENT_STATUS와 재조회 필요 표시 구현. 실제 E 상태 버전·충돌 교정 절차는 팀 확인 | O·B·E | 이벤트 순서와 충돌 |
| D11 | 메뉴·옵션·카탈로그의 실제 물리 스키마가 미정 | 가격 단위, 옵션 그룹·필수 개수, 품절·배달비·판본 조회 방법 합의 | A·B·수집 데이터 담당 미정 | B-01 가격과 유효성 검증 |
| D12 | 실행 버전은 사용자 지정 완료, 공용 세션·함수 비동기 여부·오류 구현은 미정 | Python 3.14·PostgreSQL 18 기준으로 팀 Session·AppError·DTO·sync/async 연결 방식 확인 | O·B·C·D | import·실행·트랜잭션 충돌 |
| D13 | B-03 I28은 ANSWER_SUPPLEMENT 예시뿐. 질문 발급·조회·일반 메뉴 수정·최초 미완성 주문 규칙 없음 | 로컬 REPLACE_ITEMS 전체 목록 수정, B-02 include_supplements 선택 조회, 견적별 질문 스냅샷 구현. B-01 생성 실패에는 주문안 없음 유지. 업무상 수정 불가는 200+RESEARCH_REQUIRED+issues로 기존 주문 보존, 잘못된 입력은 422. 세부 DTO·질문·오류·draft 필요 여부는 팀 확인 | O·B·UI | 주문 변경과 필수 옵션 보완 |
| D14 | B-04 I29는 reason/evidence_ref 예시, G29는 실패 시 재탐색만 기술. 최신 조회·실패 상태/응답·근거 해석은 미정 | 로컬 LatestCatalogReader, reason 3종, 선택 evidence_ref 힌트, 실패 200+INVALID+issues/research_request 구현. 실패는 버전/금액 유지·B 대상 무효화·준비 차단, 새 재검증으로만 복구. 세부 DTO·A-07 reason 매핑·최근 검사 상태·동시성은 팀 확인 | O·A·B·카탈로그 담당 | 최신 데이터 기준·재탐색·진행 차단 |

## 같은 예제로 확인할 계약 검사

예제의 ID와 금액은 합성 테스트 데이터다. 시간은 고정 시계로 제어한다. 원본 엑셀의 11월 13일 expires_at은 과거 예시이므로 실행 시 그대로 사용하지 않는다.

공통 성공 예제는 메뉴 17,000원, 옵션 1,000원, 수량 1, 배달비 2,000원, 예산 20,000원이다. 시각을 `2026-11-16T10:00:00+09:00`으로 고정하고 견적 만료는 예제용 `10:10:00+09:00`으로 둔다. C는 즉시할인 2,000원과 결제액 18,000원을 반환한다. 이 만료 간격은 실제 TTL 합의가 아니다.

| 검사 | 입력 또는 조건 | 기대 관측 | 구현 연결 |
| --- | --- | --- | --- |
| T01 | 정상 메뉴·옵션·수량 | B-01 견적 20,000원, 201, 금융 할인 후 예산은 PENDING_BENEFIT | 카탈로그·B-01 |
| T02 | 수량 0 | 422 VALIDATION_ERROR, 새 주문안 생성 안 함 | B-01 |
| T03 | 품절로 생성 불가 | 200, order_plans 빈 배열, RESEARCH_REQUIRED | 카탈로그·B-01 |
| T04 | 견적 저장 뒤 원본 메뉴 가격 변경 | B-02가 기존 견적 20,000원 유지 | B-02 |
| T05 | C-05 할인 2,000원·결제액 18,000원 | B-05에 같은 값·규칙 버전 저장, 예산 PASS | C·B-05 |
| T06 | 오래된 주문 버전 또는 다른 견적 | 409, 현재 혜택·승인 대상 변경 안 함 | B-05·06·07 |
| T07 | 현재 견적·혜택으로 대상 생성 | D-02가 필요한 메뉴 상세·금액·버전·만료가 모두 존재 | B-06·D |
| T08 | 승인 APPROVED, 관련 ID 모두 일치 | B-07 ready=true, 저장된 대상의 금액·수단 반환 | B-07·D |
| T09 | 승인 철회·만료 또는 관련 ID 불일치 | ready=false와 사유, 결제 진행 자료 사용 불가 | B-07·D |
| T10 | D 조회 불가 | 503 APPROVAL_UNAVAILABLE, ready=true로 대체 안 함 | B-07·D |
| T11 | 같은 멱등 키와 같은 내용, 동시 요청 포함 | 생성·연결은 한 번, 원래 응답 재생 | B-01·05·06 |
| T12 | 같은 멱등 키와 다른 내용 | 409, 기존 결과 유지 | B-01·05·06 |
| T13 | 같은 event_id·같은 내용 두 번 | 결제 결과 한 번만 반영, 기존 응답 반환 | B-08 |
| T14 | 같은 event_id·다른 내용 | 409 EVENT_CONFLICT, 기존 결과 유지 | B-08 |
| T15 | 과거 견적의 결제 이벤트 | 원래 견적에 연결, applied_to_current_quote=false | B-08 |
| T16 | UNKNOWN 이후 E 조회로 APPROVED 확인 | 합의한 순서 규칙으로 상태 확정, 새 결제 요청 안 함 | O·B-08·E, D05·D10 결정 후 |
| T17 | 카드 A→B→A 재선택 | 현재 혜택과 응답이 마지막 선택 A로 일치 | O·B-05, D08 결정 후 |
| T18 | 승인 응답의 request_id와 호출 문맥 불일치 | 준비 완료로 처리하지 않음 | B-07 |

현재 C-05 대체 구현은 `tests/ordering/fakes.py`, 명시적 개발 응답은 `examples/benefit_calculations.json`이다. 팀 코드 수령 뒤 같은 DTO 예제를 실제 어댑터에 적용한다. 실제 C·D 내부 로직과 E 결제 완료는 별도의 통합 검사로 확인한다. 위 표는 검사 계획이며 실제 완료 범위는 아래에 기록한다.

## 팀 코드 수령 후 합치는 순서

1. 팀원이 준 파일의 저장소·브랜치·커밋 또는 압축파일 판본을 기록한다. 기존 B 작업과 분리한 작업 브랜치에서 비교한다.
2. 이 문서의 기준 원본과 새 팀 명세를 비교하고 D01~D14 중 결정된 내용을 먼저 갱신한다. 문서 날짜만 보고 새 코드가 최종 계약이라고 가정하지 않는다.
3. 공용 앱 진입점·의존성·DB·오류·DTO·sync/async 방식을 맞춘다. 팀 공용 main·설정·DB 코드 전체를 B 버전으로 교체하지 않는다.
4. O의 입력을 B 공개 DTO에 연결하고, 카탈로그·C·D 어댑터만 실제 코드로 연결한다. `service.py`에 팀원의 DB 모델 import가 퍼지지 않는지 확인한다.
5. B 마이그레이션을 팀 DB 이력 뒤에 추가한다. 빈 DB와 기존 DB에서 확인한다.
6. 계약 검사 T01~T18 중 결정된 범위를 대체 구현·실제 어댑터에 각각 실행하고 차이를 기록한다.
7. O→A 후보 선택→B-01→C-01→B-05→B-06→D 승인→B-07→E 결제→B-08 흐름을 실행한다. 결제 거절·응답 유실도 확인한다. E 최종 검증은 E→D-06이다.
8. 저장소·커밋·환경·실행 명령·결과·남은 차이를 아래 인계 기록에 남긴다. 테스트 대체 구현이 실제 배포 설정에 연결되지 않았는지 확인한다.

단순 파일 병합 성공, DTO 검사 성공, 실제 모의 결제 성공은 서로 다른 완료 상태다. 수행한 범위만 완료로 기록한다.

## 인계 기록과 변경 관리

현재 수정 기준은 `backend/docs/integration/B_MVP1_INTEGRATION_GUIDE.md`다. 이전 output 문서는 이 위치로 안내하는 기록으로 유지한다. 팀 개발 저장소가 정해지면 코드와 함께 옮긴다. 여러 복사본을 동시에 최신본으로 관리하지 않는다. 예제 JSON과 자동 계약 검사도 같은 저장소에서 관리한다.

| 기록 항목 | 현재 상태 또는 작성 방법 |
| --- | --- |
| 문서 판본 | 초안 10, 2026년 11월 16일. B 전체 명세 검토·호환성 수정·원문 계약 검사 반영 |
| 기준 명세 | 프로젝트 output의 보관 엑셀, 위 SHA-256 |
| 구현 코드·커밋 | `backend/app/modules/ordering` 및 `backend/tests/ordering`. 커밋·원격 변경 없음 |
| 공용 런타임·DB·의존성 | Python 3.14.3, PostgreSQL 18.7. 의존성 `backend/uv.lock`, 설정 `.env.example`, 로컬 DB `compose.yaml`. E는 Java 25·Spring Boot 4.1, UI는 Node 26 통합 기준 |
| DB migration | b01→b06→b07→b03→b04→`20261116_review_quote_validation.py`. 견적별 검사 결과를 보존하고 기존 B04 실패 응답·최근 상태에서 근거를 이전한다. 테이블 7개 및 기존 금액·결제 보존. 팀 이력으로 재배치 필요 |
| 실제 실행 명령 | backend에서 `uv run alembic upgrade head`, `uv run alembic check`, `uv run pytest -q`, `uv run ruff check app migrations tests scripts`, `uv run python scripts/smoke_http.py` |
| 대체 구현 검사 | PostgreSQL 18 및 보조 SQLite에서 각각 자동 검사 320개 통과. B-08 중복·순서·확정 충돌·과거 견적·동시 수신·롤백·E 변환, B-03 주문 수정·질문·동시성·기존 기록 보존, B-04 최신 조회·품절 차단·복구·늦은 관측 및 C/D 응답 경쟁·migration, B-09 UP/DOWN·누락 테이블/컬럼·복구·데이터 불변 포함. 연결/pool 오류는 모의 예외 검사. 증거 `backend/data/pytest-postgresql18.xml` 및 `pytest-sqlite.xml` |
| 실제 HTTP 검사 | Python 3.14.3·PostgreSQL 18.7에서 B-01→B-05→B-06→B-07→B-08 합성 결과 연결. UNKNOWN→APPROVED, 이벤트 중복 재생, B-02 결제 조회, B-03 버전 1→2→3·보완 답변·이전 결제 보존, B-04 버전 4·INVALID 차단·버전 5 복구, 실행 전후 B-09 무인증 UP. 실제 E 전송 없음. 증거 `backend/data/smoke-http-result.json` |
| 실제 팀 어댑터·결제 통합 | 미실행. 실제 C 코드·E·프런트엔드 코드의 런타임 및 업무 연동 검증은 남음 |
| 열린 결정 | D01~D14 API·공용 코드·질문·재검증 계약은 팀 확인 필요. 사용자 지정 실행 버전은 적용 완료 |
| 다음 시작점 | 실제 O·카탈로그·A·C·D·E를 각 경계에 연결. 최신 판본·재탐색·해시·UNKNOWN 식별·E03 조회·상태 충돌 계약을 확인하고 E→D-06·원장 검증 |

현재 로컬 선택은 다음과 같다. 외부 B-01 JSON을 바꾸지 않고 내부 `CallContext`에서 선택적 user_id·policy_version을 받는다. B-01은 미제공 시 DB에 null로 보관한다. B-05는 저장된 사용자와 현재 사용자 문맥이 있어야 하며 누락은 409 USER_CONTEXT_REQUIRED, 다른 사용자는 403이다. 기존 null 소유자를 임의로 채우지 않는다. HTTP 앱의 기본 문맥에는 사용자 정보가 없으므로 팀 인증 문맥 주입이 필요하다. 개발 fixture에서만 명시적 ORDERING_DEMO_USER_ID를 쓸 수 있다. 이러한 선택은 D01·D04에 대한 팀 API 합의를 대체하지 않는다.

견적 유효시간은 임시 설정 600초, 저장은 UTC·응답은 +09:00이다. POST_FINANCIAL만 지원하며 혜택 전 예산은 PENDING_BENEFIT이다. B-02의 기본 응답은 유지하고 include_payment=true에서만 결제 요약을 추가한다. 이 선택적 확장도 D09의 최종 팀 합의와 구분한다. B-01의 멱등 키 재구성 D07은 계속 미결이다.

계획 T01~T18의 B 담당 범위를 합성 C·D·E 결과와 로컬 충돌 규칙으로 검증했다. T07의 실제 D 독립 검증, T08~T10의 실제 D 연동, T16의 실제 E 조회는 미실행이다. 실제 승인 발급·D-06·E 원장·FEP는 검증하지 않았다. 자동 검사 320개와 계획 표 18개는 서로 다른 단위다.

## B05 구현 상태와 C 연결 방법

`ports.BenefitCalculator.calculate(BenefitCalculationRequest)`가 C-05 경계다. 팀 동기 함수는 `adapters.benefit.CallableBenefitCalculator`에 주입한다. 계산 DTO를 팀 입력으로 명시적으로 매핑하고 C 응답을 합의한 DTO 또는 dict로 반환한다. 구체적인 조립 예시는 backend README에 있다. 별도 HTTP·C-04 호출·다른 모듈 DB 읽기를 추가하지 않는다.

고정 응답 어댑터는 사용자·식당·메뉴 합계·배달비·수단·규칙 버전이 일치하는 예제에만 응답한다. request_id는 추적 키라 예제 매칭에서 제외한다. 할인을 계산하는 가짜 금융 엔진은 구현하지 않았다. 실제 C 실패를 임시 성공 응답으로 대체하지 않는다.

C 호출 전에 저장된 견적·사용자를 확인하고, 호출 후 짧은 트랜잭션에서 현재 주문 버전·견적 만료·혜택 회차를 다시 확인한다. 지정 수단·규칙 버전 및 결제액 산술 일관성을 검증하고 새 혜택 회차·계산 원본·예산 PASS 또는 FAIL을 저장한다. 기존 PREPARED B 승인 대상은 SUPERSEDED로 바꾸고 ID를 반환한다. D 승인 철회는 O·D 범위다.

## B06 구현 상태와 D 연결 방법

`OrderingAPI.prepare_approval_target`가 현재 주문·견적·혜택 회차를 검증하고 승인 대상을 저장한다. 외부 입력은 원본 B-06의 base_order_version·quote_id·expected_benefit_revision 그대로다. 성공은 201이며 내부 line_id를 제외한 메뉴별 상세·금액·판본·만료·확인 불가 조건을 포함한다. 동일 견적·혜택 회차는 키가 달라도 같은 대상을 재사용하는 로컬 규칙을 적용했다. 만료·무효화된 대상은 재생하지 않는다.

`OrderingAPI.get_delegation_validation_request(order_plan_id, approval_target_id, context)`는 O가 D-02에 넘길 DTO만 반환한다. 저장한 사용자·정책 버전과 B-06 quote를 사용하고, 실제 D 호출·approval_id 발급·proposal_hash 생성은 수행하지 않는다. 저장된 policy_version이 없으면 409 POLICY_CONTEXT_REQUIRED이며 현재 문맥의 값을 임의로 대입하지 않는다. 구체적 호출 예시는 backend README에 있다.

`20261116_b06` migration이 Quote.restaurant_snapshot을 추가한다. B-01에서 식당 ID·local_currency_merchant·local_currency_basis를 복사하며 기존 견적은 가격 변경 없이 빈 근거로 유지한다. 지역화폐 필수 조건에서 동일 식당의 true와 근거를 확인할 수 없으면 local_currency_merchant를 unverified_conditions에 넣는다. 명시적인 MVP 1 추정 근거는 원본 기준대로 사용한다. 메뉴 설명 null은 D 문자열 필드에 빈 문자열로 옮기고, 제외 재료 조건에 필요한 설명이 없으면 확인 불가로 표시한다. 이 누락 처리의 실제 D 호환성은 담당 코드로 확인해야 한다.

B-05와 B-06은 같은 주문 행 잠금으로 순서를 맞춘다. B-06 생성이 먼저면 후속 B-05가 해당 대상을 SUPERSEDED로 바꾸며, B-05가 먼저면 오래된 expected_benefit_revision의 B-06은 409다. 생성·스냅샷·멱등 기록은 한 트랜잭션이며 기존 원본 견적과 이전 대상 스냅샷은 덮어쓰지 않는다.

## B07 구현 상태와 D 승인 조회 경계

`OrderingAPI.check_execution`은 원본 B-07 입력을 받아 현재 주문·견적·혜택·승인 대상을 대조한다. `ports.ApprovalReader.read_approval(approval_id, context)`로 D-04를 매번 새로 읽고, 조회 후 주문 잠금을 얻어 B 상태와 만료를 다시 확인한다. 정상은 200·ready=true·execution_input, 업무 준비 실패는 200·ready=false·reasons·execution_input=null이다. 준비 조건 확인과 기록만 수행하며 E 전송과 D 승인 소비는 수행하지 않는다.

팀 D 동기 함수는 `adapters.delegation.CallableApprovalReader`로 감싼다. 승인·요청·대상·주문 버전·견적·실행 ID를 모두 대조하고 D APPROVED와 만료·확정 시각·원본 해시를 확인한다. D-04 원본에 없는 proposal_hash는 선택 필드로 받아 누락 시 준비 실패로 둔다. 필드 전달 방식은 D02 미결 항목이며 임시 해시를 실환경에서 생성하지 않는다.

개발 파일 어댑터는 명시적 development 설정에서만 선택하고 매번 파일을 다시 읽는다. 기본 파일은 빈 목록으로 자동 승인이 없다. 실제 D timeout·연결 실패는 503 APPROVAL_UNAVAILABLE, 응답 계약 오류는 503 APPROVAL_DATA_INVALID이며 임시 성공 응답으로 대체하지 않는다. 없는 승인 조회의 404는 그대로 전달한다.

반복 확인도 D 재조회와 새 execution_check_id를 만든다. B-07에는 성공 응답을 재생하는 멱등 캐시를 적용하지 않는다. 승인 조회 중 B-05가 혜택을 변경하거나 견적이 만료되면 최종 B 상태 대조에서 ready=false로 기록한다. D는 조회 이후에도 철회될 수 있으므로 실제 결제 직전 E→D-06을 반드시 거쳐야 한다.

새 request_snapshot·approval_snapshot 필드는 준비 확인 당시의 요청과 관측한 D 응답을 보존한다. 최종 결제 허가 기록이 아니다. 로컬 0원 결제 차단, 세부 reasons·업무 실패 200과 해시 필드 계약은 실제 O·D·E 코드 수령 후 함께 대조한다. 자세한 설정과 조립 예시는 backend README에 있다.

## B08 구현 상태와 E 결과 경계

`OrderingAPI.apply_payment_event`는 O가 전달한 이벤트를 원래 견적·승인 대상·실행에 연결한다. 같은 실행의 B-07 ready 기록이 있어야 하며, 뒤늦은 결과를 기록할 때 현재 D 승인이나 견적 만료를 다시 판단하지 않는다. 이미 E가 처리한 결제 사실을 현재 메뉴 선택으로 덮어쓰지 않는다. 새 결제·자동 재시도·E 조회는 수행하지 않는다.

O에서 `adapters.payment.payment_reference`로 B-07 연결 정보를 보관하고, E의 HTTP 200 업무 JSON을 `payment_event_from_e`로 변환한다. HTTP 클라이언트는 O 담당이다. BLOCKED는 None으로 변환해 B-08에 보내지 않는다. 응답 유실을 관측한 경우만 `payment_event_for_timeout`으로 UNKNOWN을 구성한다. E의 ID·금액·수단 불일치는 변환 오류로 드러내며 성공으로 대체하지 않는다.

같은 event_id와 같은 본문은 최초 응답을 재생하고, 다른 내용은 409 EVENT_CONFLICT다. 키는 event_id와 같아야 한다. 관측 시각은 해시에서 UTC로 정규화한다. 새 관측은 새 ID, 재전송은 기존 본문 그대로 사용한다. 수신 원본 status와 유효 채택 상태를 구분해 저장한다. B-08은 기존 테이블을 사용하므로 새 migration이 없다.

로컬 정책으로 확정 상태를 미확정 상태로 낮추지 않고, 상반되는 확정 상태나 결제 ID 충돌은 CONFLICT와 CHECK_PAYMENT_STATUS로 반환한다. 기존 채택 상태는 유지한다. 같은 시각의 상충 미확정 결과도 임의 선택하지 않는다. UNKNOWN 이후 확정 결과를 채택할 수 있고, 확정 결과는 더 오래된 관측 시각이어도 미확정보다 우선한다. 반대 확정 상태로의 자동 교정은 구현하지 않았으며 실제 E 원장 대조 절차가 필요하다.

B-02의 선택적 payment.executions는 선택한 견적의 실행별 최신 채택 상태와 재조회 필요 여부를 보여 준다. 과거 혜택·견적 결과는 현재 금액을 바꾸지 않는다. applied_to_current_quote는 현재 주문 버전·혜택 회차·대상이 일치할 때의 표시다. 이벤트 재생은 당시 응답이므로 현재 표시는 B-02를 조회한다. 이 로컬 D05·D09·D10 규칙을 실제 E 코드와 대조해야 한다.

예산 FAIL도 200으로 저장·반환하고 원본 next_action BUILD_APPROVAL_TARGET을 유지한다. D의 독립 예산 검증을 우회하는 승인이 아니다. eligible=false는 할인 0원이어야 한다. C 수단·규칙 버전 불일치는 409 STALE_CALCULATION, 잘못된 금액·응답 형식은 503 BENEFIT_DATA_INVALID다.

로컬 D08 규칙으로 과거 A 선택 뒤 B가 현재 상태이면 과거 A 키 재사용은 409 STALE_IDEMPOTENCY_RESULT로 알린다. 새로운 A 선택에는 O가 새 선택 회차 키를 보내야 한다. 같은 키의 현재 성공은 재생하고, 서로 다른 선택의 동시 계산 중 오래된 회차는 409 BENEFIT_VERSION_CONFLICT로 차단한다. 만료된 견적의 B-05는 재생도 409다. 이 충돌 처리와 최종 키 형식은 팀 합의 전 로컬 선택이다.

필드명·자료형·필수 여부·상태 의미·멱등 키가 바뀌면 호출자와 응답 예제, 어댑터, DB migration, 계약 검사에 미치는 영향을 함께 기록한다. 합의된 판본 이후 계약이 바뀌는 경우에는 적용 시점과 이전 호출자의 전환 방법을 정한다. 원본 변경 이력에 합의 근거가 없으면 구현자의 임시 선택을 팀 결정으로 기록하지 않는다.

## 작성과 확인 기록

2026년 11월 16일 B-04 구현 후 B-01~B-09 명세·연결 검토와 수정을 반영해 초안 10으로 갱신했다. B API 9개가 로컬 구현됐으며 검증은 Python 3.14·PostgreSQL 18과 합성 상대 결과 기준이다. 실제 팀 코드의 업무 연동은 검증 전이다.

## B09 구현 상태와 운영 경계

명세 A34:L34에 맞춰 무인증 `GET /ordering/health`를 제공한다. X-Request-ID와 사용자 문맥도 필요 없다. HTTP 200은 `{"service":"ordering","status":"UP"}`, HTTP 503은 `{"service":"ordering","status":"DOWN"}`이며 공통 오류 봉투 대신 health 전용 형식을 사용한다. 직접 호출은 `OrderingAPI.health()`이고 기존 `create_router(...)`에 함께 포함된다. 업무 경로의 서비스 토큰 인증은 그대로 적용된다.

검사 범위는 로컬 구현 선택이다. 매번 주입된 세션 팩토리로 B 소유 테이블 7개의 모든 매핑 컬럼을 LIMIT 0 SELECT한다. DB 연결·pool timeout·테이블·필수 컬럼 오류는 DOWN이며 SQL·접속 정보는 반환하지 않는다. 사용자 행·공유 Base의 다른 모듈 테이블은 읽지 않고 데이터·승인·결제를 변경하지 않는다. 결과 캐시는 없고 응답은 no-store다. DB 대기 시간 제한은 팀 공용 engine 설정을 따른다. 제약조건·인덱스를 포함한 전체 migration 검사는 별도다. 이 범위를 변경하려면 팀 운영 기준과 먼저 맞춘다.

B-09는 C·D·E 가용성 검사가 아니며 UP이 결제 가능을 뜻하지 않는다. 따라서 E 임시 health 응답을 추가하지 않았다. 대신 기존 합성 E UNKNOWN→APPROVED 결과를 B-08에 연결한 뒤 B-09 호출이 데이터를 바꾸지 않는지 검증했다. 실제 E 결과는 기존 adapters/payment.py에 연결하며 실제 E·D-06·원장은 아직 미검증이다. 새 migration·의존성은 없다.

## B03 구현 상태와 팀 연결 계약

명세 A28:L28의 PATCH·base_order_version·ANSWER_SUPPLEMENT·answers·응답 view/changes/invalidated_approval_target_ids/next_action을 제공한다. 서비스 토큰·X-Request-ID·Idempotency-Key가 필요하다. 직접 호출은 `OrderingAPI.update_order_plan(order_plan_id, UpdateOrderPlanRequest, CallContext, idempotency_key)`다. 기본 B-01·B-02 응답은 유지하며 새 의존성은 없다.

명세에 없는 아래 세부 형식은 D13의 로컬 선택이다. 팀 합의로 확정된 것이 아니다.

- 일반 수정은 `mode=REPLACE_ITEMS`와 최종 전체 items를 받는다. 메뉴 추가·삭제·수량·옵션을 검증하며 명세의 ANSWER_SUPPLEMENT와 요청 필드를 혼용하지 않는다. 식당·조건·정책·catalog_version은 기존 견적에서 보존한다.
- B-01·B-03 성공 시 각 옵션 그룹의 질문을 견적에 저장한다. B-02 `include_supplements=true`에서만 supplements 배열을 추가한다. 조회는 저장 자료만 읽는다. 질문은 question_id·line_id·menu_id·option_group_id·min_selections·max_selections·choices(value,price_krw,available)·selected_values를 포함한다.
- question_id는 견적·항목·그룹에 묶인 B 발급 ID다. O·UI는 문자열을 그대로 전달한다. 답변은 지정 그룹만 바꾸며 나머지 항목·그룹은 보존한다. 지난 견적·다른 주문의 질문은 409 SUPPLEMENT_NOT_FOUND다.
- B-01 생성 불가 응답은 여전히 주문안 없음이다. 따라서 최초 미완성 주문의 draft를 B-03으로 완성하는 흐름은 제공하지 않는다. 기존 유효 주문의 옵션 선택 변경을 지원한다. 업무상 변경 불가는 200+RESEARCH_REQUIRED+issues로 이전 견적과 대상을 보존하고, 잘못된 자료형·중복 옵션은 422다. O는 올바른 전체 items로 다시 요청한다. draft 상태가 필요하면 별도 계약을 정한다.
- changes의 before/after는 JSON 정수·배열·객체이며 옵션 목록은 배열이다. line_id는 견적 내 항목 순번으로 버전과 함께 식별한다. 동일 항목이라도 새 키로 수락한 수정은 새 버전·견적을 만든다.

기존 CatalogReader로 현재 견적의 판본을 읽고 검증한다. 최신 판본이나 만료 재견적은 B-04 범위다. 만료는 QUOTE_EXPIRED, 오래된 base 버전은 VERSION_CONFLICT다. 팀 카탈로그의 함수·컬럼 매핑은 기존 어댑터만 교체한다. 정책 버전 불일치는 거부하며 PATCH로 조건·정책을 덮어쓰지 않는다.

새 견적은 혜택 미연결 PENDING_BENEFIT이다. 이전 B 대상은 SUPERSEDED로 저장하고 무효화 ID를 반환한다. O는 C-01→B-05→B-06→D-02 재검증·사용자 승인→B-07로 진행한다. B는 D 상태를 변경하지 않으며 D-02 재호출에 의한 이전 D 승인 철회는 O·D 책임이다. B-07은 이전 대상의 준비 완료를 차단한다. B-08의 늦은 결과는 원래 견적에 보존하며 새 견적으로 옮기지 않는다. 주문 변경은 환불·새 결제 요청이 아니다.

동일 키·같은 내용은 최초 응답을 재생하고 다른 내용은 IDEMPOTENCY_CONFLICT다. 과거 성공 응답을 재생할 수 있으므로 최신 상태는 B-02에서 확인한다. 카탈로그 조회는 잠금 밖에서 수행하고 주문 행 잠금 뒤 버전·만료를 재확인한다. 동시 다른 수정은 한 건만 채택한다. 저장 오류 시 견적·버전·대상 무효화·멱등 키를 함께 롤백한다.

b07 뒤의 `20261116_b03` migration은 b_quotes.supplement_questions_snapshot만 추가한다. 예전 견적은 질문을 추정하지 않고 []로 보관하며 REPLACE_ITEMS 성공 시 새 질문을 만든다. 테이블 수와 기존 기록을 보존한다. 독립 DB에는 적용 완료했으며 팀 DB에는 공용 이력 다음으로 옮겨 적용한다. 실행 예제·필드 형식은 backend README와 examples/update_order_items.json·answer_supplement.json에 있다.

B-03 검사에는 메뉴·수량·옵션·그룹/항목 보존·질문 판본·동시 수정·중복·롤백·구 데이터 migration을 포함한다. 이전 승인 차단, 늦은 합성 E 결과 보존, 새 B-05·B-06·B-07 연결을 검사했다. HTTP smoke는 기존 합성 E 승인 결과 뒤 B-03 버전 1→2→3과 과거 결제 보존을 확인한다. 실제 D 승인 철회·실제 E는 미검증이다.

## B04 구현 상태와 최신 조회 경계

명세 A29:L29의 `POST /ordering/order-plans/{order_plan_id}/revalidations`를 구현했다. 서비스 토큰·X-Request-ID·Idempotency-Key가 필요하다. 공개 함수는 `OrderingAPI.revalidate_order_plan(order_plan_id, RevalidateOrderPlanRequest, CallContext, idempotency_key)`다. 성공 응답은 원문의 view·changes·invalidated_approval_target_ids·CALCULATE_BENEFITS다. 최신 메뉴·옵션·배달비로 새 버전·견적·만료·질문 스냅샷을 저장하며 조건·정책·수량·선택은 유지한다. 가격이 같아도 만료 갱신을 위해 새 견적을 만든다. 혜택은 PENDING_BENEFIT이며 O가 C-01→B-05→B-06→D-02 재승인→B-07을 진행한다.

최신 자료는 기존 CatalogReader와 분리한 `LatestCatalogReader.read_latest_restaurant(restaurant_id, evidence_ref)`로 받는다. `build_ordering(..., latest_catalog=...)`에 주입하고 CallableLatestCatalogReader로 팀 함수·DTO 변환을 감싼다. B-01·B-03은 기존 지정 판본 조회를 유지하며 B-03이 재검증 후 견적을 수정하려면 이 조회도 새 판본을 지원해야 한다. 예제 코드와 설정은 backend README의 B-04 절을 따른다.

D14의 로컬 선택은 다음과 같다.

- reason은 PRICE_CHANGED·SOLD_OUT·QUOTE_EXPIRED, evidence_ref는 선택 힌트다. 힌트가 과거 판본을 가리켜도 어댑터는 호출 시점의 현재 권위 있는 스냅샷을 반환한다. 버전명 정렬·이전 판본 fallback·evidence_ref 자체를 최신으로 간주하는 처리는 없다. 실제 최신 판본 선택·관측 근거 검증은 카탈로그 담당과 합의한다.
- 최신 조회를 설정하지 않으면 B-04만 CATALOG_UNAVAILABLE 503이다. 외부 오류 때 fixture로 전환하지 않는다. 현재 상태를 알 수 없는 오류는 폐점·품절로 추정하지 않는다. 실제 폐점은 최신 판본과 available=false로 표현한다. 성공한 조회도 그 관측 시점의 스냅샷이며 영구 최신 보장은 아니다.
- 주문 불가는 HTTP 200과 INVALID·RESEARCH_REQUIRED·issues·research_request를 반환한다. research_request는 A-07 입력 형태의 reason·restaurant_id·menu_ids·current_price_krw다. 영업 불가는 RESTAURANT_CLOSED, 나머지는 SOLD_OUT으로 요약하고 선택 메뉴 전체를 알린다. 상세 원인은 issues, 금액은 null이다. 이 매핑·배제 범위는 팀 확인 필요이며 B가 A-07을 호출하지 않는다.
- 실패 시 기존 주문 버전·견적·가격·혜택·결제를 보존하고 새 가짜 견적을 만들지 않는다. 마지막 검사 상태를 b_order_plans.revalidation_snapshot에 저장하고 B-02의 해당 현재 견적 view를 INVALID로 표시한다. 당시 혜택 금액 판정 PASS는 남아 있어도 주문 진행 허용을 뜻하지 않는다. 이전 B 대상은 SUPERSEDED다. B-03·B-05·B-06을 ORDER_REVALIDATION_REQUIRED로, B-07을 ready=false로 차단한다. O는 재탐색하거나 새 B-04로 정상 스냅샷을 확인한다. 복구는 새 견적을 만들며 이전 대상을 되살리지 않는다.
- 같은 키는 당시 성공·실패 응답을 재생한다. 새 관측에는 새 키가 필요하다. 다른 내용은 IDEMPOTENCY_CONFLICT다. 현재 버전·견적 외 요청은 VERSION_CONFLICT·QUOTE_ID_MISMATCH다. 최신 조회는 DB 잠금 밖에서 수행한다. 저장 직전 주문 버전과 마지막 재검증 ID를 재확인해, 실패로 버전이 유지된 경우에도 오래된 결과가 최근 검사 상태를 덮지 않도록 REVALIDATION_CONFLICT로 거부한다.

b03 뒤의 `20261116_b04_revalidation_state.py`가 주문의 마지막 재검증 JSON을 추가했다. 독립 PostgreSQL에는 적용했고 기존 스냅샷·금액·결제를 보존하는 migration 검사도 수행했다. 테이블은 여전히 7개다. 마지막 검사 JSON은 전체 감사 이력이 아니며 과거 검사 응답은 멱등 기록에 남는다. B-02는 현재 저장 상태만 읽으며 재검증을 수행하지 않는다. B-08은 실패·만료·견적 변경 뒤 도착한 기존 실행의 결과를 원래 견적에 기록한다. B-04는 D 승인 철회·발급·E 결제·환불을 직접 수행하지 않는다.

최신 fixture는 examples/latest_catalog.json의 식당별 현재 스냅샷 한 건이며 매 호출 다시 읽는다. ORDERING_LATEST_CATALOG_MODE=fixture를 별도로 선택하고 production에서는 금지한다. 실제 서비스 오류를 fixture 성공으로 바꾸지 않는다. B-04 검증에는 최신 가격·옵션·배달비·만료·품절·필수 선택 변경·동시 수정·오래된 관측·진행 중 C/D 응답 차단·롤백·복구·늦은 E 결과 보존·새 혜택/승인 준비 연결을 포함한다. 실제 카탈로그·A·D·E 코드 연동은 아직 검증 전이다.
