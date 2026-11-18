"""NFC 카드 태그 확인 (로컬 확장, 팀 합의 전).

실물 NFC 카드(NTAG213)에는 결제 정보가 없다. 둘 중 하나로 '이 가상카드의 실물 카드를 댔다'를 확인한다.
  - 토큰: 카드에 써 둔 URL(http://.../tap?t=토큰)의 t 값. 아이폰·안드로이드 모두 쓸 수 있다
          (아이폰은 웹에서 NFC를 직접 못 읽어서, iOS가 카드의 URL을 Safari로 열어 줄 때 토큰이 전달된다)
  - 일련번호(UID): 카드마다 다른 고유 번호. 안드로이드 크롬(Web NFC)에서만 읽힌다
어떤 카드가 어떤 가상카드인지는 .env로 정한다 (둘 중 하나만 써도 된다).
    E_NFC_TOKENS=CARD001=카드에_쓴_토큰
    E_NFC_CARDS=CARD001=04:2C:98:1A:32:24:91

확인 규칙
  - 가상카드에 토큰이나 UID가 등록돼 있는데 태그 없이 결제하면 → NFC_CARD_REQUIRED
  - 태그한 값이 어디에도 등록돼 있지 않으면                    → NFC_CARD_NOT_REGISTERED
  - 태그한 값이 다른 가상카드에 등록돼 있으면                   → NFC_CARD_MISMATCH
  - 아무것도 등록되지 않은 가상카드를 태그 없이 결제하면        → 지금처럼 통과 (기존 동작 유지)
"""

import hmac

MESSAGES = {
    "NFC_CARD_REQUIRED": "이 카드는 실물 NFC 카드를 태그해야 결제할 수 있어요.",
    "NFC_TAP_EMPTY": "카드 정보를 읽지 못했어요. 다시 대 주세요.",
    "NFC_CARD_NOT_REGISTERED": "등록되지 않은 NFC 카드예요.",
    "NFC_CARD_MISMATCH": "선택한 결제수단과 다른 카드를 태그했어요.",
}


def normalize_serial(serial: str) -> str:
    """'04:2C:98:1A:32:24:91', '04-2c-98-...' 처럼 구분자·대소문자가 달라도 같은 값으로 본다."""
    return serial.replace(":", "").replace("-", "").replace(" ", "").strip().lower()


def parse_pairs(raw: str, normalize=None) -> dict[str, str]:
    """'CARD001=값,CARD002=값' → {'CARD001': '값', 'CARD002': '값'}"""
    pairs = {}
    for item in raw.split(","):
        if "=" not in item:
            continue
        payment_method_id, value = item.split("=", 1)
        payment_method_id = payment_method_id.strip()
        value = value.strip()
        if normalize is not None:
            value = normalize(value)
        if payment_method_id and value:
            pairs[payment_method_id] = value
    return pairs


def parse_cards(raw: str) -> dict[str, str]:
    return parse_pairs(raw, normalize_serial)


def parse_tokens(raw: str) -> dict[str, str]:
    return parse_pairs(raw)


def _same(a: str, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a, b)


def check(
    nfc_cards: dict[str, str], nfc_tokens: dict[str, str], payment_method_id: str, card_tap: dict | None
) -> str | None:
    """결제해도 되면 None, 안 되면 사유 코드를 돌려준다."""
    expected_serial = nfc_cards.get(payment_method_id)
    expected_token = nfc_tokens.get(payment_method_id)
    if card_tap is None:
        if expected_serial or expected_token:
            return "NFC_CARD_REQUIRED"
        return None

    serial = normalize_serial(card_tap.get("serial_number") or "")
    token = (card_tap.get("token") or "").strip()
    if not serial and not token:
        return "NFC_TAP_EMPTY"
    if _same(token, expected_token) or _same(serial, expected_serial):
        return None

    for bound_token in nfc_tokens.values():
        if _same(token, bound_token):
            return "NFC_CARD_MISMATCH"
    for bound_serial in nfc_cards.values():
        if _same(serial, bound_serial):
            return "NFC_CARD_MISMATCH"
    return "NFC_CARD_NOT_REGISTERED"
