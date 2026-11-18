// 07 내 주문 목록 — O-09
import { Link, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, DEV_USERS, getUserToken, setUserToken } from "../api/client";
import { Button, Screen, Spinner, Tag } from "../components/ui";
import { krw, STATE_BADGE, when } from "../lib/format";

export default function MyRuns() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const token = getUserToken();
  const runs = useQuery({ queryKey: ["my-runs", token], queryFn: () => api.myRuns(30), refetchInterval: 5000 });

  return (
    <Screen
      title="내 주문"
      sub="최근 주문 순"
      back={() => nav("/")}
      footer={<Button onClick={() => nav("/")}>새 주문</Button>}
    >
      {runs.isLoading && <Spinner label="불러오는 중…" />}
      {runs.isError && <p className="empty">주문 목록을 불러오지 못했어요.</p>}
      {runs.data?.results.length === 0 && <p className="empty">아직 주문이 없어요.</p>}
      {runs.data?.results.map((r) => {
        const badge = STATE_BADGE[r.state];
        return (
          <Link key={r.request_id} to={`/runs/${r.request_id}`} className="card" style={{ textDecoration: "none", color: "inherit" }}>
            <div className="title-row">
              <b>{r.restaurant_name ?? "—"}</b>
              <span className="price" style={{ fontSize: 15 }}>
                {r.charge_krw ? krw(r.charge_krw) : "—"}
              </span>
            </div>
            <span className="muted small">{r.message}</span>
            <div className="title-row">
              <Tag tone={badge.tone}>{badge.label}</Tag>
              <span className="faint">{when(r.created_at)}</span>
            </div>
          </Link>
        );
      })}
      <div className="user-switch">
        테스트 사용자
        <select
          value={token}
          onChange={(e) => {
            setUserToken(e.target.value);
            qc.invalidateQueries();
            nav(0);
          }}
        >
          {DEV_USERS.map((u) => (
            <option key={u.token} value={u.token}>
              {u.userId}
            </option>
          ))}
        </select>
      </div>
    </Screen>
  );
}
