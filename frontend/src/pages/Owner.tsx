// ODIGO 우리 가게 길잡이 = 점주 대시보드 (이슈 #50·#92, 로컬 확장) — "우리 가게가 왜 추천되고 왜 빠졌는지, 이번 주 무엇을 고치면 되는지"
//   /owner?r=<가게 ID>&days=7
//   추천 흐름(후보 → 고객 화면 → 선택 → 주문 완료) · 이번 주 할 일 · 빠진 이유 · 고객이 찾은 것 · 메뉴별 반응
//   MVP 1 시연: 로그인한 사용자면 아무 가게나 고를 수 있다 (점주 인증은 MVP 2).
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { OwnerDashboard, OwnerFunnel } from "../api/types";
import { Tag } from "../components/ui";
import { krw, OWNER_EXCLUSION } from "../lib/format";

const STEPS: { key: keyof OwnerFunnel; label: string; hint: string }[] = [
  { key: "candidates", label: "추천 후보", hint: "조건에 맞아 후보로 뽑힌 횟수" },
  { key: "shown", label: "고객 화면에 보임", hint: "후보 화면에 실제로 보인 횟수" },
  { key: "selected", label: "고객이 선택", hint: "고객이 이 가게를 고른 횟수" },
  { key: "completed", label: "주문 완료", hint: "결제까지 끝난 횟수 (시연은 모의 결제)" },
];

export default function Owner() {
  const [params, setParams] = useSearchParams();
  const restaurantId = params.get("r") ?? "";
  const days = Number(params.get("days")) === 30 ? 30 : 7;
  const [q, setQ] = useState("");
  const set = (next: Record<string, string>) => setParams({ r: restaurantId, days: String(days), ...next });

  const list = useQuery({ queryKey: ["owner-restaurants", q], queryFn: () => api.ownerRestaurants(q) });
  const rows = list.data?.results ?? [];
  const current = restaurantId || rows[0]?.restaurant_id || "";
  const dash = useQuery({
    queryKey: ["owner-dashboard", current, days],
    queryFn: () => api.ownerDashboard(current, days),
    enabled: !!current,
  });

  return (
    <div className="admin owner">
      <header className="admin-head owner-band">
        <div>
          <h1>ODIGO 우리 가게 길잡이</h1>
          <p className="owner-band-sub">
            ODIGO 추천에서 우리 가게가 어떻게 보였는지, 이번 주 무엇을 해 보면 좋을지 알려 드려요.
          </p>
        </div>
        <div className="admin-bar">
          <input
            className="input"
            placeholder="가게 이름 검색"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            aria-label="가게 이름 검색"
          />
          <select
            className="input owner-select"
            value={current}
            onChange={(e) => set({ r: e.target.value })}
            aria-label="가게 고르기"
          >
            {restaurantId && !rows.some((r) => r.restaurant_id === restaurantId) && (
              <option value={restaurantId}>{dash.data?.restaurant.name ?? restaurantId}</option>
            )}
            {rows.map((r) => (
              <option key={r.restaurant_id} value={r.restaurant_id}>
                {r.name} {r.candidates_7d ? `· 후보 ${r.candidates_7d}번` : ""}
              </option>
            ))}
          </select>
          <div className="tabs" role="group" aria-label="기간">
            {[7, 30].map((d) => (
              <button key={d} className={days === d ? "on" : ""} onClick={() => set({ r: current, days: String(d) })}>
                최근 {d}일
              </button>
            ))}
          </div>
        </div>
      </header>

      {list.error ? (
        <p className="empty">가게 목록을 불러오지 못했어요. 백엔드 서버가 켜져 있는지 확인해 주세요. ({(list.error as Error).message})</p>
      ) : !current ? (
        <p className="empty">{list.isLoading ? "불러오는 중…" : q ? `'${q}'이(가) 들어간 가게가 없어요.` : "가게를 찾지 못했어요."}</p>
      ) : dash.error ? (
        <p className="empty">길잡이를 불러오지 못했어요. ({(dash.error as Error).message})</p>
      ) : !dash.data ? (
        <p className="muted">불러오는 중…</p>
      ) : (
        <Report d={dash.data} />
      )}
    </div>
  );
}

