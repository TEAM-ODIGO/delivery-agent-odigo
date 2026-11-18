// 02 조건 확인 (재질문) — prompt.questions
import { useState } from "react";
import type { StepProps } from "../pages/RunPage";
import { Button, Screen, Tag } from "../components/ui";

export default function Clarify({ view, busy, send, cancel }: StepProps) {
  const prompt = view.prompt;
  const questions = prompt?.type === "CLARIFICATION" ? prompt.questions : [];
  const [values, setValues] = useState<Record<string, string>>({});
  const set = (slot: string, v: string) => setValues((cur) => ({ ...cur, [slot]: v }));
  const missing = questions.some((q) => q.required && !values[q.slot]);
  const isMoney = (slot: string) => slot.endsWith("_krw");

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
      {questions.map((q) => (
        <div className="card" key={q.slot}>
          <div className="title-row">
            <b>{q.question}</b>
            {q.required && <Tag tone="bad">필수</Tag>}
          </div>
          {q.input_type === "SELECT" ? (
            <div className="chips">
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
          ) : (
            <div className="input-suffix">
              <input
                className="input"
                inputMode={isMoney(q.slot) ? "numeric" : "text"}
                value={
                  isMoney(q.slot) && values[q.slot]
                    ? Number(values[q.slot].replace(/[^0-9]/g, "") || 0).toLocaleString("ko-KR")
                    : values[q.slot] ?? ""
                }
                onChange={(e) => set(q.slot, isMoney(q.slot) ? e.target.value.replace(/[^0-9]/g, "") : e.target.value)}
                aria-label={q.question}
              />
              {isMoney(q.slot) && <span>원</span>}
            </div>
          )}
        </div>
      ))}
    </Screen>
  );
}
