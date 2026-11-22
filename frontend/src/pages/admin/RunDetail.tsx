// 주문 상세 (O-18): 위에 요약, 아래 탭 — 진행 타임라인(O-04) · 감사 로그(O-12) · 실시간 확인(O-19) · 요기요 장바구니(O-20)
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import type { RunEvent } from "../../api/types";
import { Tag } from "../../components/ui";
import { clock, EVENT_LABEL, gap, krw, PAYMENT_STATUS, reasonText, REPLY_LABEL, STATE_BADGE, stateText, when } from "../../lib/format";

type TabKey = "timeline" | "audit" | "live" | "cart";
const TABS: { key: TabKey; label: string }[] = [
  { key: "timeline", label: "진행 타임라인" },
  { key: "audit", label: "감사 로그" },
  { key: "live", label: "실시간 확인" },
  { key: "cart", label: "요기요 장바구니" },
];

function time(iso: string) {
  const d = new Date(iso);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
}

function describe(e: RunEvent): string {
  const d = e.detail;
  const str = (v: unknown) => (typeof v === "string" ? v : "");
  if (e.event_type === "STATE_CHANGED") return `${e.from_state ? `${stateText(e.from_state)} → ` : ""}${stateText(e.to_state)}`;
  if (e.event_type === "USER_REPLY") return REPLY_LABEL[str(d.type)] ?? str(d.type);
  if (e.event_type === "PAYMENT_RESULT")
    return `${PAYMENT_STATUS[str(d.status)] ?? str(d.status)}${e.sync_b08 ? ` · B-08 전달 ${e.sync_b08 === "DONE" ? "완료" : "대기"}` : ""}`;
  if (e.event_type === "SERVICE_ERROR") return `${e.service ?? ""} ${str(d.api)} 오류 ${reasonText(str(d.code))}`.trim();
  if (e.event_type === "SERVICE_CALL") return `${e.service ?? ""} ${str(d.api)}`.trim();
  if (e.event_type === "LIVE_CHECKED") return `${str(d.status)}${d.reason ? ` · ${str(d.reason)}` : ""}`;
  return "";
}

export default function RunDetail({ token }: { token: string }) {
  const { requestId = "" } = useParams();
  const [tab, setTab] = useState<TabKey>("timeline");
  const detail = useQuery({
    queryKey: ["admin-detail", token, requestId],
    queryFn: () => api.adminRunDetail(token, requestId),
    refetchInterval: 5000,
  });
  if (detail.error instanceof ApiError)
    return (
      <p className="empty">
        {detail.error.status === 404 ? "주문을 찾을 수 없어요." : detail.error.message} <Link to="/admin/runs">목록으로</Link>
      </p>
    );
  const d = detail.data;
  if (!d) return <p className="muted">불러오는 중…</p>;
  const reasons = d.reason_codes.map(reasonText);

  return (
    <div className="admin-stack">
      <Link to="/admin/runs" className="link-btn">
        ← 주문 목록
      </Link>
      <section className="panel detail-head">
        <div className="title-row">
          <b className="detail-title">{d.selected?.restaurant_name ?? "가게 선택 전"}</b>
          <Tag tone={STATE_BADGE[d.state].tone}>{STATE_BADGE[d.state].label}</Tag>
        </div>
        <p className="detail-msg">“{d.message}”</p>
        <dl className="kv">
          <dt>주문 번호</dt>
          <dd className="mono">{d.request_id}</dd>
          <dt>사용자</dt>
          <dd>{d.user_id}</dd>
          <dt>시작 · 마지막 변경</dt>
          <dd>
            {when(d.created_at)} · {when(d.updated_at)}
          </dd>
          <dt>메뉴</dt>
          <dd>{d.selected?.items.length ? d.selected.items.map((i) => `${i.name} ${i.quantity}`).join(", ") : `후보 ${d.candidate_count}곳 중 선택 전`}</dd>
          <dt>금액</dt>
          <dd>
            {krw(d.payment?.charge_krw ?? d.charge_krw)}
            {d.payment?.payment_method_id && <span className="faint"> · {d.payment.payment_method_id}</span>}
          </dd>
          <dt>결제</dt>
          <dd>{d.payment?.payment_status ? (PAYMENT_STATUS[d.payment.payment_status] ?? d.payment.payment_status) : "—"}</dd>
          {reasons.length > 0 && (
            <>
              <dt>실패·차단 사유</dt>
              <dd className="bad-text">{reasons.join(", ")}</dd>
            </>
          )}
          {d.error?.message && (
            <>
              <dt>안내 문구</dt>
              <dd>{d.error.message}</dd>
            </>
          )}
        </dl>
      </section>

      <nav className="tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            className={tab === t.key ? "on" : ""}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {tab === "timeline" && <Timeline token={token} requestId={requestId} />}
      {tab === "audit" && <Audit token={token} requestId={requestId} />}
      {tab === "live" && <Live token={token} requestId={requestId} />}
      {tab === "cart" && <Cart token={token} requestId={requestId} />}
    </div>
  );
}

