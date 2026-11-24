// 운영 개요 (O-17, 이슈 #94): 오늘 지표 띠 → 시간대별 주문 + 지금 현황·연결 상태 → 운영 브리핑
//   → 살펴볼 주문 + 실시간 주문 흐름 → 오늘의 순위. 5초마다 새로고침.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import type { AdminActionRow, AdminFeedEvent, AdminMetrics, AdminService, AdminSummary } from "../../api/types";
import { Tag } from "../../components/ui";
import { actionLevel, actionText, clock, krw, reasonText, STATE_BADGE } from "../../lib/format";

const REFRESH_MS = 5000;

export default function Overview({ token }: { token: string }) {
  const navigate = useNavigate();
  const summary = useQuery({
    queryKey: ["admin-summary", token],
    queryFn: () => api.adminSummary(token),
    refetchInterval: REFRESH_MS,
  });
  const feed = useQuery({
    queryKey: ["admin-events", token],
    queryFn: () => api.adminEvents(token),
    refetchInterval: REFRESH_MS,
  });
  if (summary.error instanceof ApiError && summary.error.status === 403)
    return <p className="empty">관리자 토큰이 맞지 않아요.</p>;
  const s = summary.data;
  if (!s) return <p className="muted">불러오는 중…</p>;
  const open = (id: string) => navigate(`/admin/runs/${id}`);

  return (
    <div className="admin-stack">
      <Ticker s={s} />
      <div className="ops-main">
        <section className="panel">
          <div className="panel-head">
            <h2>
              시간대별 주문 <span className="faint small">오늘 {s.counts.started}건</span>
            </h2>
            <span className="ops-legend">
              <span>
                <i style={{ background: "var(--pri)" }} />
                오늘
              </span>
              <span>
                <i style={{ background: "#b4b8c4" }} />
                어제
              </span>
            </span>
          </div>
          <HourlyChart today={s.hourly.today} yesterday={s.hourly.yesterday} />
        </section>
        <section className="panel">
          <h2>지금 주문 현황</h2>
          <Now s={s} />
          <h2 style={{ marginTop: 18 }}>
            연결 상태 <span className="faint small">마지막으로 본 시각</span>
          </h2>
          <Services list={s.services} />
        </section>
      </div>

      <Briefing s={s} />

      <div className="ops-main">
        <ActionPanel s={s} onOpen={open} />
        <section className="panel">
          <h2>
            실시간 주문 흐름 <span className="faint small">최근 일부터</span>
          </h2>
          <Feed events={feed.data?.events} onOpen={open} />
        </section>
      </div>

      <Rankings s={s} />
    </div>
  );
}

// ---------------------------------------------------------------- 지표 띠
type Better = "up" | "down" | "none";