// ---- 상태 표시 (이슈 #92): 프론트에서 계산, 기준값은 여기 한곳에서 바꾼다 ----
type Status = "ok" | "warn" | "bad";
const STATUS_TEXT: Record<Status, string> = { ok: "좋음", warn: "보통", bad: "개선 필요" };
// 앞 단계 대비 비율(%) 기준: [좋음 이상, 보통 이상]
const RATE_RULES: Record<"shown" | "selected" | "completed", [number, number]> = {
  shown: [80, 60], // 후보 → 고객 화면에 보임
  selected: [50, 35], // 화면에 보임 → 고객이 선택
  completed: [60, 40], // 선택 → 주문 완료
};
const QUALITY_RULE: [number, number] = [70, 40]; // 메뉴 정보 충실도(세 항목 평균)

// 빠진 이유마다 다른 색 (없는 코드는 순서대로 기본 색)
const EXCLUSION_COLOR: Record<string, string> = {
  OUT_OF_RANK: "#5b6cd9",
  BELOW_MIN_ORDER: "#e39a2d",
  CLOSED_NOW: "#8e6fd8",
  BUDGET_EXCEEDED: "#e0605e",
  DELIVERY_TIME_EXCEEDED: "#2a9d8f",
  NO_REQUESTED_MENU: "#d16ba5",
  NO_TASTE_MATCH: "#c08a3e",
  NO_ORDERABLE_MENU: "#6b7280",
  LOCAL_CURRENCY_NOT_CONFIRMED: "#4c9be8",
};
const FALLBACK_COLORS = ["#5b6cd9", "#e39a2d", "#8e6fd8", "#e0605e", "#2a9d8f", "#d16ba5"];

const byRate = (rate: number, [good, fair]: [number, number]): Status =>
  rate >= good ? "ok" : rate >= fair ? "warn" : "bad";
const rate = (a: number, b: number) => (b > 0 ? Math.round((a / b) * 100) : null);

type StepView = { key: keyof OwnerFunnel; label: string; hint: string; status: Status | null; ratio: string | null; note?: string };

function stepViews(d: OwnerDashboard): StepView[] {
  const f = d.funnel;
  return STEPS.map((s, i) => {
    if (i === 0) {
      const before = d.previous.candidates;
      const status: Status | null = !before && !f.candidates ? null : !before ? "ok" : f.candidates > before ? "ok" : f.candidates === before ? "warn" : "bad";
      return { ...s, status, ratio: null };
    }
    const prevKey = STEPS[i - 1].key;
    const r = rate(f[s.key], f[prevKey]);
    const base = i === 1 ? "후보" : i === 2 ? "보인 것" : "선택";
    // 앞 단계보다 많으면(예: 화면 기록을 남기기 전 주문) 비율을 믿을 수 없어 상태를 붙이지 않는다
    if (r !== null && r > 100) return { ...s, status: null, ratio: null, note: "기록 부족" };
    return {
      ...s,
      status: r === null ? null : byRate(r, RATE_RULES[s.key as keyof typeof RATE_RULES]),
      ratio: r === null ? null : `${base}의 ${r}%`,
    };
  });
}

// 한 줄 요약: 가장 약한 단계 → 가장 많이 빠진 이유 → 잘 되고 있음
function summary(d: OwnerDashboard, steps: StepView[]): { text: string; strong?: string; tail?: string; tone: Status } {
  const f = d.funnel;
  if (!f.candidates)
    return { text: `최근 ${d.period.days}일 동안 추천 후보에 오른 적이 없어요. 아래 빠진 이유를 확인해 보세요.`, tone: "bad" };
  const weak = steps.slice(1).find((s) => s.status === "bad") ?? steps.slice(1).find((s) => s.status === "warn");
  if (weak) {
    const r = rate(f[weak.key], f[STEPS[STEPS.findIndex((s) => s.key === weak.key) - 1].key]);
    const what =
      weak.key === "shown" ? "고객 화면에 보이는 비율" : weak.key === "selected" ? "화면에서 골라지는 비율" : "주문까지 이어지는 비율";
    const lead = weak.key === "shown" ? `후보에는 ${f.candidates}번 올랐지만, ` : weak.key === "selected" ? "후보로는 자주 뽑히지만, " : `${f.selected}번 선택됐지만, `;
    return { text: lead, strong: `${what}(${r}%)`, tail: "이 낮아요.", tone: weak.status ?? "warn" };
  }
  const top = d.exclusions[0];
  if (top)
    return {
      text: "흐름은 좋아요. 가장 많이 빠진 이유는 ",
      strong: `'${OWNER_EXCLUSION[top.reason] ?? top.reason}'(${top.count}번)`,
      tail: "이에요.",
      tone: "ok",
    };
  return { text: `후보 ${f.candidates}번이 주문 ${f.completed}번으로 잘 이어지고 있어요.`, tone: "ok" };
}

