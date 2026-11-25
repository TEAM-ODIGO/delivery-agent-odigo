"""여러 맛 중에서 고민하는 요청 (이슈 #117).

"매콤하고 짭짤한 거랑 달달한 거 중에서 뭐가 좋을지 모르겠어"는 '매콤하면서 달달한 메뉴'(AND)가 아니라
'매콤한 쪽' 또는 '달달한 쪽'(OR)을 고르고 싶은 것이다. 저장된 조건(taste_preferences)은 그대로 두고,
고를 때만 원래 문장에서 맛을 방향별로 나눠(taste_groups) 방향마다 따로 찾은 뒤 번갈아 보여 준다.
(음료 온도 선호와 같은 방식: 조건이 아니라 탐색할 때만 쓰는 값)
"""

import re

from app.modules.discovery.extractor import extract_tastes

# 고민·선택을 나타내는 말이 있어야 나눈다 ('매콤하고 달달한 거'는 매콤달콤 하나)
CHOICE = re.compile(r"중에서|중에|중\s*(?:뭐|어떤|하나)|아니면|또는|할지|고를지|뭐가\s*(?:좋|나을|낫)|고민|vs|VS|이냐|일지")
# 방향을 나누는 연결: '짭짤한 거랑 달달한 거', '매운 거나 단 거', '국물 아니면 면'
SPLIT = re.compile(
    r"(?:거|걸|것|게|음식|맛|류|쪽|요리|메뉴)\s*(?:이랑|랑|하고|아니면|또는|이나|나|vs|VS)(?=\s|$)"
    r"|\s(?:아니면|또는|vs|VS)\s"
)


def taste_groups(message: str, tastes: list[str]) -> list[list[str]]:
    """맛을 방향별로 나눈 목록. 고민하는 말이 없거나 한 방향뿐이면 []."""
    if len(tastes) < 2 or not CHOICE.search(message or ""):
        return []
    groups: list[list[str]] = []
    placed: set[str] = set()
    for segment in SPLIT.split(message):
        found, _ = extract_tastes(segment)
        group = [code for code in found if code in tastes and code not in placed]
        if group:
            groups.append(group)
            placed.update(group)
    return groups if len(groups) >= 2 else []


def search_prefs(constraints: dict, message: str) -> dict:
    """탐색할 때만 쓰는 값. 지금은 맛 방향(taste_groups)만."""
    groups = taste_groups(message, constraints.get("taste_preferences") or [])
    return {"taste_groups": groups} if groups else {}
