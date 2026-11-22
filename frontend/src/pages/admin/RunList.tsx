// 주문 목록 (O-11): 상태 필터, 열은 시각·가게·상태·금액·사유만. 행을 누르면 상세
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import type { RunState } from "../../api/types";
import { Tag } from "../../components/ui";
import { krw, reasonText, STATE_BADGE, when } from "../../lib/format";

const FILTERS: { label: string; state?: RunState }[] = [
  { label: "전체" },
  { label: "승인 대기", state: "AWAITING_APPROVAL" },
  { label: "결제 확인 중", state: "PAYMENT_UNKNOWN" },
  { label: "결제 완료", state: "COMPLETED" },
  { label: "차단", state: "BLOCKED" },
  { label: "실패", state: "FAILED" },
];

export default function RunList({ token }: { token: string }) {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<RunState | undefined>();
  const runs = useQuery({
    queryKey: ["admin-runs", token, filter],
    queryFn: () => api.adminRuns(token, filter),
    refetchInterval: 5000,
  });
  if (runs.error instanceof ApiError && runs.error.status === 403)
    return <p className="empty">관리자 토큰이 맞지 않아요.</p>;

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>주문 목록{runs.data ? ` · ${runs.data.count}건` : ""}</h2>
        <div className="chips">
          {FILTERS.map((f) => (
            <button
              key={f.label}
              className={`chip${filter === f.state ? " on" : ""}`}
              onClick={() => setFilter(f.state)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>
      <table className="data">
        <thead>
          <tr>
            <th>시각</th>
            <th>가게</th>
            <th>상태</th>
            <th className="num">금액</th>
            <th>사유</th>
          </tr>
        </thead>
        <tbody>
          {runs.data?.results.map((r) => (
            <tr key={r.request_id} className="clickable" onClick={() => navigate(`/admin/runs/${r.request_id}`)}>
              <td>{when(r.created_at)}</td>
              <td className="ellipsis">{r.restaurant_name ?? "—"}</td>
              <td>
                <Tag tone={STATE_BADGE[r.state].tone}>{STATE_BADGE[r.state].label}</Tag>
              </td>
              <td className="num">{krw(r.charge_krw)}</td>
              <td className="ellipsis">{r.reason_codes?.length ? r.reason_codes.map(reasonText).join(", ") : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {runs.data?.results.length === 0 && <p className="empty">주문이 없어요.</p>}
    </section>
  );
}
