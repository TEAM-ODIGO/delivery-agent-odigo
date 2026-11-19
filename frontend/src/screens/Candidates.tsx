// 03 후보 선택 / 03b 후보 다시 선택 (notice CANDIDATE_UNAVAILABLE)
// 예산 초과 확인(로컬 확장): 예산 안의 후보가 하나도 없으면 팝업으로 묻는다.
//   예 → 예산 초과를 알고 진행한다는 뜻으로 선택 응답에 allow_over_budget을 실어 보낸다.
//   아니오 → 주문을 취소하고 처음 화면으로 돌아간다.
// 최소주문금액 미달(로컬 확장): orderable=false인 후보는 비활성으로 보이고 선택할 수 없다.
//   카드 아래에 "최소주문금액이 맞지 않아서 주문이 불가합니다"를 보여 준다.
import { useEffect, useState } from "react";
import type { StepProps } from "../pages/RunPage";
import type { Candidate } from "../api/types";
import { Banner, Button, Modal, Screen, Tag } from "../components/ui";
import { krw } from "../lib/format";

const isOver = (c: Candidate) => c.violations.some((v) => v.code === "BUDGET_EXCEEDED");
const isOrderable = (c: Candidate) => c.orderable !== false;
const MIN_ORDER_TEXT = "최소주문금액이 맞지 않아서 주문이 불가합니다";

export default function Candidates({ view, busy, send, cancel, decline }: StepProps) {
  const prompt = view.prompt;
  const list = prompt?.type === "SELECT_CANDIDATE" ? prompt.candidates : [];
  const orderable = list.filter(isOrderable);
  const [picked, setPicked] = useState<number | null>(orderable[0]?.index ?? null);
  const chosen = orderable.find((c) => c.index === picked) ?? orderable[0];
  const nothingOrderable = list.length > 0 && orderable.length === 0;

  const noneWithinBudget = orderable.length > 0 && orderable.every(isOver);
  const [consent, setConsent] = useState(false); // 예산 초과를 알고 계속하겠다고 답했는가
  const [ask, setAsk] = useState<"all" | "one" | null>(noneWithinBudget ? "all" : null);
  // 후보가 줄어(03b) 남은 후보가 모두 예산 초과가 되면 다시 묻는다
  useEffect(() => {
    if (noneWithinBudget && !consent) setAsk("all");
  }, [noneWithinBudget, consent]);

  const submit = (allowOverBudget: boolean) =>
    chosen &&
    send({
      type: "SELECT_CANDIDATE",
      candidate_index: chosen.index,
      ...(allowOverBudget ? { allow_over_budget: true } : {}),
    });

  const choose = () => {
    if (!chosen) return;
    if (!isOver(chosen)) return submit(false);
    if (consent) return submit(true);
    setAsk("one"); // 예산 안 후보도 있는데 예산 초과 후보를 골랐을 때
  };

  return (
    <Screen
      title="이런 곳은 어때요?"
      step={view.step}
      footer={
        nothingOrderable ? (
          <Button onClick={decline} disabled={busy}>
            조건 바꿔 다시 주문
          </Button>
        ) : (
          <>
            <Button kind="secondary" onClick={cancel} disabled={busy}>
              취소
            </Button>
            <Button disabled={!chosen || busy} onClick={choose}>
              {busy ? "주문안 만드는 중…" : "이 가게로"}
            </Button>
          </>
        )
      }
    >
      {view.notice && <Banner tone="warn">{view.notice.message}</Banner>}
      {consent && <Banner tone="pri">예산을 넘는 가게로 계속 진행해요. 결제 전에 금액을 한 번 더 확인해 드려요.</Banner>}
      {nothingOrderable && (
        <Banner tone="warn">지금 조건으로는 주문할 수 있는 가게가 없어요. 금액이나 인원을 바꿔 다시 주문해 주세요.</Banner>
      )}
      {list.map((c) => (
        <CandidateCard key={c.index} c={c} selected={chosen?.index === c.index} onClick={() => setPicked(c.index)} />
      ))}

      {ask === "all" && (
        <Modal
          label="예산 초과 확인"
          actions={
            <>
              <Button kind="secondary" onClick={decline} disabled={busy} autoFocus>
                아니오
              </Button>
              <Button
                onClick={() => {
                  setConsent(true);
                  setAsk(null);
                }}
                disabled={busy}
              >
                예
              </Button>
            </>
          }
        >
          <b>지정 금액에 해당하는 메뉴와 가게가 없어서 예산 초과의 가게를 추천합니다.</b>
          <span>계속 주문하시겠습니까?</span>
        </Modal>
      )}
      {ask === "one" && chosen && (
        <Modal
          label="예산 초과 확인"
          actions={
            <>
              <Button kind="secondary" onClick={() => setAsk(null)} disabled={busy} autoFocus>
                아니오
              </Button>
              <Button
                onClick={() => {
                  setConsent(true);
                  setAsk(null);
                  submit(true);
                }}
                disabled={busy}
              >
                예
              </Button>
            </>
          }
        >
          <b>선택한 가게는 예산을 넘어요.</b>
          <span>
            {chosen.violations.find((v) => v.code === "BUDGET_EXCEEDED")?.message ?? "지정 금액보다 비싼 가게예요."}
          </span>
          <span>계속 주문하시겠습니까?</span>
        </Modal>
      )}
    </Screen>
  );
}

