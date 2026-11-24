// 05 승인 — prompt.order / checks / unverified_conditions / expires_at
// NFC 실물 카드(로컬 확장): .env에 VITE_NFC_PAY=1이면 '승인하고 결제'를 누른 뒤 카드를 휴대폰에 대야 결제된다
import { useEffect, useRef, useState } from "react";
import type { StepProps } from "../pages/RunPage";
import {
  canReadNfcInPage,
  clearPendingTap,
  isIPhone,
  isPendingKey,
  loadPendingTap,
  NFC_PAY,
  readCardTap,
  savePendingTap,
} from "../lib/nfc";
import { Banner, Button, RobotOverlay, Row, Screen, Tag } from "../components/ui";
import { clock, krw, secondsLeft, unverifiedTexts } from "../lib/format";
import { useNow } from "../lib/hooks";
import YogiyoCart, { cartBlockReason, cartWaitingText, useAutoStartCart, useYogiyoCart } from "../components/YogiyoCart";
import { useIsMutating } from "@tanstack/react-query";

export default function Approval({ view, busy, send, back }: StepProps) {
  const now = useNow();
  const prompt = view.prompt;
  const [tapping, setTapping] = useState(false);
  const [tapError, setTapError] = useState<string | null>(null);
  // 요기요 금액 반영을 시도한 담기 작업 (이슈 #86): 같은 결과로 두 번 보내지 않고, 실패하면 막지 않는다
  const [requoteTried, setRequoteTried] = useState<string | null>(null);
  const tapCtrl = useRef<AbortController | null>(null);
  // 아이폰: 웹에서 NFC를 직접 못 읽는다. 카드를 대면 iOS가 카드의 URL(/tap)을 Safari로 열고, 그 화면이 결제를 보낸다
  const iphoneMode = NFC_PAY && !canReadNfcInPage() && isIPhone();
  const [waitingIphone, setWaitingIphone] = useState(() => iphoneMode && loadPendingTap() === view.request_id);

  // /tap 화면(다른 탭)이 결제를 보내면 기다리던 표시가 지워진다 → 이 화면도 새로 불러와 결과로 넘어간다
  useEffect(() => {
    if (!waitingIphone) return;
    const onStorage = (e: StorageEvent) => {
      if (isPendingKey(e.key) && loadPendingTap() !== view.request_id) window.location.reload();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [waitingIphone, view.request_id]);

  if (prompt?.type !== "APPROVAL") return null;
  const { order, checks, unverified_conditions, expires_at, split, over_budget, requote } = prompt;
  const left = secondsLeft(expires_at, now);
  const expired = left === 0;
  const unverified = unverifiedTexts(unverified_conditions);
  const cart = useYogiyoCart(view.request_id);
  useAutoStartCart(view.request_id);
  const starting = useIsMutating({ mutationKey: ["yogiyo-cart-start", view.request_id] }) > 0;
  const cartBlock = cartBlockReason(cart.data, starting);
  // 요기요 장바구니 금액이 견적과 다르면 그 금액으로 견적·승인을 다시 만든다 (이슈 #86, 서버가 B·C·D를 다시 거침)
  const mismatchJob = cart.data?.status === "CART_MISMATCH" ? cart.data.job_id : undefined;
  const needsRequote = !!mismatchJob && requote?.cart_job_id !== mismatchJob && requoteTried !== mismatchJob;
  useEffect(() => {
    if (!needsRequote || busy || !mismatchJob) return;
    setRequoteTried(mismatchJob);
    send({ type: "REQUOTE_FROM_CART" });
  }, [needsRequote, busy, mismatchJob, send]);
  const requoting = !!mismatchJob && requote?.cart_job_id !== mismatchJob && (needsRequote || busy);
  // 자동 재계산 요청이 실패했으면(서버 오류 등) 조용히 넘어가지 않고 다시 시도할 수 있게 보여 준다
  const requoteFailed = !!mismatchJob && requote?.cart_job_id !== mismatchJob && requoteTried === mismatchJob && !busy;
  // NFC 태그는 카드 결제일 때만 받는다. 지역화폐 단독 결제는 카드가 없으니 태그 없이 바로 결제하고,
  // 잔액이 모자라면 E가 INSUFFICIENT_BALANCE로 거절한다. (분할결제는 payment_method가 나머지를 낼 카드라 태그 필요)
  const needsTap = NFC_PAY && order.payment_method.type !== "LOCAL_CURRENCY";

  async function approve() {
    if (!needsTap) {
      send({ type: "APPROVE" });
      return;
    }
    setTapError(null);
    if (iphoneMode) {
      savePendingTap(view.request_id);
      setWaitingIphone(true);
      return;
    }
    if (!canReadNfcInPage()) {
      setTapError("이 브라우저에서는 NFC 카드를 읽을 수 없어요. 안드로이드 크롬이나 아이폰 Safari에서 열어 주세요.");
      return;
    }
    const ctrl = new AbortController();
    tapCtrl.current = ctrl;
    setTapping(true);
    try {
      const card_tap = await readCardTap(ctrl.signal);
      send({ type: "APPROVE", card_tap });
    } catch (err) {
      setTapError(err instanceof Error ? err.message : "카드를 읽지 못했어요.");
    } finally {
      ctrl.abort(); // 다 읽었으면 NFC 스캔을 끈다
      tapCtrl.current = null;
      setTapping(false);
    }
  }

  function stopTap() {
    tapCtrl.current?.abort();
    if (waitingIphone) {
      clearPendingTap();
      setWaitingIphone(false);
    }
  }

  const waiting = tapping || waitingIphone;
  // 요기요에서 담는 중이거나 요기요 금액으로 다시 계산하는 중이면 화면 전체에 로봇 로딩을 띄운다 (실패하면 내린다)
  const cartLoading = (!!cartBlock && cart.data?.status !== "FAILED") || requoting;

  return (
    <Screen
      title="이대로 결제할까요?"
      step={view.step}
      footer={
        <>
          {/* 이전 (이슈 #68): 주문을 취소하고 같은 문장이 채워진 입력 화면으로 */}
          {waiting ? (
            <Button kind="secondary" onClick={stopTap}>
              태그 취소
            </Button>
          ) : (
            <Button kind="secondary" onClick={back} disabled={busy}>
              이전
            </Button>
          )}
          <Button onClick={approve} disabled={busy || expired || !!cartBlock || waiting || requoting}>
            {requoting
              ? "요기요 금액으로 다시 계산 중…"
              : busy
              ? "결제 요청 중…"
              : waiting
                ? "카드를 대 주세요…"
                : cartBlock && cart.data?.status !== "FAILED"
                  ? "요기요 확인 중…"
                  : needsTap
                    ? "승인하고 카드 태그"
                    : "승인하고 결제"}
          </Button>
        </>
      }
    >
      {cartLoading && (
        <RobotOverlay label={requoting ? "요기요 금액으로 다시 계산 중…" : cartWaitingText(cart.data)}>
          <span className="faint">요기요 장바구니에 같은 메뉴를 담아 금액을 맞추고 있어요</span>
          {!requoting && (
            <button className="link" onClick={back} disabled={busy}>
              이전으로
            </button>
          )}
        </RobotOverlay>
      )}
      {over_budget && (
        <Banner tone="warn">
          예산 {krw(over_budget.limit_krw)}보다 {krw(over_budget.over_krw)} 많은 주문이에요. 앞에서 확인하신 대로
          진행돼요.
        </Banner>
      )}
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
          <Row label={`${order.payment_method.name} 할인`} value={`−${krw(order.instant_discount_krw)}`} tone="ok" />
        )}
        <div className="divider" />
        {requote && requote.previous.charge_krw !== order.charge_krw && (
          <div className="title-row price-old">
            <span>이전 견적</span>
            <s>{krw(requote.previous.charge_krw)}</s>
          </div>
        )}
        <div className="title-row">
          <b>내실 금액</b>
          <span className="big-price">{krw(order.charge_krw)}</span>
        </div>
        {requote && (
          <div className="requote-note">
            <Tag tone="pri">요기요 실시간 반영</Tag>
            <span className="muted">
              {requote.changes
                .map((c) => `${c.kind === "DELIVERY_FEE" ? "배달비" : c.name ?? "메뉴"} ${krw(c.from_krw)} → ${krw(c.to_krw)}`)
                .join(" · ")}
            </span>
          </div>
        )}
        {split ? (
          <>
            <Row label={`지역화폐 · ${split.local_currency.name ?? split.local_currency.payment_method_id}`} value={krw(split.local_currency.charge_krw)} />
            <Row label={`카드 · ${split.remainder.name ?? split.remainder.payment_method_id}`} value={krw(split.remainder.charge_krw)} />
          </>
        ) : (
          <span className="muted">{order.payment_method.name}(으)로 결제해요</span>
        )}
      </div>

      {checks.length > 0 && (
        <>
          <div className="section-title">말씀하신 조건에 맞는지 확인했어요</div>
          {checks.map((c) => (
            <div className="check-row" key={c}>
              <span>✓ {c}</span>
            </div>
          ))}
        </>
      )}

      {unverified.length > 0 && (
        <>
          <div className="section-title">확인하지 못한 부분</div>
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
      <YogiyoCart requestId={view.request_id} requotedJobId={requote?.cart_job_id} />
      {requoteFailed && (
        <>
          <Banner tone="bad">요기요 금액으로 다시 계산하지 못했어요. 지금 내실 금액은 이전 견적 기준이에요.</Banner>
          <Button kind="secondary" onClick={() => send({ type: "REQUOTE_FROM_CART" })}>
            요기요 금액으로 다시 계산
          </Button>
        </>
      )}
      {cartBlock && <Banner tone={cart.data?.status === "FAILED" ? "bad" : "pri"}>{cartBlock}</Banner>}
      {tapping && (
        <Banner tone="pri">결제할 NFC 카드({order.payment_method.name})를 휴대폰 뒷면에 대 주세요</Banner>
      )}
      {waitingIphone && (
        <Banner tone="pri">
          결제할 NFC 카드({order.payment_method.name})를 아이폰 윗부분에 대고, 화면에 뜨는 알림을 눌러 주세요. (5분 안에)
        </Banner>
      )}
      {tapError && <Banner tone="bad">{tapError}</Banner>}
      <p className="faint">
        {needsTap
          ? "카드를 태그하면 바로 결제가 시작되고, 그 뒤에는 취소할 수 없어요."
          : "승인하면 바로 결제가 시작되고, 그 뒤에는 취소할 수 없어요."}
      </p>
    </Screen>
  );
}