function Report({ d }: { d: OwnerDashboard }) {
  const r = d.restaurant;
  const steps = stepViews(d);
  const sum = summary(d, steps);
  const maxEx = Math.max(1, ...d.exclusions.map((e) => e.count));
  const totalEx = d.exclusions.reduce((a, e) => a + e.count, 0);
  const q = d.menu_quality;
  const quality = [
    { label: "설명 있음", ok: q.mains - q.no_description },
    { label: "인분 표기", ok: q.mains - q.no_servings },
    { label: "맵기 정보", ok: q.mains - q.no_spice },
  ].map((x) => ({ ...x, rate: q.mains ? Math.round((x.ok / q.mains) * 100) : 0 }));
  const qualityAvg = q.mains ? Math.round(quality.reduce((a, x) => a + x.rate, 0) / quality.length) : null;
  const menus = d.menus.slice(0, 8);
  const maxRec = Math.max(1, ...menus.map((m) => m.recommended));
  const maxSel = Math.max(1, ...menus.map((m) => m.selected));
  const hasRequested =
    d.requested.foods.length > 0 || d.requested.tastes.length > 0 || !!d.requested.budget_per_person_median || d.closed_hours.length > 0;

  return (
    <div className="admin-stack" style={{ marginTop: 18 }}>
      <section className="owner-title">
        <div className="title-row">
          <b className="detail-title">{r.name}</b>
          {r.category && <Tag>{r.category}</Tag>}
          {d.demo_data && <Tag tone="warn">시연 데이터 포함</Tag>}
        </div>
        <p className="faint small" style={{ margin: 0 }}>
          ★ {r.rating.toFixed(1)} (리뷰 {r.review_count.toLocaleString()}) · 최소주문 {krw(r.min_order_krw)} · 배달팁{" "}
          {krw(r.base_delivery_fee_krw)} · 영업 {r.opening_hours ?? "미등록"}
        </p>
      </section>

      <section className="owner-funnel" aria-label="추천 흐름">
        {steps.map((s) => (
          <div key={s.key} className={`owner-stat ${s.status === "bad" ? "is-bad" : ""}`} title={s.hint}>
            <div className="owner-stat-head">
              <span className="stat-label">{s.label}</span>
              {s.status ? <Tag tone={s.status}>{STATUS_TEXT[s.status]}</Tag> : <Tag tone="gray">{s.note ?? "기록 없음"}</Tag>}
            </div>
            <b className={`owner-stat-value ${s.key === "completed" ? "pri" : ""}`}>
              {d.funnel[s.key].toLocaleString()}
              <small>번</small>
            </b>
            <span className="small">
              <Delta now={d.funnel[s.key]} before={d.previous[s.key]} days={d.period.days} />
              {s.ratio && <span className="faint"> · {s.ratio}</span>}
            </span>
          </div>
        ))}
      </section>

      <section className={`owner-summary ${sum.tone}`} aria-label="한 줄 요약">
        <span className="owner-summary-label">한 줄 요약</span>
        <p>
          {sum.text}
          {sum.strong && <strong>{sum.strong}</strong>}
          {sum.tail}
        </p>
      </section>

      <section className="panel">
        <h2>이번 주 해 볼 일 <span className="faint small">근거가 큰 순서로 최대 3개</span></h2>
        {d.actions.length === 0 ? (
          <p className="muted">지금은 특별히 고칠 점이 보이지 않아요. 기록이 더 쌓이면 알려 드릴게요.</p>
        ) : (
          <ol className="owner-actions">
            {d.actions.map((a, i) => (
              <li key={a.code}>
                <span className="owner-action-num">{i + 1}</span>
                <div>
                  <b>{a.title}</b>
                  <span>{a.detail}</span>
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>

      <div className="admin-two">
        <section className="panel">
          <div className="panel-head">
            <h2>추천에서 빠진 이유</h2>
            {totalEx > 0 && <span className="faint small">총 {totalEx}번</span>}
          </div>
          {d.exclusions.length === 0 ? (
            <p className="muted">빠진 기록이 없어요.</p>
          ) : (
            <ul className="owner-bars">
              {d.exclusions.map((e, i) => {
                const color = EXCLUSION_COLOR[e.reason] ?? FALLBACK_COLORS[i % FALLBACK_COLORS.length];
                return (
                  <li key={e.reason}>
                    <span>
                      <em className="owner-dot" style={{ background: color }} />
                      {OWNER_EXCLUSION[e.reason] ?? e.reason}
                    </span>
                    <i className="owner-track">
                      <i style={{ width: `${(e.count / maxEx) * 100}%`, background: color }} />
                    </i>
                    <b>{e.count}번</b>
                  </li>
                );
              })}
            </ul>
          )}
          {d.shown_unavailable > 0 && (
            <p className="small" style={{ marginTop: 10 }}>
              고객 화면에 보였지만 요기요에서 주문할 수 없던 때가 <b>{d.shown_unavailable}번</b> 있었어요.
            </p>
          )}
          <p className="faint small" style={{ marginTop: 8, marginBottom: 0 }}>
            '조건은 맞지만 순위 밖'은 다른 가게가 평점·거리·가격에서 앞서 뒤로 밀린 경우예요.
          </p>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>메뉴 정보 충실도</h2>
            {qualityAvg !== null && <Tag tone={byRate(qualityAvg, QUALITY_RULE)}>{STATUS_TEXT[byRate(qualityAvg, QUALITY_RULE)]}</Tag>}
          </div>
          <ul className="owner-bars">
            {quality.map((x) => {
              const st = byRate(x.rate, QUALITY_RULE);
              return (
                <li key={x.label}>
                  <span>{x.label}</span>
                  <i className="owner-track">
                    <i className={`fill-${st}`} style={{ width: `${x.rate}%` }} />
                  </i>
                  <b>{q.mains ? `${x.rate}%` : "-"}</b>
                </li>
              );
            })}
          </ul>
          <p className="faint small" style={{ marginTop: 10, marginBottom: 0 }}>
            메인 메뉴 {q.mains}개 기준 · 정보가 많을수록 맛·인원 조건에 잘 맞춰 추천돼요.
          </p>
        </section>
      </div>

      <section className="panel">
        <h2>메뉴별 반응</h2>
        {menus.length === 0 ? (
          <p className="muted">추천에 들어간 메뉴가 아직 없어요.</p>
        ) : (
          <div className="owner-menus">
            <span className="faint small">메뉴</span>
            <span className="faint small">추천에 들어감</span>
            <span className="faint small">고객이 선택</span>
            {menus.map((m) => (
              <MenuRow key={m.name} m={m} maxRec={maxRec} maxSel={maxSel} />
            ))}
          </div>
        )}
      </section>

      {hasRequested && (
        <details className="panel owner-more">
          <summary>이 가게를 볼 수 있던 고객이 찾은 것</summary>
          <dl className="kv" style={{ marginTop: 12 }}>
            <dt>찾은 음식</dt>
            <dd>{names(d.requested.foods) || "기록 없음"}</dd>
            <dt>원한 맛</dt>
            <dd>{names(d.requested.tastes) || "기록 없음"}</dd>
            <dt>1인당 예산 (중간값)</dt>
            <dd>{d.requested.budget_per_person_median ? krw(d.requested.budget_per_person_median) : "기록 없음"}</dd>
            {d.closed_hours.length > 0 && (
              <>
                <dt>영업시간 밖 요청</dt>
                <dd>{d.closed_hours.map((h) => `${h}시`).join(", ")}에 많았어요</dd>
              </>
            )}
          </dl>
        </details>
      )}

      <p className="faint small">
        기간: 최근 {d.period.days}일 ({d.period.since.slice(0, 10)} ~ {d.period.until.slice(0, 10)}) · 다른 가게나 고객
        개인 정보는 보여 주지 않아요.
      </p>
    </div>
  );
}

function MenuRow({ m, maxRec, maxSel }: { m: OwnerDashboard["menus"][number]; maxRec: number; maxSel: number }) {
  return (
    <>
      <span className="owner-menu-name">{m.name}</span>
      <span className="owner-menu-bar">
        <i className="owner-track">
          <i style={{ width: `${(m.recommended / maxRec) * 100}%` }} />
        </i>
        <b>{m.recommended}</b>
      </span>
      <span className="owner-menu-bar">
        <i className="owner-track">
          <i className="fill-ok" style={{ width: `${(m.selected / maxSel) * 100}%` }} />
        </i>
        <b>{m.selected}</b>
      </span>
    </>
  );
}

function Delta({ now, before, days }: { now: number; before: number; days: number }) {
  if (!before) return <span className="faint">지난 {days}일 기록 없음</span>;
  const diff = now - before;
  if (diff === 0) return <span className="faint">지난 {days}일과 같아요</span>;
  return (
    <span className={diff > 0 ? "up" : "down"}>
      {diff > 0 ? "▲" : "▼"} {Math.abs(diff)} <span className="faint">지난 {days}일보다</span>
    </span>
  );
}

const names = (xs: { name: string; count: number }[]) =>
  xs
    .slice(0, 5)
    .map((x) => `${x.name} ${x.count}`)
    .join(" · ");
