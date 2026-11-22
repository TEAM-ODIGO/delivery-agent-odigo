// 첫 화면의 '카드 대고 혜택 보기' (로컬 확장, 팀 합의 전). 결제 중이 아닐 때 카드를 대면 내 카드 혜택 화면으로 간다.
//  - 안드로이드 크롬: 버튼을 누르면 Web NFC로 카드를 읽는다 (처음 한 번은 NFC 권한을 물어본다)
//  - 아이폰: 웹에서 NFC를 못 읽는다. 카드를 대면 iOS가 카드 URL(/tap?t=토큰)을 열고,
//           기다리는 주문이 없으니 /tap 화면이 혜택 화면으로 보내 준다
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { canReadNfcInPage, isIPhone, readCardTap } from "../lib/nfc";

export default function CardTapEntry() {
  const nav = useNavigate();
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ctrl = useRef<AbortController | null>(null);
  const canRead = canReadNfcInPage();
  const iphone = isIPhone();

  useEffect(() => () => ctrl.current?.abort(), []); // 화면을 떠나면 스캔을 끈다

  async function scan() {
    setError(null);
    const c = new AbortController();
    ctrl.current = c;
    setScanning(true);
    try {
      const cardTap = await readCardTap(c.signal);
      nav("/card", { state: { cardTap } });
    } catch (err) {
      if (!c.signal.aborted) setError(err instanceof Error ? err.message : "카드를 읽지 못했어요.");
    } finally {
      c.abort();
      ctrl.current = null;
      setScanning(false);
    }
  }

  function stop() {
    ctrl.current?.abort();
    setScanning(false);
  }

  return (
    <div className="card card-tap">
      <div className="title-row">
        <b>내 카드 혜택 보기</b>
        {canRead &&
          (scanning ? (
            <button className="link" onClick={stop}>
              취소
            </button>
          ) : (
            <button className="link" onClick={scan}>
              카드 대기 ›
            </button>
          ))}
      </div>
      <span className="muted">
        {scanning
          ? "카드를 휴대폰 뒷면 가운데에 대 주세요…"
          : canRead
            ? "카드를 대면 이번 달 실적, 남은 할인 한도, 최근 결제를 보여 드려요."
            : iphone
              ? "카드를 아이폰 윗부분에 대고, 뜨는 알림을 누르면 이번 달 실적과 남은 할인 한도를 볼 수 있어요."
              : "안드로이드 크롬이나 아이폰에서 카드를 대면 이번 달 실적과 남은 할인 한도를 볼 수 있어요."}
      </span>
      {error && <span className="small bad-text">{error}</span>}
    </div>
  );
}