function Ticker({ s }: { s: AdminSummary }) {
  const t = s.metrics.today;
  const y = s.metrics.yesterday;
  const cum = s.hourly.today.reduce<number[]>((a, n) => [...a, (a[a.length - 1] ?? 0) + n], []);
  const nowHour = new Date().getHours();
  const items: { label: string; value: string; diff: number | null; unit: string; better: Better; sub?: string; spark?: number[]; tone?: "ok" | "warn" | "bad" }[] = [
    { label: "오늘 주문", value: `${t.started}건`, diff: t.started - y.started, unit: "건", better: "up", sub: "어제 이 시각보다", spark: cum.slice(0, nowHour + 1) },
    { label: "결제 완료율", value: pctText(t.completion_rate), diff: diffOf(t.completion_rate, y.completion_rate), unit: "%p", better: "up", sub: `${t.completed}건 완료`, tone: rateTone(t) },
    { label: "평균 결제 금액", value: krw(t.avg_charge_krw), diff: diffOf(t.avg_charge_krw, y.avg_charge_krw), unit: "원", better: "none", sub: "어제보다" },
    { label: "혜택으로 아낀 금액", value: krw(t.saved_krw), diff: t.saved_krw - y.saved_krw, unit: "원", better: "up", sub: t.completed ? `건당 ${krw(Math.round(t.saved_krw / t.completed))}` : "어제보다" },
    { label: "평균 주문 시간", value: secText(t.avg_order_seconds), diff: diffOf(t.avg_order_seconds, y.avg_order_seconds), unit: "초", better: "down", sub: "요청 → 결제" },
    { label: "살펴볼 주문", value: `${s.action_count}건`, diff: null, unit: "", better: "none", sub: s.action_count ? "아래 목록에서 확인" : "지금은 없어요", tone: s.action_count ? "bad" : "ok" },
  ];
  return (
    <section className="ops-ticker" aria-label="오늘 지표">
      {items.map((k) => (
        <div key={k.label} className="ops-tk">
          <span className="ops-tk-label">
            {k.label}
            {k.tone && <Tag tone={k.tone}>{k.tone === "ok" ? "정상" : k.tone === "warn" ? "지켜보기" : "확인 필요"}</Tag>}
          </span>
          <span className="ops-tk-row">
            <b className={`ops-tk-value ${k.label === "살펴볼 주문" && s.action_count ? "worse" : ""}`}>{k.value}</b>
            {k.spark && k.spark.length > 1 && <Spark values={k.spark} />}
          </span>
          <span className="ops-tk-sub">
            <Diff diff={k.diff} unit={k.unit} better={k.better} /> <span className="faint">{k.sub}</span>
          </span>
        </div>
      ))}
    </section>
  );
}

const diffOf = (a: number | null, b: number | null) => (a === null || b === null ? null : a - b);
const pctText = (n: number | null) => (n === null ? "—" : `${n}%`);
function secText(sec: number | null): string {
  if (sec === null) return "—";
  const m = Math.floor(sec / 60);
  return m ? `${m}분 ${sec % 60}초` : `${sec}초`;
}
function rateTone(m: AdminMetrics): "ok" | "warn" | "bad" | undefined {
  if (m.completion_rate === null) return undefined;
  return m.completion_rate >= 50 ? "ok" : m.completion_rate >= 30 ? "warn" : "bad";
}

function Diff({ diff, unit, better }: { diff: number | null; unit: string; better: Better }) {
  if (diff === null || diff === 0) return <span className="faint">{diff === 0 ? "변화 없음 ·" : ""}</span>;
  const good = better === "none" ? null : (diff > 0) === (better === "up");
  const n = unit === "원" ? Math.abs(diff).toLocaleString() : Math.abs(diff);
  return (
    <span className={good === null ? "faint" : good ? "good" : "worse"}>
      {diff > 0 ? "▲" : "▼"} {n}
      {unit}
    </span>
  );
}

