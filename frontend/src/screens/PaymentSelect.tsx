// 04 결제수단 선택 — prompt.options / order / excluded / expires_at
import { useState } from "react";
import type { StepProps } from "../pages/RunPage";
import { Banner, Button, Row, Screen, Tag } from "../components/ui";
import { clock, EXCLUDE_REASON, krw, secondsLeft } from "../lib/format";
import { useNow } from "../lib/hooks";

export default function PaymentSelect({ view, busy, send, cancel }: StepProps) {
  const now = useNow();
  const prompt = view.prompt;
  const [picked, setPicked] = useState<string | null>(
    prompt?.type === "SELECT_PAYMENT_METHOD" ? prompt.options[0]?.payment_method_id ?? null : null,
  );
  const [splitOn, setSplitOn] = useState(false);
  const [localText, setLocalText] = useState("");
  if (prompt?.type !== "SELECT_PAYMENT_METHOD") return null;
  const { options, order, excluded, expires_at } = prompt;
  const left = secondsLeft(expires_at, now);
  const expired = left === 0;

  // 분할결제(로컬 확장): 지역화폐로 낼 금액을 먼저 정하고, 나머지는 카드로
  const hasLocal = options.some((o) => o.method_type === "LOCAL_CURRENCY");
  const cards = options.filter((o) => o.method_type !== "LOCAL_CURRENCY");
  const canSplit = hasLocal && cards.length > 0 && (order.total_krw ?? 0) > 1;
  const splitting = splitOn && canSplit;
  const pickable = splitting ? cards : options;
  const chosen = pickable.find((o) => o.payment_method_id === picked) ?? pickable[0];
  const localAmount = Number(localText.replace(/[^0-9]/g, "")) || 0;
  const localOk = localAmount >= 1 && localAmount < (order.total_krw ?? 0);
  const localHint =
    localText === "" ? null : !localOk ? `1원 이상, 주문 금액(${krw(order.total_krw)}) 미만으로 입력해 주세요` : null;

  return (
    <Screen
      title="어떤 걸로 결제할까요?"
      step={{ ...view.step, label: `${view.step.label} · 혜택 큰 순` }}
      footer={
        <>
          <Button kind="secondary" onClick={cancel} disabled={busy}>
            취소
          </Button>
          <Button
            disabled={!chosen || busy || expired || (splitting && !localOk)}
            onClick={() =>
              chosen &&
              send({
                type: "SELECT_PAYMENT_METHOD",
                payment_method_id: chosen.payment_method_id,
                ...(splitting ? { local_currency_amount_krw: localAmount } : {}),
              })
            }
          >
            {busy
              ? "조건 확인 중…"
              : splitting
                ? localOk
                  ? `지역화폐 ${krw(localAmount)} + ${chosen?.name ?? "카드"}로 진행`
                  : "지역화폐 금액을 입력해 주세요"
                : chosen
                  ? `${krw(chosen.charge_krw)}으로 진행`
                  : "진행"}
          </Button>
        </>
      }
    >
      <div className="card soft">
        <b className="small">
          {order.restaurant_name} · {order.items.map((i) => `${i.name} ${i.quantity}`).join(", ")}
        </b>
        <Row label="메뉴" value={krw(order.menu_subtotal_krw)} />
        <Row label="배달비" value={krw(order.delivery_fee_krw)} />
        <Row label="주문 금액" value={krw(order.total_krw)} strong />
      </div>

      {canSplit && (
        <div className="card soft">
          <label className="pm-row" style={{ alignItems: "center" }}>
            <input type="checkbox" checked={splitOn} onChange={(e) => setSplitOn(e.target.checked)} />
            <span className="grow">
              <b>지역화폐로 일부만 결제</b>
              <span className="muted small"> · 낼 금액을 정하고 나머지는 카드로</span>
            </span>
          </label>
          {splitting && (
            <>
              <div className="input-suffix" style={{ marginTop: 10 }}>
                <input
                  className="input"
                  inputMode="numeric"
                  placeholder="지역화폐로 낼 금액"
                  value={localText}
                  onChange={(e) => setLocalText(e.target.value)}
                  aria-label="지역화폐로 낼 금액"
                />
                <span>원</span>
              </div>
              {localHint ? (
                <span className="small" style={{ color: "var(--bad, #d33)" }}>{localHint}</span>
              ) : localOk ? (
                <span className="muted small">
                  나머지 {krw((order.total_krw ?? 0) - localAmount)}은 아래에서 고른 카드로 결제해요 (혜택은 승인 화면에서 확인)
                </span>
              ) : null}
            </>
          )}
        </div>
      )}

      {pickable.map((o) => {
        const sel = chosen?.payment_method_id === o.payment_method_id;
        return (
          <button
            key={o.payment_method_id}
            className={`card${sel ? " selected" : ""}`}
            onClick={() => setPicked(o.payment_method_id)}
            aria-pressed={sel}
          >
            <div className="opt-head">
              <span className={`radio${sel ? " on" : ""}`} />
              <span className="grow">
                <b>{o.name}</b>
                <Tag tone={o.rank === 1 ? "ok" : "pri"}>{o.rank === 1 ? "추천 1" : o.rank}</Tag>
              </span>
              <span className="price">{splitting ? "나머지" : krw(o.charge_krw)}</span>
            </div>
            <span className="muted small opt-sub">
              {o.instant_discount_krw > 0 ? `즉시할인 −${krw(o.instant_discount_krw)}` : "할인 없음"}
              {o.reasons[0] ? ` · ${o.reasons[0]}` : ""}
            </span>
          </button>
        );
      })}

      {excluded.length > 0 && (
        <div>
          <div className="muted" style={{ fontWeight: 700 }}>
            이번 주문에 쓸 수 없는 결제수단
          </div>
          {excluded.map((e) => (
            <div className="muted" key={e.payment_method_id}>
              {e.name} · {e.reason_codes.map((c) => EXCLUDE_REASON[c] ?? c).join(", ")}
            </div>
          ))}
        </div>
      )}

      {expires_at && (
        <Banner tone={expired ? "bad" : "pri"}>
          {expired ? "추천 유효시간이 지났어요. 취소 후 다시 주문해 주세요." : `이 추천은 ${clock(expires_at)}까지 유효해요`}
        </Banner>
      )}
    </Screen>
  );
}
