// 고객 화면 공통 상단 바 (이슈 #66): 보라색 바, 가운데 로고 + ODIGO(흰 글씨), 오른쪽 ☰ 메뉴.
// 고객이 주문할 때 알 필요 없는 설정(결제 한도·자동 주문 범위)은 메뉴 안에 둔다.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { DEMO_TAGS } from "../lib/format";

// 새 주문(처음 화면)은 상단 바의 로고·ODIGO를 누르면 간다
const MENU = [
  { to: "/settings", label: "내 카드·주문 설정" },
  { to: "/orders", label: "주문 내역" },
  // '내 카드 혜택'은 설정 화면 카드 목록(⋮)과 겹쳐 메뉴에서 뺐다 (이슈 #72). /card 페이지는 #83에서 없앴다
];

export default function TopBar() {
  const [open, setOpen] = useState(false);
  const [testOpen, setTestOpen] = useState(false);
  const nav = useNavigate();
  // 테스트 시나리오(#품절 등)는 이 서버 구성에서 동작할 때만 메뉴 맨 아래에 보인다 (이슈 #101)
  const demoTags = useQuery({ queryKey: ["demo-tags"], queryFn: api.demoTags, staleTime: 60_000 });
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <div className="topbar">
        <span className="topbar-side" />
        <button className="topbar-title" onClick={() => nav("/")} aria-label="ODIGO 처음 화면">
          <img src="/images/odigo-logo.webp" alt="" />
          ODIGO
        </button>
        <button className="topbar-menu" onClick={() => setOpen(true)} aria-label="메뉴 열기" aria-expanded={open}>
          <i />
          <i />
          <i />
        </button>
      </div>
      {open && (
        <div className="drawer-backdrop" onClick={() => setOpen(false)}>
          <nav className="drawer" aria-label="메뉴" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-head">
              <b className="drawer-title">무엇을 도와드릴까요?</b>
              <button className="drawer-close" onClick={() => setOpen(false)} aria-label="메뉴 닫기">
                ×
              </button>
            </div>
            {MENU.map((m) => (
              <button
                key={m.to}
                className="drawer-item"
                onClick={() => {
                  setOpen(false);
                  nav(m.to);
                }}
              >
                {m.label} <span className="faint">›</span>
              </button>
            ))}
            <div className="drawer-foot">
              {demoTags.data?.available && (
                <>
                  <button className="drawer-test" onClick={() => setTestOpen((v) => !v)} aria-expanded={testOpen}>
                    테스트 시나리오 <span>{testOpen ? "−" : "+"}</span>
                  </button>
                  {testOpen && (
                    <div className="drawer-tags">
                      {DEMO_TAGS.map((t) => (
                        <button
                          key={t.tag}
                          className="chip"
                          onClick={() => {
                            setOpen(false);
                            nav("/", { state: { appendTag: t.tag } });
                          }}
                        >
                          {t.tag} <span className="faint">&nbsp;{t.desc}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
              <p className="drawer-note">실제 주문·결제는 이루어지지 않는 체험 서비스예요.</p>
            </div>
          </nav>
        </div>
      )}
    </>
  );
}
