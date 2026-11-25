// 02 조건 확인 (재질문) — prompt.questions
// 예산은 직접 입력하거나 +1천/+5천/+1만/+5만 버튼으로 더할 수 있다
import { useState } from "react";
import type { StepProps } from "../pages/RunPage";
import { Button, Screen } from "../components/ui";

const AMOUNT_STEPS = [
  { label: "+1천", value: 1_000 },
  { label: "+5천", value: 5_000 },
  { label: "+1만", value: 10_000 },
  { label: "+5만", value: 50_000 },
];
const MAX_AMOUNT = 10_000_000; // 배달 한 번에 1천만원을 넘는 예산은 받지 않는다 (버튼을 여러 번 눌러도 여기서 멈춤)

const SLOT_LABEL: Record<string, string> = { people: "인원", budget_krw: "예산" };

// 35000 → "3만 5,000원"
function toKoreanWon(amount: number) {
  if (amount <= 0) return "";
  const man = Math.floor(amount / 10_000);
  const rest = amount % 10_000;
  let text = "";
  if (man > 0) text += `${man.toLocaleString("ko-KR")}만`;
  if (rest > 0) text += `${text ? " " : ""}${rest.toLocaleString("ko-KR")}`;
  return `${text}원`;
}

export default function Clarify({ view, busy, send, cancel }: StepProps) {
  const prompt = view.prompt;
  const questions = prompt?.type === "CLARIFICATION" ? prompt.questions : [];
  const [values, setValues] = useState<Record<string, string>>({});
  const set = (slot: string, v: string) => setValues((cur) => ({ ...cur, [slot]: v }));
  const missing = questions.some((q) => q.required && !values[q.slot]);
  const isMoney = (slot: string) => slot.endsWith("_krw");

  const amountOf = (slot: string) => Number(values[slot] || 0);
  const setAmount = (slot: string, amount: number) => {
    const capped = Math.min(amount, MAX_AMOUNT);
    set(slot, capped > 0 ? String(capped) : "");
  };

  const submit = () =>
    send({
      type: "CLARIFICATION",
      answers: questions
        .filter((q) => values[q.slot])
        .map((q) => {
          const raw = values[q.slot];
          const num = Number(raw.replace(/[^0-9]/g, ""));
          return { slot: q.slot, value: /^\d[\d,]*$/.test(raw) ? num : raw };
        }),
    });

  return (
    <Screen
      title="조금만 더 알려주세요"
      step={view.step}
      footer={
        <>
          <Button kind="secondary" onClick={cancel} disabled={busy}>
            취소
          </Button>
          <Button onClick={submit} disabled={missing || busy}>
            {busy ? "찾는 중…" : "확인"}
          </Button>
        </>
      }
    >
      <p className="clarify-lead">이것만 알려주시면 바로 가게를 찾아 드릴게요.</p>

      {questions.map((q) => (
        <section className="card clarify-q" key={q.slot}>
          <div className="clarify-head">
            <span className="clarify-icon">
              <SlotIcon slot={q.slot} />
            </span>
            <div className="clarify-title">
              <span className="clarify-label">{SLOT_LABEL[q.slot] ?? "추가 정보"}</span>
              <b>{q.question}</b>
            </div>
          </div>

          {q.input_type === "SELECT" ? (
            <div className="choice-grid">
              {q.options.map((o) => (
                <button
                  key={String(o)}
                  className={`chip${values[q.slot] === String(o) ? " on" : ""}`}
                  onClick={() => set(q.slot, String(o))}
                >
                  {o}
                  {q.slot === "people" ? "명" : ""}
                </button>
              ))}
            </div>
          ) : isMoney(q.slot) ? (
            <>
              <label className="amount-field">
                <input
                  className="amount-input"
                  inputMode="numeric"
                  placeholder="금액 입력"
                  value={values[q.slot] ? amountOf(q.slot).toLocaleString("ko-KR") : ""}
                  onChange={(e) => setAmount(q.slot, Number(e.target.value.replace(/[^0-9]/g, "")))}
                  aria-label={q.question}
                />
                <span>원</span>
              </label>
              <div className="amount-hint">
                <span>{toKoreanWon(amountOf(q.slot))}</span>
                {values[q.slot] && (
                  <button type="button" className="amount-reset" onClick={() => setAmount(q.slot, 0)}>
                    지우기
                  </button>
                )}
              </div>
              <div className="amount-quick">
                {AMOUNT_STEPS.map((s) => (
                  <button
                    type="button"
                    key={s.value}
                    onClick={() => setAmount(q.slot, amountOf(q.slot) + s.value)}
                    disabled={amountOf(q.slot) >= MAX_AMOUNT}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <input
              className="input"
              value={values[q.slot] ?? ""}
              onChange={(e) => set(q.slot, e.target.value)}
              aria-label={q.question}
            />
          )}
        </section>
      ))}
    </Screen>
  );
}

function SlotIcon({ slot }: { slot: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {slot === "people" ? (
        <>
          <circle cx="9" cy="8" r="3.5" />
          <path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6" />
          <path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5c1.8.8 3 2.7 3 5.5" />
        </>
      ) : slot.endsWith("_krw") ? (
        <>
          <rect x="3" y="6" width="18" height="12" rx="2" />
          <circle cx="12" cy="12" r="2.5" />
        </>
      ) : (
        <>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 8v5M12 16h.01" />
        </>
      )}
    </svg>
  );
}