function CandidateCard({ c, selected, onClick }: { c: Candidate; selected: boolean; onClick: () => void }) {
  const r = c.restaurant;
  const meta = [
    r.rating !== null ? `★ ${r.rating.toFixed(1)}` : null,
    r.delivery_minutes !== null ? `약 ${r.delivery_minutes}분` : null,
    r.delivery_fee_krw !== null ? `배달비 ${krw(r.delivery_fee_krw)}` : null,
  ].filter(Boolean);
  const local =
    r.local_currency_merchant === true
      ? r.local_currency_basis === "KOMSCO_MATCHED"
        ? "지역화폐 가능"
        : "지역화폐 가능(추정)"
      : null;
  const blocked = !isOrderable(c);
  const minOrderText = c.violations.find((v) => v.code === "MIN_ORDER_NOT_MET")?.message ?? MIN_ORDER_TEXT;
  const violationText = c.violations
    .filter((v) => v.code !== "MIN_ORDER_NOT_MET")
    .map((v) => v.message)
    .filter(Boolean);

  return (
    <button
      className={`card${selected && !blocked ? " selected" : ""}`}
      onClick={onClick}
      aria-pressed={blocked ? undefined : selected}
      disabled={blocked}
    >
      <div className="title-row">
        <b>{c.restaurant_name}</b>
        <span className="price">{krw(c.total_krw)}</span>
      </div>
      {meta.length > 0 && <span className="muted">{meta.join(" · ")}</span>}
      <span className="small">
        {c.items.map((i) => `${i.name} ${i.quantity}${i.unit_price_krw !== null ? ` × ${krw(i.unit_price_krw)}` : ""}`).join(", ")}
      </span>
      <div className="tags">
        {blocked ? (
          <Tag tone="bad">주문 불가</Tag>
        ) : c.meets_all_constraints ? (
          <Tag tone="ok">조건 충족</Tag>
        ) : (
          <Tag tone="warn">조건 일부 벗어남</Tag>
        )}
        {local && <Tag tone="pri">{local}</Tag>}
      </div>
      {blocked && (
        <>
          <span className="small bad-text">{minOrderText}</span>
          {c.min_order && (
            <span className="small">
              필요한 최소주문금액 {krw(c.min_order.required_krw)} · 현재 주문 {krw(c.min_order.order_krw)}
            </span>
          )}
        </>
      )}
      {violationText.length > 0 ? (
        <span className="small" style={{ color: "var(--bad)" }}>
          {violationText.join(" ")}
        </span>
      ) : (
        c.reasons[0] && <span className="muted small">{c.reasons[0]}</span>
      )}
    </button>
  );
}
