// 운영 개요 (O-17): 오늘 숫자 → 자주 나온 사유·멈춘 단계 → 조치할 주문
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import type { AdminActionRow } from "../../api/types";
import { Tag } from "../../components/ui";
import { actionText, krw, reasonText, STATE_BADGE, when } from "../../lib/format";

export default function Overview({ token }: { token: string }) {
  const navigate = useNavigate();
  const summary = useQuery({
    queryKey: ["admin-summary", token],
    queryFn: () => api.adminSummary(token),
    refetchInterval: 5000,
  });
  if (summary.error instanceof ApiError && summary.error.status === 403)
    return <p className="empty">관리자 토큰이 맞지 않아요.</p>;
  const s = summary.data;
  if (!s) return <p className="muted">불러오는 중…</p>;
  const c = s.counts;
  const cards = [
    { label: "오늘 시작", value: c.started, tone: "" },
    { label: "결제 완료", value: c.completed, tone: "ok" },
    { label: "결제 확인 중", value: c.payment_unknown, tone: c.payment_unknown ? "warn" : "" },
    { label: "진행 차단", value: c.blocked, tone: c.blocked ? "bad" : "" },
    { label: "처리 실패", value: c.failed, tone: c.failed ? "bad" : "" },
  ];

  return (
    <div className="admin-stack">
      <section className="stat-row" aria-label="오늘 주문">
        {cards.map((k) => (
          <div key={k.label} className={`stat ${k.tone}`}>
            <span className="stat-label">{k.label}</span>
            <b className="stat-value">{k.value}</b>
          </div>
        ))}
      </section>

      <section className="panel">
        <h2>
          조치할 주문 {s.action_count > 0 && <span className="count">{s.action_count}</span>}
        </h2>
        {s.actions.length === 0 ? (
          <p className="muted">지금 개입할 주문이 없어요.</p>
        ) : (
          <ActionTable rows={s.actions} onOpen={(id) => navigate(`/admin/runs/${id}`)} />
        )}
        <p className="faint small" style={{ marginTop: 8 }}>
          기준: 결제 확인 중 · 사용자 차례가 아닌 단계에서 {s.stuck_minutes}분 넘게 멈춤 · 오늘 실패·차단
        </p>
      </section>

      <div className="admin-two">
        <section className="panel">
          <h2>오늘 자주 나온 실패·차단 사유</h2>
          {s.top_reasons.length === 0 ? (
            <p className="muted">없어요.</p>
          ) : (
            <ul className="bar-list">
              {s.top_reasons.map((r) => (
                <li key={r.code}>
                  <span>{reasonText(r.code)}</span>
                  <i style={{ width: `${(r.count / s.top_reasons[0].count) * 100}%` }} />
                  <b>{r.count}건</b>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="panel">
          <h2>지금 진행 중인 주문의 단계</h2>
          {s.by_step.length === 0 ? (
            <p className="muted">진행 중인 주문이 없어요.</p>
          ) : (
            <ul className="bar-list">
              {s.by_step.map((b) => (
                <li key={b.state}>
                  <span>{b.label}</span>
                  <i style={{ width: `${(b.count / Math.max(...s.by_step.map((x) => x.count))) * 100}%` }} />
                  <b>{b.count}건</b>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function ActionTable({ rows, onOpen }: { rows: AdminActionRow[]; onOpen: (id: string) => void }) {
  return (
    <table className="data">
      <thead>
        <tr>
          <th>조치 이유</th>
          <th>시각</th>
          <th>가게</th>
          <th>상태</th>
          <th className="num">금액</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.request_id} className="clickable" onClick={() => onOpen(r.request_id)}>
            <td>
              <Tag tone={r.action === "PAYMENT_UNKNOWN" || r.action === "STUCK" ? "warn" : "bad"}>
                {actionText(r.action, r.stuck_minutes)}
              </Tag>
              {r.reason_codes[0] && <span className="faint small"> {reasonText(r.reason_codes[0])}</span>}
            </td>
            <td>{when(r.updated_at)}</td>
            <td className="ellipsis">{r.restaurant_name ?? "—"}</td>
            <td>
              <Tag tone={STATE_BADGE[r.state].tone}>{STATE_BADGE[r.state].label}</Tag>
            </td>
            <td className="num">{krw(r.charge_krw)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
