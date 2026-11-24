// 점주 대시보드 (이슈 #50, 로컬 확장) — "우리 가게가 왜 추천되고 왜 빠졌는지, 이번 주 무엇을 고치면 되는지"
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
      <header className="admin-head">
        <div>
          <h1>가게 리포트</h1>
          <p className="muted" style={{ margin: 0 }}>
            ODIGO 추천에서 우리 가게가 어떻게 보였는지, 이번 주 무엇을 고치면 좋을지 알려 드려요.
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
        <p className="empty">리포트를 불러오지 못했어요. ({(dash.error as Error).message})</p>
      ) : !dash.data ? (
        <p className="muted">불러오는 중…</p>
      ) : (
        <Report d={dash.data} />
      )}
    </div>
  );
}

function Report({ d }: { d: OwnerDashboard }) {
  const f = d.funnel;
  const r = d.restaurant;
  const maxEx = Math.max(1, ...d.exclusions.map((e) => e.count));
  const totalEx = d.exclusions.reduce((a, e) => a + e.count, 0);
  const q = d.menu_quality;

  return (
    <div className="admin-stack" style={{ marginTop: 18 }}>
      <section className="owner-hero">
        <div className="title-row">
          <b className="detail-title">{r.name}</b>
          {r.category && <Tag>{r.category}</Tag>}
          {d.demo_data && <Tag tone="warn">시연 데이터 포함</Tag>}
        </div>
        <p className="owner-sentence">{sentence(d)}</p>
        <p className="faint small" style={{ margin: 0 }}>
          ★ {r.rating.toFixed(1)} (리뷰 {r.review_count.toLocaleString()}) · 최소주문 {krw(r.min_order_krw)} · 배달팁{" "}
          {krw(r.base_delivery_fee_krw)} · 영업 {r.opening_hours ?? "미등록"}
        </p>
      </section>

      <section className="owner-funnel" aria-label="추천 흐름">
        {STEPS.map((s, i) => (
          <div key={s.key} className="stat" title={s.hint}>
            <span className="stat-label">
              {i + 1}. {s.label}
            </span>
            <b className="stat-value">{f[s.key].toLocaleString()}번</b>
            <Delta now={f[s.key]} before={d.previous[s.key]} days={d.period.days} />
            {i > 0 && f[STEPS[i - 1].key] > 0 && (
              <span className="faint small">앞 단계의 {pct(f[s.key], f[STEPS[i - 1].key])}</span>
            )}
          </div>
        ))}
      </section>

      <section className="panel">
        <h2>지금 해 볼 일 <span className="faint small">근거가 큰 순서로 최대 3개</span></h2>
        {d.actions.length === 0 ? (
          <p className="muted">지금은 특별히 고칠 점이 보이지 않아요. 기록이 더 쌓이면 알려 드릴게요.</p>
        ) : (
          <ol className="owner-actions">
            {d.actions.map((a) => (
              <li key={a.code}>
                <b>{a.title}</b>
                <span>{a.detail}</span>
              </li>
            ))}
          </ol>
        )}
      </section>

      <div className="admin-two">
        <section className="panel">
          <h2>추천에서 빠진 이유 {totalEx > 0 && <span className="faint small">총 {totalEx}번</span>}</h2>
          {d.exclusions.length === 0 ? (
            <p className="muted">빠진 기록이 없어요.</p>
          ) : (
            <ul className="bar-list">
              {d.exclusions.map((e) => (
                <li key={e.reason}>
                  <span>{OWNER_EXCLUSION[e.reason] ?? e.reason}</span>
                  <i style={{ width: `${(e.count / maxEx) * 100}%` }} />
                  <b>{e.count}번</b>
                </li>
              ))}
            </ul>
          )}
          {d.shown_unavailable > 0 && (
            <p className="small" style={{ marginTop: 10 }}>
              고객 화면에 보였지만 요기요에서 주문할 수 없던 때가 <b>{d.shown_unavailable}번</b> 있었어요.
            </p>
          )}
          <p className="faint small" style={{ marginTop: 8 }}>
            '조건은 맞지만 순위 밖'은 다른 가게가 평점·거리·가격에서 앞서 뒤로 밀린 경우예요.
          </p>
        </section>

        <section className="panel">
          <h2>이 가게를 볼 수 있던 고객이 찾은 것</h2>
          <dl className="kv">
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
        </section>
      </div>

      <div className="admin-two">
        <section className="panel">
          <h2>메뉴별 반응</h2>
          {d.menus.length === 0 ? (
            <p className="muted">추천에 들어간 메뉴가 아직 없어요.</p>
          ) : (
            <table className="data">
              <thead>
                <tr>
                  <th>메뉴</th>
                  <th className="num">추천에 들어감</th>
                  <th className="num">고객이 선택</th>
                </tr>
              </thead>
              <tbody>
                {d.menus.slice(0, 8).map((m) => (
                  <tr key={m.name}>
                    <td>{m.name}</td>
                    <td className="num">{m.recommended}번</td>
                    <td className="num">{m.selected}번</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="panel">
          <h2>메뉴 정보 상태</h2>
          <p className="small" style={{ marginTop: 0 }}>
            메인 메뉴 {q.mains}개 기준이에요. 정보가 많을수록 맛·인원 조건에 잘 맞춰 추천돼요.
          </p>
          <ul className="bar-list owner-quality">
            {[
              { label: "설명 있음", ok: q.mains - q.no_description },
              { label: "인분 표기 있음", ok: q.mains - q.no_servings },
              { label: "맵기 정보 있음", ok: q.mains - q.no_spice },
            ].map((x) => (
              <li key={x.label}>
                <span>{x.label}</span>
                <i style={{ width: `${q.mains ? (x.ok / q.mains) * 100 : 0}%` }} />
                <b>{q.mains ? pct(x.ok, q.mains) : "-"}</b>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <p className="faint small">
        기간: 최근 {d.period.days}일 ({d.period.since.slice(0, 10)} ~ {d.period.until.slice(0, 10)}) · 다른 가게나 고객
        개인 정보는 보여 주지 않아요.
      </p>
    </div>
  );
}

function Delta({ now, before, days }: { now: number; before: number; days: number }) {
  if (!before) return <span className="faint small">지난 {days}일 기록 없음</span>;
  const diff = now - before;
  if (diff === 0) return <span className="faint small">지난 {days}일과 같아요</span>;
  return (
    <span className={`small ${diff > 0 ? "up" : "down"}`}>
      지난 {days}일보다 {diff > 0 ? "▲" : "▼"} {Math.abs(diff)}번
    </span>
  );
}

function sentence(d: OwnerDashboard): string {
  const f = d.funnel;
  if (!f.candidates) return `최근 ${d.period.days}일 동안 추천 후보에 오른 적이 없어요. 아래 빠진 이유를 확인해 보세요.`;
  const top = d.exclusions[0];
  const why = top ? ` 가장 많이 빠진 이유는 '${OWNER_EXCLUSION[top.reason] ?? top.reason}'(${top.count}번)이에요.` : "";
  return (
    `최근 ${d.period.days}일 동안 추천 후보에 ${f.candidates}번 올라 고객 화면에 ${f.shown}번 보였고, ` +
    `${f.selected}번 선택돼 ${f.completed}번 주문으로 이어졌어요.${why}`
  );
}

const pct = (a: number, b: number) => `${Math.round((a / b) * 100)}%`;
const names = (xs: { name: string; count: number }[]) =>
  xs
    .slice(0, 5)
    .map((x) => `${x.name} ${x.count}`)
    .join(" · ");
