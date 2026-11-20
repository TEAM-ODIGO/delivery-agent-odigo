// 08 운영 기록 조회 (관리자) — O-11 목록 → O-04 단계 이력 + O-12 D 감사 로그. 읽기 전용.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, getAdminToken, setAdminToken } from "../api/client";
import type { RunEvent, RunState } from "../api/types";
import { Tag } from "../components/ui";
import { clock, krw, STATE_BADGE } from "../lib/format";

const FILTERS: { label: string; state?: RunState }[] = [
  { label: "전체" },
  { label: "승인 대기", state: "AWAITING_APPROVAL" },
  { label: "결제 확인 중", state: "PAYMENT_UNKNOWN" },
  { label: "결제 완료", state: "COMPLETED" },
  { label: "차단", state: "BLOCKED" },
  { label: "실패", state: "FAILED" },
];

function time(iso: string) {
  const d = new Date(iso);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
}

function describe(e: RunEvent): string {
  const d = e.detail;
  if (e.event_type === "USER_REPLY") return `사용자 응답 ${typeof d.type === "string" ? d.type : ""}`.trim();
  if (e.event_type === "STATE_CHANGED") return `${e.from_state ? `${e.from_state} → ` : "→ "}${e.to_state}`;
  if (e.event_type === "PAYMENT_RESULT") return `결제 결과 ${typeof d.status === "string" ? d.status : ""}${e.sync_b08 ? ` · B-08 ${e.sync_b08}` : ""}`;
  if (e.event_type === "SERVICE_ERROR") return `오류 ${typeof d.code === "string" ? d.code : ""}`;
  return "";
}

export default function Admin() {
  const [token, setToken] = useState(getAdminToken());
  const [draft, setDraft] = useState(token);
  const [filter, setFilter] = useState<RunState | undefined>();
  const [selected, setSelected] = useState<string | null>(null);

  const runs = useQuery({
    queryKey: ["admin-runs", token, filter],
    queryFn: () => api.adminRuns(token, filter),
    enabled: !!token,
    refetchInterval: 5000,
  });
  const events = useQuery({
    queryKey: ["admin-events", token, selected],
    queryFn: () => api.runEvents(token, selected!),
    enabled: !!token && !!selected,
  });
  const audits = useQuery({
    queryKey: ["admin-audit", token, selected],
    queryFn: () => api.auditLogs(token, selected!),
    enabled: !!token && !!selected,
  });
  const forbidden = runs.error instanceof ApiError && runs.error.status === 403;

  return (
    <div className="admin">
      <h1>운영 기록 조회</h1>
      <p className="muted" style={{ margin: 0 }}>
        관리자 전용 · 읽기 전용 (승인은 사용자 본인만 할 수 있어요)
      </p>

      <div className="admin-bar" style={{ marginTop: 16 }}>
        <input
          className="input"
          type="password"
          placeholder="관리자 토큰 (ADMIN_ACCESS_TOKEN)"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (setAdminToken(draft), setToken(draft))}
        />
        <button className="chip on" onClick={() => (setAdminToken(draft), setToken(draft))}>
          적용
        </button>
        <span style={{ width: 16 }} />
        {FILTERS.map((f) => (
          <button
            key={f.label}
            className={`chip${filter === f.state ? " on" : ""}`}
            onClick={() => {
              setFilter(f.state);
              setSelected(null);
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {!token && <p className="empty">관리자 토큰을 넣어 주세요.</p>}
      {forbidden && <p className="empty">관리자 토큰이 맞지 않아요.</p>}

      {token && !forbidden && (
        <div className="admin-grid">
          <section className="panel">
            <h2>주문 목록 (O-11){runs.data ? ` · ${runs.data.count}건` : ""}</h2>
            <table className="data">
              <thead>
                <tr>
                  <th>시각</th>
                  <th>사용자</th>
                  <th>가게</th>
                  <th>상태</th>
                  <th>금액</th>
                  <th>사유</th>
                </tr>
              </thead>
              <tbody>
                {runs.data?.results.map((r) => (
                  <tr
                    key={r.request_id}
                    className={`clickable${selected === r.request_id ? " sel" : ""}`}
                    onClick={() => setSelected(r.request_id)}
                  >
                    <td>{clock(r.created_at)}</td>
                    <td>{r.user_id}</td>
                    <td>{r.restaurant_name ?? "—"}</td>
                    <td>
                      <Tag tone={STATE_BADGE[r.state].tone}>{STATE_BADGE[r.state].label}</Tag>
                    </td>
                    <td>{r.charge_krw ? krw(r.charge_krw) : "—"}</td>
                    <td className="mono">{r.reason_codes?.join(", ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {runs.data?.results.length === 0 && <p className="empty">주문이 없어요.</p>}
          </section>

          <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
            <section className="panel">
              <h2>O 단계 이력 (O-04){selected ? ` · ${selected}` : ""}</h2>
              {!selected && <p className="muted">왼쪽에서 주문을 고르세요.</p>}
              {events.data && (
                <table className="data">
                  <thead>
                    <tr>
                      <th>시각</th>
                      <th>종류</th>
                      <th>변화 / API</th>
                      <th>ms</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.data.events.map((e) => (
                      <tr key={e.seq}>
                        <td>{time(e.created_at)}</td>
                        <td className="mono">{e.event_type}</td>
                        <td>
                          {describe(e)}
                          {e.service && (
                            <span className="faint">
                              {" "}
                              {e.service}
                              {typeof e.detail.api === "string" ? ` · ${e.detail.api}` : ""}
                            </span>
                          )}
                        </td>
                        <td>{typeof e.detail.duration_ms === "number" ? e.detail.duration_ms : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
            <section className="panel">
              <h2>D 감사 로그 (D-09)</h2>
              {audits.data && (
                <table className="data">
                  <thead>
                    <tr>
                      <th>시각</th>
                      <th>이벤트</th>
                      <th>결과</th>
                      <th>사유</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audits.data.results.map((a) => (
                      <tr key={a.log_id}>
                        <td>{time(a.created_at)}</td>
                        <td className="mono">{a.event_type}</td>
                        <td>{a.result}</td>
                        <td className="mono">{a.reason_codes.join(", ") || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {selected && audits.data?.results.length === 0 && <p className="muted">감사 로그가 없어요.</p>}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}
