// 03 후보 선택 / 03b 후보 다시 선택 (notice CANDIDATE_UNAVAILABLE)
import { useState } from "react";
import type { StepProps } from "../pages/RunPage";
import type { Candidate } from "../api/types";
import { Banner, Button, Screen, Tag } from "../components/ui";
import { krw } from "../lib/format";

export default function Candidates({ view, busy, send, cancel }: StepProps) {
  const prompt = view.prompt;
  const list = prompt?.type === "SELECT_CANDIDATE" ? prompt.candidates : [];
  const [picked, setPicked] = useState<number | null>(list[0]?.index ?? null);
  const chosen = list.find((c) => c.index === picked) ?? list[0];

  return (
    <Screen
      title="이런 곳은 어때요?"
      step={view.step}
      footer={
        <>
          <Button kind="secondary" onClick={cancel} disabled={busy}>
            취소
          </Button>
          <Button
            disabled={!chosen || busy}
            onClick={() => chosen && send({ type: "SELECT_CANDIDATE", candidate_index: chosen.index })}
          >
            {busy ? "주문안 만드는 중…" : "이 가게로"}
          </Button>
        </>
      }
    >
      {view.notice && <Banner tone="warn">{view.notice.message}</Banner>}
      {list.map((c) => (
        <CandidateCard key={c.index} c={c} selected={chosen?.index === c.index} onClick={() => setPicked(c.index)} />
      ))}
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
