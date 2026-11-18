// 05 승인 — prompt.order / checks / unverified_conditions / expires_at
// NFC 실물 카드(로컬 확장): .env에 VITE_NFC_PAY=1이면 '승인하고 결제'를 누른 뒤 카드를 휴대폰에 대야 결제된다
import { useRef, useState } from "react";
import type { StepProps } from "../pages/RunPage";
import { Banner, Button, Row, Screen, Tag } from "../components/ui";
import { clock, krw, secondsLeft, unverifiedTexts } from "../lib/format";
import { useNow } from "../lib/hooks";

const NFC_PAY = import.meta.env.VITE_NFC_PAY === "1";

// 카드가 휴대폰에 닿으면 카드 일련번호(UID)를 돌려준다. 안드로이드 크롬(Web NFC)에서만 동작한다.
function readCardSerial(signal: AbortSignal): Promise<string> {
  if (!("NDEFReader" in window)) {
    return Promise.reject(new Error("이 기기에서는 NFC를 쓸 수 없어요. 안드로이드 휴대폰의 크롬에서 열어 주세요."));
  }
  const reader = new (window as any).NDEFReader();
  return new Promise((resolve, reject) => {
    signal.addEventListener("abort", () => reject(new Error("태그를 취소했어요.")));
    reader.onreading = (event: any) => resolve(event.serialNumber);
    reader.onreadingerror = () => {
      reject(new Error("카드를 읽지 못했어요. 카드를 휴대폰 뒷면 가운데에 다시 대 주세요."));
    };
    reader.scan({ signal }).catch(reject);
  });
}

export default function Approval({ view, busy, send, cancel }: StepProps) {
  const now = useNow();
  const prompt = view.prompt;
  const [tapping, setTapping] = useState(false);
  const [tapError, setTapError] = useState<string | null>(null);
  const tapCtrl = useRef<AbortController | null>(null);
  if (prompt?.type !== "APPROVAL") return null;
  const { order, checks, unverified_conditions, expires_at, split } = prompt;
  const left = secondsLeft(expires_at, now);
  const expired = left === 0;
  const unverified = unverifiedTexts(unverified_conditions);

  async function approve() {
    if (!NFC_PAY) {
      send({ type: "APPROVE" });
      return;
    }
    const ctrl = new AbortController();
    tapCtrl.current = ctrl;
    setTapError(null);
    setTapping(true);
    try {
      const serial = await readCardSerial(ctrl.signal);
      if (!serial) {
        throw new Error("카드 일련번호를 읽지 못했어요. 다시 대 주세요.");
      }
      send({ type: "APPROVE", card_tap: { serial_number: serial } });
    } catch (err) {
      setTapError(err instanceof Error ? err.message : "카드를 읽지 못했어요.");
    } finally {
      ctrl.abort(); // 다 읽었으면 NFC 스캔을 끈다
      tapCtrl.current = null;
      setTapping(false);
    }
  }

  return (
    <Screen
      title="이대로 결제할까요?"
      step={view.step}
      footer={
        <>
          {tapping ? (
            <Button kind="secondary" onClick={() => tapCtrl.current?.abort()}>
              태그 취소
            </Button>
          ) : (
            <Button kind="secondary" onClick={cancel} disabled={busy}>
              취소
            </Button>
          )}
          <Button onClick={approve} disabled={busy || expired || tapping}>
            {busy ? "결제 요청 중…" : tapping ? "카드를 대 주세요…" : NFC_PAY ? "승인하고 카드 태그" : "승인하고 결제"}
          </Button>
        </>
      }
    >
      <div className="card">
        <div className="title-row">
          <b>{order.restaurant_name}</b>
          {order.delivery_minutes !== null && <span className="muted">약 {order.delivery_minutes}분</span>}
        </div>
        {order.items.map((i) => (
          <Row
            key={i.name}
            label={`${i.name} × ${i.quantity}`}
            value={i.unit_price_krw !== null ? krw(i.unit_price_krw * i.quantity) : "—"}
          />
        ))}
        <Row label="배달비" value={krw(order.delivery_fee_krw)} />
        {order.instant_discount_krw > 0 && (
          <Row label={`${order.payment_method.name} 즉시할인`} value={`−${krw(order.instant_discount_krw)}`} tone="ok" />
        )}
        <div className="divider" />
        <div className="title-row">
          <b>결제 금액</b>
          <span className="big-price">{krw(order.charge_krw)}</span>
        </div>
        {split ? (
          <>
            <Row label={`지역화폐 · ${split.local_currency.name ?? split.local_currency.payment_method_id}`} value={krw(split.local_currency.charge_krw)} />
            <Row label={`카드 · ${split.remainder.name ?? split.remainder.payment_method_id}`} value={krw(split.remainder.charge_krw)} />
          </>
        ) : (
          <span className="muted">결제수단 {order.payment_method.name}</span>
        )}
      </div>

      {checks.length > 0 && (
        <>
          <div className="section-title">확인한 조건</div>
          {checks.map((c) => (
            <div className="check-row" key={c}>
              <span>✓ {c}</span>
              <Tag tone="ok">D 검증</Tag>
            </div>
          ))}
        </>
      )}

      {unverified.length > 0 && (
        <>
          <div className="section-title">확인 못 한 조건</div>
          {unverified.map((u) => (
            <Banner key={u}>{u}</Banner>
          ))}
        </>
      )}

      {expires_at && (
        <Banner tone={expired ? "bad" : "pri"}>
          {expired ? "승인 유효시간이 지났어요. 취소 후 다시 주문해 주세요." : `승인은 ${clock(expires_at)}까지 유효해요`}
        </Banner>
      )}
      {tapping && (
        <Banner tone="pri">결제할 NFC 카드({order.payment_method.name})를 휴대폰 뒷면에 대 주세요</Banner>
      )}
      {tapError && <Banner tone="bad">{tapError}</Banner>}
      <p className="faint">
        {NFC_PAY ? "카드를 태그하면 바로 결제가 시작되고, 그 뒤에는 취소할 수 없어요." : "승인하면 바로 결제가 시작되고, 그 뒤에는 취소할 수 없어요."}
      </p>
    </Screen>
  );
}