function Spark({ values }: { values: number[] }) {
  const max = Math.max(1, ...values);
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * 46).toFixed(1)},${(20 - (v / max) * 18).toFixed(1)}`).join(" ");
  return (
    <svg className="ops-spark" viewBox="0 0 46 22" aria-hidden="true">
      <polyline points={pts} fill="none" stroke="var(--pri)" strokeWidth="1.6" />
    </svg>
  );
}

// ---------------------------------------------------------------- 시간대별 그래프
function HourlyChart({ today, yesterday }: { today: number[]; yesterday: number[] }) {
  const nowHour = new Date().getHours();
  const first = [...today, ...yesterday].findIndex((n) => n > 0);
  const firstY = yesterday.findIndex((n) => n > 0);
  const start = Math.min(9, ...[first, firstY].filter((h) => h >= 0).map((h) => h % 24));
  const hours = Array.from({ length: 24 - start }, (_, i) => start + i);
  const W = 640, H = 220, PL = 30, PR = 12, PT = 22, PB = 26;
  const max = Math.max(2, ...today, ...yesterday);
  const top = Math.ceil(max / 2) * 2;
  const X = (i: number) => PL + (i * (W - PL - PR)) / (hours.length - 1);
  const Y = (v: number) => PT + (H - PT - PB) * (1 - v / top);
  const line = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const tv = hours.filter((h) => h <= nowHour).map((h) => today[h]);
  const yv = hours.map((h) => yesterday[h]);
  const peakI = tv.length ? tv.indexOf(Math.max(...tv)) : -1;
  const ticks = [0, top / 2, top];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="시간대별 주문 수 (오늘과 어제)">
      {ticks.map((v) => (
        <g key={v}>
          <line x1={PL} x2={W - PR} y1={Y(v)} y2={Y(v)} stroke="#eef0f4" />
          <text x={PL - 8} y={Y(v) + 4} textAnchor="end" fontSize="10" fill="#9ca3af">
            {v}
          </text>
        </g>
      ))}
      {hours.map((h, i) =>
        h % 2 === 1 ? (
          <text key={h} x={X(i)} y={H - 8} textAnchor="middle" fontSize="10" fill="#9ca3af">
            {h}시
          </text>
        ) : null,
      )}
      {tv.map((v, i) =>
        v ? <rect key={i} x={X(i) - 6} y={Y(v)} width="12" height={Y(0) - Y(v)} rx="2" fill="var(--pri)" opacity=".12" /> : null,
      )}
      <path d={line(yv)} fill="none" stroke="#b4b8c4" strokeWidth="2" strokeDasharray="4 4" />
      {tv.length > 1 && (
        <>
          <path d={`${line(tv)} L${X(tv.length - 1)},${Y(0)} L${X(0)},${Y(0)} Z`} fill="var(--pri)" opacity=".08" />
          <path d={line(tv)} fill="none" stroke="var(--pri)" strokeWidth="2.5" strokeLinejoin="round" />
          <circle cx={X(tv.length - 1)} cy={Y(tv[tv.length - 1])} r="5" fill="var(--pri)" stroke="#fff" strokeWidth="2" />
        </>
      )}
      {peakI >= 0 && tv[peakI] > 0 && (
        <g transform={`translate(${Math.min(Math.max(X(peakI), PL + 44), W - PR - 44)},${Y(tv[peakI]) - 10})`}>
          <rect x="-42" y="-18" width="84" height="20" rx="10" fill="#1e2a5a" />
          <text y="-4" textAnchor="middle" fontSize="11" fill="#fff" fontWeight="700">
            최고 {hours[peakI]}시 {tv[peakI]}건
          </text>
        </g>
      )}
    </svg>
  );
}

// ---------------------------------------------------------------- 지금 현황 · 연결 상태
function Now({ s }: { s: AdminSummary }) {
  const c = s.counts;
  const stopped = c.payment_unknown + c.blocked + c.failed;
  const parts = [
    { n: c.in_progress, color: "var(--pri)", label: "진행 중" },
    { n: c.completed, color: "var(--ok)", label: "완료" },
    { n: c.payment_unknown, color: "#e39a2d", label: "결제 확인 중" },
    { n: c.blocked + c.failed, color: "#d14343", label: "차단·실패" },
    { n: c.cancelled, color: "#c9cdd3", label: "취소" },
  ];
  const total = Math.max(1, parts.reduce((a, p) => a + p.n, 0));
  return (
    <>
      <div className="ops-now">
        <div>
          <span className="muted small">진행 중</span>
          <b style={{ color: "var(--pri)" }}>{c.in_progress}</b>
        </div>
        <div>
          <span className="muted small">결제 완료</span>
          <b className="good">{c.completed}</b>
        </div>
        <div>
          <span className="muted small">멈춤·실패</span>
          <b className={stopped ? "worse" : ""}>{stopped}</b>
        </div>
      </div>
      <div className="ops-split" aria-hidden="true">
        {parts.map((p) => (p.n ? <i key={p.label} style={{ width: `${(p.n / total) * 100}%`, background: p.color }} /> : null))}
      </div>
      <p className="faint small" style={{ margin: 0 }}>
        {parts.map((p) => `${p.label} ${p.n}`).join(" · ")}
      </p>
    </>
  );
}

const SERVICE_DOT: Record<AdminService["status"], string> = { OK: "var(--ok)", WARN: "#e39a2d", DOWN: "#d14343", IDLE: "#c9cdd3", OFF: "#c9cdd3" };

function Services({ list }: { list: AdminService[] }) {
  return (
    <div className="ops-svc">
      {list.map((x) => (
        <div key={x.key} className="ops-svc-row">
          <i className="ops-dot" style={{ background: SERVICE_DOT[x.status] }} />
          <span>
            {x.label}
            {x.mode && x.mode !== "real" && x.mode !== "off" && <span className="faint small"> · {x.mode === "remote" ? "PC 작업기" : "모의"}</span>}
          </span>
          <span className={`small ${x.status === "WARN" || x.status === "DOWN" ? "worse" : "faint"}`}>
            {x.note ?? (x.last_at ? ago(x.last_at) : "—")}
          </span>
        </div>
      ))}
    </div>
  );
}

function ago(iso: string): string {
  const sec = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (sec < 60) return `${sec}초 전`;
  if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}시간 전`;
  return `${Math.floor(sec / 86400)}일 전`;
}

