// 08 운영 기록 (관리자, 이슈 #46·#94) — 개요 → 살펴볼 주문 → 주문 상세. 읽기 전용.
//   /admin            운영 개요: 오늘 지표, 시간대별 주문, 살펴볼 주문, 실시간 주문 흐름, 순위 (O-17)
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
    <div className="admin ops">
      <header className="ops-band">
        <div className="ops-band-top">
          <div>
            <h1>ODIGO 운영 기록</h1>
            <p className="ops-band-sub">관리자 전용 · 읽기 전용 (승인은 사용자 본인만 할 수 있어요)</p>
          </div>
          <div className="ops-band-right">
            <div className="admin-bar">
              <input
                className="input"
                type="password"
                placeholder="관리자 토큰 (ADMIN_ACCESS_TOKEN)"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && apply()}
              />
              <button className="ops-apply" onClick={apply}>
                적용
              </button>
            </div>
            {token && (
              <span className="ops-live">
                <i />
                실시간 · 5초마다 새로고침
              </span>
            )}
          </div>
        </div>
        <nav className="ops-tabs">
          <NavLink to="/admin" end>
            운영 개요
          </NavLink>
          <NavLink to="/admin/runs">주문 목록</NavLink>
        </nav>
      </header>

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
