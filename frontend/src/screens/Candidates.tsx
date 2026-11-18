// 03 후보 선택 / 03b 후보 다시 선택 (notice CANDIDATE_UNAVAILABLE)
// 예산 초과 확인(로컬 확장): 예산 안의 후보가 하나도 없으면 팝업으로 묻는다.
//   예 → 예산 초과를 알고 진행한다는 뜻으로 선택 응답에 allow_over_budget을 실어 보낸다.
//   아니오 → 주문을 취소하고 처음 화면으로 돌아간다.
import { useEffect, useState } from "react";
import type { StepProps } from "../pages/RunPage";
import type { Candidate } from "../api/types";
import { Banner, Button, Modal, Screen, Tag } from "../components/ui";
import { krw } from "../lib/format";

const isOver = (c: Candidate) => c.violations.some((v) => v.code === "BUDGET_EXCEEDED");

export default function Candidates({ view, busy, send, cancel, decline }: StepProps) {
  const prompt = view.prompt;
  const list = prompt?.type === "SELECT_CANDIDATE" ? prompt.candidates : [];
  const [picked, setPicked] = useState<number | null>(list[0]?.index ?? null);
  const chosen = list.find((c) => c.index === picked) ?? list[0];

  const noneWithinBudget = list.length > 0 && list.every(isOver);
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
        <>
          <Button kind="secondary" onClick={cancel} disabled={busy}>
            취소
          </Button>
          <Button disabled={!chosen || busy} onClick={choose}>
            {busy ? "주문안 만드는 중…" : "이 가게로"}
          </Button>
        </>
      }
    >
      {view.notice && <Banner tone="warn">{view.notice.message}</Banner>}
      {consent && <Banner tone="pri">예산을 넘는 가게로 계속 진행해요. 결제 전에 금액을 한 번 더 확인해 드려요.</Banner>}
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
  const violationText = c.violations.map((v) => v.message).filter(Boolean);

  return (
    <button className={`card${selected ? " selected" : ""}`} onClick={onClick} aria-pressed={selected}>
      <div className="title-row">
        <b>{c.restaurant_name}</b>
        <span className="price">{krw(c.total_krw)}</span>
      </div>
      {meta.length > 0 && <span className="muted">{meta.join(" · ")}</span>}
      <span className="small">
        {c.items.map((i) => `${i.name} ${i.quantity}${i.unit_price_krw !== null ? ` × ${krw(i.unit_price_krw)}` : ""}`).join(", ")}
      </span>
      <div className="tags">
        {c.meets_all_constraints ? <Tag tone="ok">조건 충족</Tag> : <Tag tone="warn">조건 일부 벗어남</Tag>}
        {local && <Tag tone="pri">{local}</Tag>}
      </div>
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
