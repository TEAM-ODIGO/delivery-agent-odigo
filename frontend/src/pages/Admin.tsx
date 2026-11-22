// 08 운영 기록 (관리자, 이슈 #46) — 개요 → 조치할 주문 → 주문 상세. 읽기 전용.
//   /admin            운영 개요: 오늘 숫자, 자주 나온 사유, 조치할 주문 (O-17)
//   /admin/runs       주문 목록: 상태 필터 (O-11)
//   /admin/runs/:id   주문 상세: 요약(O-18) + 탭(진행 타임라인 O-04 · 감사 로그 O-12 · 실시간 확인 O-19 · 요기요 장바구니 O-20)
import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { getAdminToken, setAdminToken } from "../api/client";
import Overview from "./admin/Overview";
import RunList from "./admin/RunList";
import RunDetail from "./admin/RunDetail";

export default function Admin() {
  const [token, setToken] = useState(getAdminToken());
  const [draft, setDraft] = useState(token);
  const apply = () => {
    setAdminToken(draft);
    setToken(draft);
  };

  return (
    <div className="admin">
      <header className="admin-head">
        <div>
          <h1>운영 기록</h1>
          <p className="muted" style={{ margin: 0 }}>
            관리자 전용 · 읽기 전용 (승인은 사용자 본인만 할 수 있어요)
          </p>
        </div>
        <div className="admin-bar">
          <input
            className="input"
            type="password"
            placeholder="관리자 토큰 (ADMIN_ACCESS_TOKEN)"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && apply()}
          />
          <button className="chip on" onClick={apply}>
            적용
          </button>
        </div>
      </header>

      <nav className="admin-nav">
        <NavLink to="/admin" end>
          운영 개요
        </NavLink>
        <NavLink to="/admin/runs">주문 목록</NavLink>
      </nav>

      {!token ? (
        <p className="empty">관리자 토큰을 넣어 주세요.</p>
      ) : (
        <Routes>
          <Route index element={<Overview token={token} />} />
          <Route path="runs" element={<RunList token={token} />} />
          <Route path="runs/:requestId" element={<RunDetail token={token} />} />
        </Routes>
      )}
    </div>
  );
}
