// 첫 접속 스플래시 (이슈 #96): 파란 배경에 ODIGO 로봇(배달 봉투를 든 그림)이 점선 길을 따라 달려 들어오고,
// "ODIGO · 말 한마디면 주문부터 결제까지"와 로딩 점이 나온 뒤 사라진다.
// - 한 탭(세션)에 한 번만. 화면을 누르면 바로 넘어간다.
// - /tap(NFC 결제)처럼 링크로 바로 들어오는 화면에서는 띄우지 않는다 (App에서 고객 화면에만 붙인다).
// - 움직임 줄이기 설정이면 애니메이션 없이 짧게 보여 준다.
import { useEffect, useState } from "react";

const SEEN_KEY = "odigo.splash.seen";
const SHOW_MS = 2300; // 등장 애니메이션(약 1.5초) + 잠깐 머묾
const FADE_MS = 350;

function alreadySeen(): boolean {
  // NFC 카드 태그 흐름(/tap → /?tap=…)으로 돌아온 화면에서는 띄우지 않는다
  const q = new URLSearchParams(window.location.search);
  if (q.has("tap") || q.has("card_t")) return true;
  try {
    return sessionStorage.getItem(SEEN_KEY) === "1";
  } catch {
    return false;
  }
}

function markSeen() {
  try {
    sessionStorage.setItem(SEEN_KEY, "1");
  } catch {
    // 저장이 막혀 있으면 다음 접속에 한 번 더 보일 뿐
  }
}

export default function Splash() {
  const [phase, setPhase] = useState<"show" | "fade" | "done">(() => (alreadySeen() ? "done" : "show"));

  useEffect(() => {
    if (phase !== "show") return;
    markSeen();
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const t = setTimeout(() => setPhase("fade"), reduced ? 900 : SHOW_MS);
    return () => clearTimeout(t);
  }, [phase]);

  useEffect(() => {
    if (phase !== "fade") return;
    const t = setTimeout(() => setPhase("done"), FADE_MS);
    return () => clearTimeout(t);
  }, [phase]);

  if (phase === "done") return null;
  return (
    <div
      className={`splash${phase === "fade" ? " is-fading" : ""}`}
      role="presentation"
      onClick={() => setPhase("fade")}
    >
      <div className="splash-stage">
        <span className="splash-path" />
        <span className="splash-runner">
          <Bot />
        </span>
      </div>
      <h1 className="splash-title">ODIGO</h1>
      <p className="splash-sub">말 한마디면 주문부터 결제까지</p>
      <span className="splash-dots" aria-label="불러오는 중">
        <i />
        <i />
        <i />
      </span>
    </div>
  );
}

// 배달 봉투를 든 ODIGO 로봇 (그림 파일 없이 SVG로 그린다)
function Bot() {
  return (
    <svg className="splash-char" viewBox="0 0 100 100" aria-hidden="true">
      <rect x="14" y="18" width="72" height="66" rx="22" fill="#B9A7F5" />
      <rect x="24" y="30" width="52" height="34" rx="14" fill="#2B2350" />
      <circle cx="40" cy="47" r="5" fill="#7FE3FF" />
      <circle cx="60" cy="47" r="5" fill="#7FE3FF" />
      <path d="M42 56q8 5 16 0" stroke="#7FE3FF" strokeWidth="3" fill="none" strokeLinecap="round" />
      <rect x="47" y="6" width="6" height="12" rx="3" fill="#B9A7F5" />
      <circle cx="50" cy="6" r="5" fill="#FFC94D" />
      <g transform="translate(0 -6) rotate(-8 75 74)">
        <path d="M60 62h30l3 28H57z" fill="#E9B872" />
        <path d="M60 62l3-6h24l3 6z" fill="#D49A52" />
        <path d="M68 57q7-7 14 0" stroke="#B57A36" strokeWidth="2.5" fill="none" strokeLinecap="round" />
        <path d="M70.5 70v13M68.5 70v4q2 3 4 0v-4" stroke="#8A5A26" strokeWidth="1.8" fill="none" strokeLinecap="round" />
        <ellipse cx="77.5" cy="73" rx="2.6" ry="3.4" fill="#8A5A26" />
        <path d="M77.5 76v7" stroke="#8A5A26" strokeWidth="1.8" strokeLinecap="round" />
      </g>
      <circle cx="59" cy="70" r="6.5" fill="#9D88E8" stroke="#7F6AD6" strokeWidth="1.2" />
    </svg>
  );
}
