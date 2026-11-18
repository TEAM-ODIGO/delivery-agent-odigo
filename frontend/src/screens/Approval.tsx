// 05 승인 — prompt.order / checks / unverified_conditions / expires_at
import type { StepProps } from "../pages/RunPage";
import { Banner, Button, Row, Screen, Tag } from "../components/ui";
import { clock, krw, secondsLeft, unverifiedTexts } from "../lib/format";
import { useNow } from "../lib/hooks";
import YogiyoCart, { cartBlockReason, useYogiyoCart } from "../components/YogiyoCart";
import { useIsMutating } from "@tanstack/react-query";

export default function Approval({ view, busy, send, cancel }: StepProps) {
  const now = useNow();
  const prompt = view.prompt;
  if (prompt?.type !== "APPROVAL") return null;
  const { order, checks, unverified_conditions, expires_at, split } = prompt;
  const left = secondsLeft(expires_at, now);
  const expired = left === 0;
  const unverified = unverifiedTexts(unverified_conditions);
  const cart = useYogiyoCart(view.request_id);
  const starting = useIsMutating({ mutationKey: ["yogiyo-cart-start", view.request_id] }) > 0;
  const cartBlock = cartBlockReason(cart.data, starting);

  return (
    <Screen
      title="이대로 결제할까요?"
      step={view.step}
      footer={
        <>
          <Button kind="secondary" onClick={cancel} disabled={busy}>
            취소
          </Button>
          <Button onClick={() => send({ type: "APPROVE" })} disabled={busy || expired || !!cartBlock}>
            {busy ? "결제 요청 중…" : "승인하고 결제"}
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
      <YogiyoCart requestId={view.request_id} />
      {cartBlock && <Banner tone="bad">{cartBlock}</Banner>}
      <p className="faint">승인하면 바로 결제가 시작되고, 그 뒤에는 취소할 수 없어요.</p>
    </Screen>
  );
}
