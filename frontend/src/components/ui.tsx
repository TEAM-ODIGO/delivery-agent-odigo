import type { ReactNode } from "react";
import type { Step } from "../api/types";
import TopBar from "./TopBar";

export function Screen({
  title,
  sub,
  step,
  danger,
  back,
  children,
  footer,
}: {
  title: string;
  sub?: string;
  step?: Step;
  danger?: boolean;
  back?: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="screen">
      <header className="screen-head">
        <TopBar />
        <h1>
          {back && (
            <button className="back" onClick={back} aria-label="뒤로">
              ‹
            </button>
          )}
          {title}
        </h1>
        {step && <StepBar step={step} danger={danger} />}
        {(sub || step) && (
          <p className="step-label">{step ? `${step.current}/${step.total} · ${step.label}` : sub}</p>
        )}
      </header>
      <main className="screen-body">{children}</main>
      {footer && <footer className="screen-foot">{footer}</footer>}
    </div>
  );
}

export function StepBar({ step, danger }: { step: Step; danger?: boolean }) {
  return (
    <div className="stepbar" role="progressbar" aria-valuenow={step.current} aria-valuemax={step.total}>
      {Array.from({ length: step.total }, (_, i) => (
        <i key={i} className={i < step.current ? (danger && i === step.total - 1 ? "on bad" : "on") : ""} />
      ))}
    </div>
  );
}

type Tone = "ok" | "pri" | "warn" | "bad" | "gray";
export function Tag({ tone = "pri", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`tag ${tone}`}>{children}</span>;
}

export function Banner({ tone = "warn", children }: { tone?: Tone; children: ReactNode }) {
  return <div className={`banner ${tone}`}>{children}</div>;
}

export function Button({
  kind = "primary",
  children,
  ...rest
}: { kind?: "primary" | "secondary" } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button className={`btn ${kind}`} {...rest}>
      {children}
    </button>
  );
}

// 사용자에게 예/아니오를 묻는 팝업. 바깥을 눌러도 닫히지 않는다 (반드시 답하게 한다).
export function Modal({ label, children, actions }: { label: string; children: ReactNode; actions: ReactNode }) {
  return (
    <div className="modal-backdrop">
      <div className="modal" role="alertdialog" aria-modal="true" aria-label={label}>
        <div className="modal-body">{children}</div>
        <div className="modal-actions">{actions}</div>
      </div>
    </div>
  );
}

export function StatusIcon({ tone, children }: { tone: "ok" | "bad" | "pri"; children: ReactNode }) {
  return <div className={`status-icon ${tone}`}>{children}</div>;
}

export function Row({ label, value, strong, tone }: { label: ReactNode; value: ReactNode; strong?: boolean; tone?: "ok" }) {
  return (
    <div className={`kv${strong ? " strong" : ""}`}>
      <span>{label}</span>
      <span className={tone === "ok" ? "ok-text" : undefined}>{value}</span>
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="spinner-wrap" role="status">
      <div className="spinner" />
      <p>{label}</p>
    </div>
  );
}

// 오래 걸리는 작업(요기요 장바구니 담기, 요기요 실시간 확인 등)을 기다리는 동안 화면 전체를 덮는 움직이는 오디고 로봇
// src: 화면마다 다른 로봇 그림 (기본은 봉투를 든 로봇, 후보 화면은 UFO 탄 로봇)
export function RobotOverlay({
  label,
  src = "/images/odigo-loading.webp",
  children,
}: {
  label: string;
  src?: string;
  children?: ReactNode;
}) {
  return (
    <div className="robot-overlay" role="status" aria-live="polite">
      <div className="robot-loader">
        <img src={src} alt="" width={220} height={220} />
        <span>{label}</span>
        {children}
      </div>
    </div>
  );
}
