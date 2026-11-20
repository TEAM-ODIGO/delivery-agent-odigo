# 프로젝트 실행 버전과 검증 범위

2026년 11월 16일 사용자가 지정한 버전을 기존 구현·현재 작업·이후 작업의 기준으로 적용한다. API 세부 계약에 대한 팀 미결 사항과 이 버전 선택을 구분한다.

| 구성 | 적용 기준 | 현재 상태 |
| --- | --- | --- |
| B와 O·A·C·D의 Python 서버 | Python 3.14.x | B를 Python 3.14.3에서 실행·검증 |
| 공용 관계형 DB | PostgreSQL 18.x | 로컬 Docker PostgreSQL 18.7에서 B migration·테스트·HTTP 검증 |
| E 런타임 | Java 25 | 통합 기준 기록. 현재 B 작업에 E 구현은 없음 |
| E 프레임워크 | Spring Boot 4.1.x | 통합 기준 기록. 실제 팀 E 코드 수령 후 검증 |
| 프런트엔드 | Node.js 26.x | 통합 기준 기록. 현재 프런트엔드 구현 없음 |

Python 범위는 backend/pyproject.toml의 `>=3.14,<3.15`와 backend/.python-version에서 관리한다. uv.lock에 의존성을 고정했고 Ruff도 py314를 대상으로 한다. DB 기본 URL과 compose.yaml은 PostgreSQL 18을 사용한다. 루트 .java-version·.node-version은 Java 25·Node 26 선택을 기록하며, 실제 E 빌드 도구와 프런트엔드 package.json에도 코드 수령 시 같은 기준을 적용한다. 프로젝트 루트 AGENTS.md가 이후 작업의 지침이다.

현재 검증은 Python 3.14.3과 PostgreSQL 18.7에서 자동 테스트 320개, 실제 Uvicorn HTTP로 B-01~B-09 연결 흐름이다. C·D는 명시적인 합성 응답이고 E 결과와 최신 카탈로그는 임시 데이터다. B-08 UNKNOWN→APPROVED 뒤 B-03 버전 1→2→3, B-04 최신 가격 버전 4·품절 INVALID·정상 버전 5 복구 및 이전 결제 보존을 검증했다. B-03·B-04 동시성·롤백·새 혜택/승인 준비 연결·migration, 진행 중 C/D 관측과 재검증 실패의 경쟁도 검사했다. B-09는 무인증 UP 응답, 격리된 테스트 DB에서는 테이블·필수 컬럼 누락 시 DOWN과 복구를 검사했다. 연결·pool 오류 응답도 모의 예외로 검사했다. 실제 카탈로그·A 재탐색·D 승인 발급/소비·E 결제는 실행하지 않았다. PostgreSQL 검사 증거는 `backend/data/pytest-postgresql18.xml`, 실제 HTTP 결과는 `backend/data/smoke-http-result.json`이다. 이 파일들은 로컬 생성 자료이며 Git에서는 제외한다.

격리된 SQLite에서도 같은 자동 테스트 320개를 통과했다. SQLite 검사는 보조 검증으로 유지한다. 이전 SQLite 개발 데이터는 변환·삭제하지 않았으며 PostgreSQL로 자동 이전하지 않는다. 현재 애플리케이션의 기본 DB와 팀 통합 검증 기준은 PostgreSQL 18이다.

버전 변경은 사용자 지시를 기준으로 명시적으로 수행한다. 단순히 의존성 해석이 성공했다는 사실과 실제 DB·상대 서비스의 연동 성공을 구분한다. Java·Spring·Node 버전 기록은 E·프런트엔드의 통합 테스트 완료를 의미하지 않는다.

전체 명세 검토에서는 원문 계약 26개와 회귀·migration·공용 Base·재견적 후 전체 연결 검사 10개를 추가했다. head는 20261116_review다. 과거 실패 견적 보존, B-03 업무 불가 200, 최신 카탈로그 통신 실패 503, 결제 충돌 재조회 안내 유지, 변경 시각/사유와 B 전용 metadata를 확인했다. 실제 팀 코드의 import·실행·배포는 검증하지 않았다. 상세는 docs/reviews/B_API_REVIEW_2026-11-16.md에 있다.