function Timeline({ token, requestId }: { token: string; requestId: string }) {
  const events = useQuery({
    queryKey: ["admin-events", token, requestId],
    queryFn: () => api.runEvents(token, requestId),
    refetchInterval: 5000,
  });
  const [showCalls, setShowCalls] = useState(false);
  const all = events.data?.events ?? [];
  // 서비스 호출(A-01, B-05 …)은 많아서 기본으로 접는다. 오류는 항상 보인다
  const list = showCalls ? all : all.filter((e) => e.event_type !== "SERVICE_CALL");
  // 단계별 머문 시간: 단계 이동 사이 간격 (마지막 단계는 마지막 기록까지)
  const moves = all.filter((e) => e.event_type === "STATE_CHANGED" && e.to_state);
  const last = all.at(-1);
  const stays = moves.map((e, i) => ({
    state: e.to_state as string,
    took: gap(e.created_at, (moves[i + 1] ?? last ?? e).created_at),
  }));

  return (
    <section className="panel">
      {stays.length > 0 && (
        <div className="stay-row">
          {stays.map((s, i) => (
            <span key={i} className="stay">
              {stateText(s.state)} <b>{s.took}</b>
            </span>
          ))}
        </div>
      )}
      <label className="small muted check-line">
        <input type="checkbox" checked={showCalls} onChange={(e) => setShowCalls(e.target.checked)} /> 서비스 호출(
        {all.filter((e) => e.event_type === "SERVICE_CALL").length}건)도 보기
      </label>
      <table className="data">
        <thead>
          <tr>
            <th>시각</th>
            <th>종류</th>
            <th>내용</th>
            <th className="num">이전부터</th>
            <th className="num">호출 시간</th>
          </tr>
        </thead>
        <tbody>
          {list.map((e, i) => (
            <tr key={e.seq} className={e.event_type === "SERVICE_ERROR" ? "row-bad" : ""}>
              <td>{time(e.created_at)}</td>
              <td>{EVENT_LABEL[e.event_type] ?? e.event_type}</td>
              <td className="ellipsis">{describe(e)}</td>
              <td className="num">{i > 0 ? gap(list[i - 1].created_at, e.created_at) : ""}</td>
              <td className="num">{typeof e.detail.duration_ms === "number" ? `${e.detail.duration_ms}ms` : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {events.data && list.length === 0 && <p className="muted">기록이 없어요.</p>}
    </section>
  );
}

function Audit({ token, requestId }: { token: string; requestId: string }) {
  const audits = useQuery({
    queryKey: ["admin-audit", token, requestId],
    queryFn: () => api.auditLogs(token, requestId),
  });
  const rows = audits.data?.results ?? [];
  return (
    <section className="panel">
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
          {rows.map((a) => (
            <tr key={a.log_id}>
              <td>{time(a.created_at)}</td>
              <td className="mono">{a.event_type}</td>
              <td>{a.result}</td>
              <td className="ellipsis">{a.reason_codes.length ? a.reason_codes.map(reasonText).join(", ") : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {audits.data && rows.length === 0 && <p className="muted">감사 로그가 없어요.</p>}
    </section>
  );
}

const LIVE_TEXT: Record<string, string> = {
  OK: "주문 가능",
  CHANGED: "가격·정보 바뀜",
  UNAVAILABLE: "주문 불가",
  ERROR: "확인 실패",
  BLOCKED: "요기요가 막음",
  CHECKING: "확인 중",
  SKIPPED: "확인 안 함",
  NOT_CHECKED: "확인 전",
};
const LIVE_GRAY = ["CHECKING", "SKIPPED", "NOT_CHECKED"];

function Live({ token, requestId }: { token: string; requestId: string }) {
  const live = useQuery({
    queryKey: ["admin-live", token, requestId],
    queryFn: () => api.adminLiveCheck(token, requestId),
  });
  const v = live.data;
  if (!v) return <p className="muted">불러오는 중…</p>;
  if (v.state === "OFF" || v.state === "NONE")
    return <p className="muted panel">실시간 확인 기록이 없어요 ({v.mode === "off" ? "꺼져 있음" : "후보 선택 전"}).</p>;
  return (
    <section className="panel">
      <p className="muted small" style={{ marginTop: 0 }}>
        {v.mode} 모드 · {v.done}/{v.total}곳 확인{v.all_unavailable ? " · 모두 주문 불가" : ""}
      </p>
      <table className="data">
        <thead>
          <tr>
            <th>순위</th>
            <th>가게 ID</th>
            <th>결과</th>
            <th>내용</th>
            <th>확인 시각</th>
          </tr>
        </thead>
        <tbody>
          {v.results.map((r) => (
            <tr key={r.candidate_index}>
              <td>{r.candidate_index + 1}</td>
              <td className="mono">{r.restaurant_id}</td>
              <td>
                <Tag tone={r.status === "OK" ? "ok" : r.status === "CHANGED" ? "warn" : LIVE_GRAY.includes(r.status) ? "gray" : "bad"}>
                  {LIVE_TEXT[r.status] ?? r.status}
                </Tag>
              </td>
              <td className="ellipsis">{r.reason ?? (r.changes.join(" · ") || "—")}</td>
              <td>{r.checked_at ? clock(r.checked_at) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Cart({ token, requestId }: { token: string; requestId: string }) {
  const cart = useQuery({
    queryKey: ["admin-cart", token, requestId],
    queryFn: () => api.adminYogiyoCart(token, requestId),
  });
  const c = cart.data;
  if (!c) return <p className="muted">불러오는 중…</p>;
  if (c.status === "NONE") return <p className="muted panel">요기요 장바구니에 담은 기록이 없어요.</p>;
  return (
    <section className="panel">
      <dl className="kv">
        <dt>상태</dt>
        <dd>
          {c.status}
          {c.mode ? ` (${c.mode})` : ""}
        </dd>
        <dt>담은 메뉴</dt>
        <dd>{c.cart_items?.length ? c.cart_items.map((i) => `${i.name} ${i.quantity}`).join(", ") : "—"}</dd>
        <dt>메뉴 금액 · 배달비</dt>
        <dd>
          {krw(c.menu_subtotal_krw)} · {krw(c.delivery_fee_krw)}
        </dd>
        {!!c.mismatches?.length && (
          <>
            <dt>견적과 다른 점</dt>
            <dd className="warn-text">{c.mismatches.join(" · ")}</dd>
          </>
        )}
        {c.error && (
          <>
            <dt>오류</dt>
            <dd className="bad-text">{c.error}</dd>
          </>
        )}
      </dl>
      {c.screenshot && <img className="cart-shot" src={c.screenshot} alt="요기요 장바구니 화면" />}
    </section>
  );
}