// ---------------------------------------------------------------- 운영 브리핑 (규칙으로 만든 문장)
function Briefing({ s }: { s: AdminSummary }) {
  const t = s.metrics.today;
  const y = s.metrics.yesterday;
  const trend = t.started > y.started ? "어제 이 시각보다 늘었고" : t.started < y.started ? "어제 이 시각보다 줄었고" : "어제 이 시각과 비슷하고";
  const urgent = s.actions[0];
  const lines: string[] = [];
  const peak = Math.max(...s.hourly.today);
  if (peak > 0) lines.push(`오늘은 ${s.hourly.today.indexOf(peak)}시에 주문이 가장 많았어요 (${peak}건).`);
  if (s.top_reasons[0]) lines.push(`멈춘 사유는 '${reasonText(s.top_reasons[0].code)}'이(가) 가장 많아요 (${s.top_reasons[0].count}건).`);
  for (const x of s.services) if (x.status === "WARN" || x.status === "DOWN") lines.push(`${x.label}: ${x.note}`);
  if (t.saved_krw > 0) lines.push(`혜택으로 오늘 ${krw(t.saved_krw)}을 아꼈어요.`);
  return (
    <section className="ops-brief" aria-label="오늘의 운영 브리핑">
      <span className="ops-brief-label">오늘의 운영 브리핑</span>
      <div>
        <p className="ops-brief-head">
          {t.started ? `주문은 ${trend}, ` : "오늘은 아직 주문이 없고, "}
          {s.action_count ? (
            <>
              <em>살펴볼 주문 {s.action_count}건</em>이 있어요
              {urgent && ` (가장 급한 건 ${urgent.restaurant_name ?? "가게 선택 전"} · ${actionText(urgent.action, urgent.stuck_minutes)})`}.
            </>
          ) : (
            "지금 살펴볼 주문은 없어요."
          )}
        </p>
        {lines.length > 0 && (
          <ul>
            {lines.slice(0, 4).map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        )}
      </div>
      <span className="ops-brief-time">{clock(new Date().toISOString())} 기준</span>
    </section>
  );
}

// ---------------------------------------------------------------- 살펴볼 주문
type Level = "all" | "warn" | "bad";

function ActionPanel({ s, onOpen }: { s: AdminSummary; onOpen: (id: string) => void }) {
  const [level, setLevel] = useState<Level>("all");
  const warn = s.actions.filter((a) => actionLevel(a.action).tone === "warn");
  const rows = level === "all" ? s.actions : s.actions.filter((a) => actionLevel(a.action).tone === level);
  const filters: { key: Level; label: string }[] = [
    { key: "all", label: `전체 ${s.action_count}` },
    { key: "warn", label: `지켜보기 ${warn.length}` },
    { key: "bad", label: `확인 필요 ${s.actions.length - warn.length}` },
  ];
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>살펴볼 주문 {s.action_count > 0 && <span className="count">{s.action_count}</span>}</h2>
        <div className="ops-pills" role="group" aria-label="살펴볼 주문 거르기">
          {filters.map((f) => (
            <button key={f.key} className={level === f.key ? "on" : ""} onClick={() => setLevel(f.key)}>
              {f.label}
            </button>
          ))}
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="muted">{s.action_count ? "이 구분에는 없어요." : "지금 살펴볼 주문이 없어요."}</p>
      ) : (
        <ActionTable rows={rows} onOpen={onOpen} />
      )}
      <p className="faint small" style={{ marginTop: 8, marginBottom: 0 }}>
        지켜보기: 결제 확인 중 · 사용자 차례가 아닌데 {s.stuck_minutes}분 넘게 멈춤 / 확인 필요: 오늘 차단·실패
      </p>
    </section>
  );
}

