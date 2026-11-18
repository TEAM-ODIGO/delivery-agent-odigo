"""NFC 카드 태그 확인 (로컬 확장, 팀 합의 전).

실물 NFC 카드(NTAG213)에는 결제 정보가 없다. 카드마다 다른 고유 일련번호(UID)만 읽어서
'이 가상카드의 실물 카드를 휴대폰에 갖다 댔다'는 증거로 쓴다.
어떤 UID가 어떤 가상카드인지는 .env의 E_NFC_CARDS로 정한다.
    E_NFC_CARDS=CARD001=04:2C:98:1A:32:24:91,CARD002=04:xx:xx:xx:xx:xx:xx

확인 규칙
  - 가상카드에 UID가 등록돼 있는데 태그 없이 결제하면 → NFC_CARD_REQUIRED
  - 태그한 UID가 어디에도 등록돼 있지 않으면           → NFC_CARD_NOT_REGISTERED
  - 태그한 UID가 다른 가상카드에 등록돼 있으면          → NFC_CARD_MISMATCH
  - UID가 등록되지 않은 가상카드를 태그 없이 결제하면   → 지금처럼 통과 (기존 동작 유지)
"""

MESSAGES = {
    "NFC_CARD_REQUIRED": "이 카드는 실물 NFC 카드를 태그해야 결제할 수 있어요.",
    "NFC_SERIAL_MISSING": "카드 일련번호를 읽지 못했어요. 다시 대 주세요.",
    "NFC_CARD_NOT_REGISTERED": "등록되지 않은 NFC 카드예요.",
    "NFC_CARD_MISMATCH": "선택한 결제수단과 다른 카드를 태그했어요.",
}


def normalize_serial(serial: str) -> str:
    """'04:2C:98:1A:32:24:91', '04-2c-98-...' 처럼 구분자·대소문자가 달라도 같은 값으로 본다."""
    return serial.replace(":", "").replace("-", "").replace(" ", "").strip().lower()


def parse_cards(raw: str) -> dict[str, str]:
    """'CARD001=04:2C:...,CARD002=04:...' → {'CARD001': '042c...', 'CARD002': '04...'}"""
    cards = {}
    for item in raw.split(","):
        if "=" not in item:
            continue
        payment_method_id, serial = item.split("=", 1)
        payment_method_id = payment_method_id.strip()
        serial = normalize_serial(serial)
        if payment_method_id and serial:
            cards[payment_method_id] = serial
    return cards


def check(nfc_cards: dict[str, str], payment_method_id: str, card_tap: dict | None) -> str | None:
    """결제해도 되면 None, 안 되면 사유 코드를 돌려준다."""
    expected = nfc_cards.get(payment_method_id)
    if card_tap is None:
        if expected:
            return "NFC_CARD_REQUIRED"
        return None
    serial = normalize_serial(card_tap.get("serial_number") or "")
    if not serial:
        return "NFC_SERIAL_MISSING"
    if serial == expected:
        return None
    for bound_serial in nfc_cards.values():
        if bound_serial == serial:
            return "NFC_CARD_MISMATCH"
    return "NFC_CARD_NOT_REGISTERED"
