# 팀 작업 규칙

## 폴더 담당 (자기 폴더만 고친다)
| 폴더 | 담당 |
|---|---|
| `app/modules/discovery/` (A) | A 담당 |
| `app/modules/ordering/` (B) | B 담당 |
| `app/modules/benefit/` (C) | 사은서 |
| `app/modules/delegation/` (D), `app/modules/orchestrator/` (O) | O·D 담당 |
| `app/core/`, `app/main.py`, `app/modules/orchestrator/wiring.py` | 공용: 바꾸기 전에 팀에 알린다 |

## 브랜치
- `main`은 항상 `uv run pytest`가 통과하는 상태로 둔다. 직접 push하지 않는다.
- 작업은 `feature/<모듈>-<내용>` 브랜치(예: `feature/c-split-payment`)에서 하고 Pull Request로 합친다.
- PR 전에 `uv run pytest`를 돌린다.

## 하지 말 것
- `.env`, 토큰, 비밀번호 커밋 금지 (`.env.example`만 올린다)
- 다른 모듈 폴더 직접 수정 금지 (담당자에게 요청)
- 다른 모듈이 부르는 함수 모양·응답 필드 이름 변경 금지 (명세 변경은 팀 합의 후)