function ActionTable({ rows, onOpen }: { rows: AdminActionRow[]; onOpen: (id: string) => void }) {
  return (
    <table className="data">
      <thead>
        <tr>
          <th>확인할 내용</th>
          <th>시각</th>
          <th>가게</th>
          <th>상태</th>
          <th className="num">금액</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const lv = actionLevel(r.action);
          return (
            <tr key={r.request_id} className="clickable" onClick={() => onOpen(r.request_id)}>
              <td>
                <Tag tone={lv.tone}>{lv.label}</Tag> {actionText(r.action, r.stuck_minutes)}
                {r.reason_codes[0] && <span className="faint small"> · {reasonText(r.reason_codes[0])}</span>}
              </td>
              <td>{clock(r.updated_at)}</td>
              <td className="ellipsis">{r.restaurant_name ?? "—"}</td>
              <td>
                <Tag tone={STATE_BADGE[r.state].tone}>{STATE_BADGE[r.state].label}</Tag>
              </td>
              <td className="num">{krw(r.charge_krw)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------- 실시간 주문 흐름
const FEED: Record<AdminFeedEvent["kind"], { label: string; color: string }> = {
  STARTED: { label: "새 주문", color: "var(--pri)" },
  AWAITING_APPROVAL: { label: "승인 대기", color: "var(--pri)" },
  REQUOTED: { label: "요기요 금액으로 다시 계산", color: "var(--pri)" },
  COMPLETED: { label: "결제 완료", color: "var(--ok)" },
  PAYMENT_UNKNOWN: { label: "결제 확인 중", color: "#e39a2d" },
  BLOCKED: { label: "차단", color: "#d14343" },
  FAILED: { label: "실패", color: "#d14343" },
  CANCELLED: { label: "취소", color: "#9ca3af" },
};

function Feed({ events, onOpen }: { events?: AdminFeedEvent[]; onOpen: (id: string) => void }) {
  if (!events) return <p className="muted">불러오는 중…</p>;
  if (events.length === 0) return <p className="muted">오늘은 아직 기록이 없어요.</p>;
  return (
    <ul className="ops-feed">
      {events.map((e, i) => {
        const f = FEED[e.kind];
        return (
          <li key={`${e.request_id}-${e.at}-${i}`} onClick={() => onOpen(e.request_id)}>
            <span className="ops-feed-time">{clock(e.at)}</span>
            <i className="ops-dot" style={{ background: f.color }} />
            <span className="ops-feed-text">
              {e.kind === "STARTED" ? (
                <>
                  {f.label} <span className="muted">"{e.message}"</span>
                </>
              ) : (
                <>
                  {e.restaurant_name && `${e.restaurant_name} `}
                  <b>{f.label}</b>
                  {e.charge_krw ? ` ${krw(e.charge_krw)}` : ""}
                  {e.saved_krw ? <span className="faint"> · {krw(e.saved_krw)} 아낌</span> : null}
                  {e.reason_codes[0] && <span className="muted"> {reasonText(e.reason_codes[0])}</span>}
                  {e.changes?.map((c) => (
                    <span key={c.kind + c.name} className="muted">
                      {" "}
                      {c.name} {krw(c.from_krw)} → {krw(c.to_krw)}
                    </span>
                  ))}
                </>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------- 오늘의 순위
const BAR_COLORS = ["var(--pri)", "#5b6cd9", "var(--ok)", "#8e6fd8", "#e39a2d", "#2a9d8f"];
const REASON_COLORS = ["#e0605e", "#e39a2d", "#8e6fd8", "#5b6cd9", "#2a9d8f"];

function Rankings({ s }: { s: AdminSummary }) {
  const r = s.rankings;
  const paid = r.payment_methods.reduce((a, x) => a + x.count, 0);
  const maxReason = Math.max(1, ...s.top_reasons.map((x) => x.count));
  return (
    <section className="panel">
      <h2>
        오늘의 순위 <span className="faint small">오늘 주문 기준</span>
      </h2>
      <div className="ops-rank">
        <RankList title="많이 고른 가게" unit="선택 수" rows={r.restaurants.map((x) => ({ name: x.name, sub: x.category, n: x.count }))} />
        <RankList title="많이 찾은 음식·맛" unit="요청 수" rows={r.foods.map((x) => ({ name: x.name, n: x.count }))} />
        <div>
          <h3 className="ops-rank-title">
            결제수단 비중 <span className="faint small">완료 {paid}건</span>
          </h3>
          {paid === 0 ? (
            <p className="muted small">아직 없어요.</p>
          ) : (
            r.payment_methods.slice(0, 5).map((x, i) => (
              <BarRow key={x.name} name={x.name} pct={Math.round((x.count / paid) * 100)} value={`${Math.round((x.count / paid) * 100)}%`} color={BAR_COLORS[i % BAR_COLORS.length]} />
            ))
          )}
        </div>
        <div>
          <h3 className="ops-rank-title">
            자주 나온 멈춤 사유 <span className="faint small">차단·실패</span>
          </h3>
          {s.top_reasons.length === 0 ? (
            <p className="muted small">없어요.</p>
          ) : (
            s.top_reasons.map((x, i) => (
              <BarRow key={x.code} name={reasonText(x.code)} pct={(x.count / maxReason) * 100} value={`${x.count}건`} color={REASON_COLORS[i % REASON_COLORS.length]} />
            ))
          )}
        </div>
      </div>
    </section>
  );
}

function RankList({ title, unit, rows }: { title: string; unit: string; rows: { name: string; sub?: string | null; n: number }[] }) {
  return (
    <div>
      <h3 className="ops-rank-title">
        {title} <span className="faint small">{unit}</span>
      </h3>
      {rows.length === 0 ? (
        <p className="muted small">아직 없어요.</p>
      ) : (
        <ol className="ops-rank-list">
          {rows.map((x, i) => (
            <li key={x.name}>
              <span className="ops-rank-n">{i + 1}</span>
              <span className="ellipsis">
                {x.name}
                {x.sub && <small>{x.sub}</small>}
              </span>
              <b>{x.n}</b>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function BarRow({ name, pct, value, color }: { name: string; pct: number; value: string; color: string }) {
  return (
    <div className="ops-bar-row">
      <span className="ellipsis">{name}</span>
      <b>{value}</b>
      <i className="ops-track">
        <i style={{ width: `${pct}%`, background: color }} />
      </i>
    </div>
  );
}
