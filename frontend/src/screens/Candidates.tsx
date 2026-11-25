// 03 후보 선택 / 03b 후보 다시 선택 (notice CANDIDATE_UNAVAILABLE)
// 예산 초과 확인(로컬 확장): 예산 안의 후보가 하나도 없으면 팝업으로 묻는다.
//   예 → 예산 초과를 알고 진행한다는 뜻으로 선택 응답에 allow_over_budget을 실어 보낸다.
//   아니오 → 주문을 취소하고 처음 화면으로 돌아간다.
// 최소주문금액 미달(로컬 확장): orderable=false인 후보는 비활성으로 보이고 선택할 수 없다.
//   카드 아래에 "최소주문금액이 맞지 않아서 주문이 불가합니다"를 보여 준다.
// 근접 후보 채우기(이슈 #37): 조건에 맞는 가게가 모자라 채운 후보(near_miss_fill)는 "조건을 조금 벗어난 가게 N곳 더 보기"로 접어 둔다.
// 비슷한 메뉴(이슈 #37): 요청한 메뉴 대신 비슷한 메뉴로 채웠으면 위에 이유(search_notice)를, 카드에 배지를 보인다.
// 이전·다른 가게 추천(이슈 #68): 제목 옆 ‹는 이 주문을 취소하고 같은 문장이 채워진 입력 화면으로 돌아간다.
//   목록을 아래로 당겼다 놓으면(당겨서 새로고침) 지금까지 보여 준 가게를 빼고 다음 순위 가게로 바꾼다. 더 없으면 안내(토스트)만 하고 그대로 둔다.
// 요기요 실시간 확인(O-15): 화면은 기다리지 않고 뜨고, 결과가 오는 대로 진행 바와 배지가 바뀐다.
//   주문 불가로 확인된 후보는 숨기지 않고 제자리에 사유와 함께 비활성으로 보인다(selectable=false).
//   고를 수 있는 후보가 3곳 되도록 다음 순번 후보가 아래에 나온다(visible).
import { useEffect, useState } from "react";
import type { StepProps } from "../pages/RunPage";
import type { Candidate, LiveResult } from "../api/types";
import { api } from "../api/client";
import { LiveBadge, LiveCheckBar, LiveCheckNote, useLiveCheck, useRecheckLive } from "../components/LiveCheck";
import { Banner, Button, Modal, Screen, Tag } from "../components/ui";
import { krw } from "../lib/format";
import { usePullToRefresh } from "../lib/usePullToRefresh";
import { cuisineImage } from "../lib/images";

const isOver = (c: Candidate) => c.violations.some((v) => v.code === "BUDGET_EXCEEDED");
const MIN_ORDER_TEXT = "최소주문금액이 맞지 않아서 주문이 불가합니다";

const DEFAULT_SHOWN = 3; // 확인 결과가 오기 전에 보여 줄 후보 수 (예비 후보는 숨김)

export default function Candidates({ view, busy, send, decline, back, again }: StepProps) {
  const prompt = view.prompt;
  const all = prompt?.type === "SELECT_CANDIDATE" ? prompt.candidates : [];
  const live = useLiveCheck(view.request_id);
  const recheck = useRecheckLive(view.request_id);
  const lc = live.data && live.data.state !== "OFF" ? live.data : null;
  // 요기요 실시간 확인이 끝나야 고를 수 있다 (이슈 #85). 확인 못 함으로 끝나도 고를 수 있고, 꺼져 있으면(OFF) 바로 고른다
  const liveChecking = (!live.data && !live.isError) || live.data?.state === "RUNNING";
  const liveOf = (c: Candidate): LiveResult | undefined => lc?.results.find((r) => r.candidate_index === c.index);
  // 보일 후보: 확인 결과가 있으면 그 visible대로, 없으면 앞에서 3곳 (확인이 꺼져 있으면 전부)
  const list = lc
    ? all.filter((c) => liveOf(c)?.visible ?? true)
    : live.data?.state === "OFF"
      ? all
      : all.slice(0, DEFAULT_SHOWN);
  const isOrderable = (c: Candidate) => c.orderable !== false && liveOf(c)?.selectable !== false;
  // 조건에 맞는 후보가 화면에 있으면 채운 근접 후보는 접어 둔다 (맞는 후보가 모두 주문 불가로 확인되면 펼쳐 보인다)
  const [showMore, setShowMore] = useState(false);
  // 지금 주문할 수 있는(요기요 확인 통과) 가게를 먼저 (이슈 #64). 같은 안에서는 원래 추천 순서 (안정 정렬)
  const orderableFirst = (a: Candidate, b: Candidate) => Number(!isOrderable(a)) - Number(!isOrderable(b));
  const mainList = list.filter((c) => !c.near_miss_fill).sort(orderableFirst);
  // 조건을 조금 벗어난 가게는 지금 주문할 수 있는(요기요 확인 통과) 가게를 먼저 (이슈 #64). 같은 안에서는 원래 순서
  const extraList = list.filter((c) => c.near_miss_fill).sort(orderableFirst);
  const mainOrderable = mainList.some(isOrderable);
  const collapsed = mainOrderable && extraList.length > 0 && !showMore;
  // 주문 가능한 가게 먼저, 같은 안에서는 조건 맞는 가게 → 조건 벗어난 가게 (정렬은 안정 정렬이라 원래 순서 유지)
  const ordered = [...mainList, ...extraList].sort(orderableFirst);
  const shown = collapsed ? mainList : ordered;
  const orderable = shown.filter(isOrderable);
  // 화면에 실제로 보이는 개수로 안내한다 (실시간 확인 결과에 따라 숨는 후보가 있어서)
  const nearNotice =
    mainOrderable && extraList.length > 0
      ? `조건에 맞는 가게 ${mainList.length}곳, 조금 벗어난 가게 ${extraList.length}곳을 찾았어요.`
      : null;
  const [picked, setPicked] = useState<number | null>(orderable[0]?.index ?? null);
  // 어떤 버튼 때문에 기다리는지 (버튼 글자를 그에 맞게 바꾼다)
  const [action, setAction] = useState<"select" | "again" | null>(null);
  // 다른 가게를 추천받아 후보가 바뀌면 고른 것·펼침을 처음으로 (index는 0부터 다시 매겨진다)
  const roundKey = all.map((c) => c.restaurant_name).join("|");
  useEffect(() => {
    setPicked(null);
    setShowMore(false);
    setAction(null);
  }, [roundKey]);

  // 화면에 실제로 보인 후보를 기록한다 (이슈 #48, 점주 대시보드의 '화면 표시' 수).
  // 실시간 확인 결과가 오는 동안 목록이 바뀌므로 1.5초 그대로일 때만 보내고, 실패해도 화면에는 알리지 않는다.
  const rendered = [
    ...(mainOrderable ? mainList : ordered).map((c) => ({ c, collapsed: false })),
    ...(mainOrderable && showMore ? extraList.map((c) => ({ c, collapsed: true })) : []),
  ];
  const viewKey = JSON.stringify(
    rendered.map(({ c, collapsed }) => [c.index, c.restaurant_name, collapsed, liveOf(c)?.status ?? null]),
  );
  useEffect(() => {
    if (rendered.length === 0) return;
    const shown = rendered.map(({ c, collapsed }) => ({
      candidate_index: c.index,
      collapsed,
      live_status: liveOf(c)?.status ?? null,
    }));
    const timer = setTimeout(() => api.recordCandidateViews(view.request_id, shown).catch(() => undefined), 1500);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view.request_id, viewKey]);
  const chosen = orderable.find((c) => c.index === picked) ?? orderable[0];
  const nothingOrderable = list.length > 0 && orderable.length === 0;
  const allClosedOnYogiyo = !!lc?.all_unavailable;

  const noneWithinBudget = orderable.length > 0 && orderable.every(isOver);
  const [consent, setConsent] = useState(false); // 예산 초과를 알고 계속하겠다고 답했는가
  const [ask, setAsk] = useState<"all" | "one" | null>(noneWithinBudget ? "all" : null);
  // 후보가 줄어(03b) 남은 후보가 모두 예산 초과가 되면 다시 묻는다
  useEffect(() => {
    if (noneWithinBudget && !consent) setAsk("all");
  }, [noneWithinBudget, consent]);

  const submit = (allowOverBudget: boolean) => {
    if (!chosen) return;
    setAction("select");
    send({
      type: "SELECT_CANDIDATE",
      candidate_index: chosen.index,
      ...(allowOverBudget ? { allow_over_budget: true } : {}),
    });
  };
  const recommendAgain = () => {
    setAction("again");
    again();
  };
  const againing = busy && action === "again";
  const { ref: pullRef, pull, ready } = usePullToRefresh<HTMLDivElement>(recommendAgain, list.length > 0 && !busy);

  const choose = () => {
    if (!chosen) return;
    if (!isOver(chosen)) return submit(false);
    if (consent) return submit(true);
    setAsk("one"); // 예산 안 후보도 있는데 예산 초과 후보를 골랐을 때
  };

  return (
    <Screen
      title="이런 곳은 어때요?"
      step={view.step}
      footer={
        nothingOrderable ? (
          <Button onClick={decline} disabled={busy}>
            조건 바꿔 다시 주문
          </Button>
        ) : (
          <>
            {/* 이전 (이슈 #68): 취소 대신, 같은 문장이 채워진 입력 화면으로 돌아가 조건을 고친다 */}
            <Button kind="secondary" onClick={back} disabled={busy}>
              이전
            </Button>
            <Button disabled={!chosen || busy || liveChecking} onClick={choose}>
              {busy && action === "select" ? "주문안 만드는 중…" : liveChecking ? "요기요 확인 중…" : "이 가게로"}
            </Button>
          </>
        )
      }
    >
      {view.notice && <Banner tone="warn">{view.notice.message}</Banner>}
      {((prompt?.type === "SELECT_CANDIDATE" && prompt.search_notice) || nearNotice) && (
        <Banner tone="pri">
          {prompt?.type === "SELECT_CANDIDATE" && prompt.search_notice}
          {prompt?.type === "SELECT_CANDIDATE" && prompt.search_notice && nearNotice ? " " : ""}
          {nearNotice}
        </Banner>
      )}
      {consent && <Banner tone="pri">예산을 넘는 가게로 계속 진행해요. 결제 전에 금액을 한 번 더 확인해 드려요.</Banner>}
      {lc && <LiveCheckBar lc={lc} />}
      {nothingOrderable &&
        (allClosedOnYogiyo ? (
          <Banner tone="warn">추천한 가게가 모두 지금 요기요에서 주문을 받지 않아요. 조건을 바꿔 다시 주문해 주세요.</Banner>
        ) : (
          <Banner tone="warn">지금 조건으로는 주문할 수 있는 가게가 없어요. 금액이나 인원을 바꿔 다시 주문해 주세요.</Banner>
        ))}
      {list.length > 0 && !againing && pull === 0 && (
        <p className="pull-hint">↓ 목록을 아래로 당기면 다른 가게를 추천해요</p>
      )}
      {/* 당겨서 다른 가게 추천 (이슈 #68): 목록을 아래로 당겼다 놓으면 지금 본 가게를 빼고 다시 추천 */}
      {list.length > 0 && (
        <div className="pull-ind" style={{ height: againing ? 44 : pull }} aria-live="polite">
          {(againing || pull > 0) && (
            <span className={`pull-ind-inner${againing ? " spinning" : ""}`}>
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden style={{ transform: againing ? undefined : `rotate(${pull * 3}deg)` }}>
                <path
                  d="M4 11V9a4 4 0 0 1 4-4h11m-3-3 3 3-3 3M20 13v2a4 4 0 0 1-4 4H5m3 3-3-3 3-3"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              {againing ? "다른 가게 찾는 중…" : ready ? "놓으면 다른 가게를 추천해요" : "당겨서 다른 가게 추천"}
            </span>
          )}
        </div>
      )}
      <div ref={pullRef} className="pull-zone">
      {(mainOrderable ? mainList : ordered).map((c) => (
        <CandidateCard
          key={c.index}
          c={c}
          live={liveOf(c)}
          selected={chosen?.index === c.index}
          onClick={() => setPicked(c.index)}
        />
      ))}
      {mainOrderable && extraList.length > 0 && (
        <>
          <button className="more-toggle" onClick={() => setShowMore((v) => !v)} aria-expanded={showMore}>
            {showMore ? "조건을 조금 벗어난 가게 접기" : `조건을 조금 벗어난 가게 ${extraList.length}곳 더 보기`}
          </button>
          {showMore &&
            extraList.map((c) => (
              <CandidateCard
                key={c.index}
                c={c}
                live={liveOf(c)}
                selected={chosen?.index === c.index}
                onClick={() => setPicked(c.index)}
              />
            ))}
        </>
      )}
      </div>

      {lc && <LiveCheckNote lc={lc} onRecheck={() => recheck.mutate()} rechecking={recheck.isPending} />}

      {ask === "all" && (
        <Modal
          label="예산 초과 확인"
          actions={
            <>
              <Button kind="secondary" onClick={decline} disabled={busy} autoFocus>
                아니오
              </Button>
              <Button
                onClick={() => {
                  setConsent(true);
                  setAsk(null);
                }}
                disabled={busy}
              >
                예
              </Button>
            </>
          }
        >
          <b>지정 금액에 해당하는 메뉴와 가게가 없어서 예산 초과의 가게를 추천합니다.</b>
          <span>계속 주문하시겠습니까?</span>
        </Modal>
      )}
      {ask === "one" && chosen && (
        <Modal
          label="예산 초과 확인"
          actions={
            <>
              <Button kind="secondary" onClick={() => setAsk(null)} disabled={busy} autoFocus>
                아니오
              </Button>
              <Button
                onClick={() => {
                  setConsent(true);
                  setAsk(null);
                  submit(true);
                }}
                disabled={busy}
              >
                예
              </Button>
            </>
          }
        >
          <b>선택한 가게는 예산을 넘어요.</b>
          <span>
            {chosen.violations.find((v) => v.code === "BUDGET_EXCEEDED")?.message ?? "지정 금액보다 비싼 가게예요."}
          </span>
          <span>계속 주문하시겠습니까?</span>
        </Modal>
      )}
    </Screen>
  );
}

function CandidateCard({
  c,
  live,
  selected,
  onClick,
}: {
  c: Candidate;
  live: LiveResult | undefined;
  selected: boolean;
  onClick: () => void;
}) {
  const r = c.restaurant;
  const meta = [
    r.rating !== null ? `★ ${r.rating.toFixed(1)}` : null,
    r.delivery_minutes !== null ? `약 ${r.delivery_minutes}분` : null,
    r.delivery_fee_krw !== null ? `배달비 ${krw(r.delivery_fee_krw)}` : null,
  ].filter(Boolean);
  const local =
    r.local_currency_merchant === true
      ? r.local_currency_basis === "KOMSCO_MATCHED"
        ? "지역화폐 가능"
        : "지역화폐 가능(추정)"
      : null;
  const minOrderBlocked = c.orderable === false;
  const liveBlocked = live?.selectable === false;
  const blocked = minOrderBlocked || liveBlocked;
  const open = selected && !blocked && c.items.length > 0;
  const minOrderText = c.violations.find((v) => v.code === "MIN_ORDER_NOT_MET")?.message ?? MIN_ORDER_TEXT;
  // 비슷한 메뉴로 고른 이유 ("'마라탕' 대신 비슷한 '짬뽕'을 담았어요 …")
  const similarReason = c.match ? c.reasons.find((r) => r.includes("대신") || r.includes("비슷한 가게")) : undefined;
  const violationText = c.violations
    .filter((v) => v.code !== "MIN_ORDER_NOT_MET")
    .map((v) => v.message)
    .filter(Boolean);

  return (
    <button
      className={`card has-deco${selected && !blocked ? " selected" : ""}`}
      onClick={onClick}
      aria-pressed={blocked ? undefined : selected}
      aria-expanded={blocked ? undefined : open}
      disabled={blocked}
    >
      <div className="title-row">
        <b>{c.restaurant_name}</b>
        <span className="price">{krw(c.total_krw)}</span>
      </div>
      {meta.length > 0 && <span className="muted">{meta.join(" · ")}</span>}
      {/* 담긴 메뉴는 한 줄로 요약하고, 고른 카드만 아래에 펼쳐 보여 준다 (이슈 #105) */}
      <span className="menu-summary">
        {menuSummary(c)}
        {!blocked && c.items.length > 0 && (
          <span className="menu-chev" aria-hidden="true">
            {open ? "▴" : "▾"}
          </span>
        )}
      </span>
      <div className="tags">
        {liveBlocked ? null : minOrderBlocked ? (
          <Tag tone="bad">주문 불가</Tag>
        ) : c.meets_all_constraints ? (
          <Tag tone="ok">조건 충족</Tag>
        ) : (
          <Tag tone="warn">조건 일부 벗어남</Tag>
        )}
        {c.match?.label && <Tag tone="warn">{c.match.label}</Tag>}
        {local && <Tag tone="pri">{local}</Tag>}
        <LiveBadge r={live} />
        {/* 태그 줄 기준으로 위치를 잡아서, 사진 아래쪽이 태그 줄과 같은 선에 온다 */}
        <img className="card-deco" src={cuisineImage(r.cuisine)} alt="" aria-hidden="true" loading="lazy" />
      </div>
      {liveBlocked && <span className="small bad-text">{live?.reason ?? "지금 요기요에서 주문할 수 없어요"}</span>}
      {live?.status === "CHANGED" && live.changes.length > 0 && (
        <span className="small warn-text">요기요 현재: {live.changes.join(" · ")}</span>
      )}
      {minOrderBlocked && (
        <>
          <span className="small bad-text">{minOrderText}</span>
          {c.min_order && (
            <span className="small">
              필요한 최소주문금액 {krw(c.min_order.required_krw)} · 현재 주문 {krw(c.min_order.order_krw)}
            </span>
          )}
        </>
      )}
      {violationText.length > 0 ? (
        <span className="small" style={{ color: "var(--bad)" }}>
          {violationText.join(" ")}
        </span>
      ) : (
        c.reasons[0] && <span className="muted small">{c.reasons[0]}</span>
      )}
      {similarReason && <span className="small warn-text">{similarReason}</span>}
      {open && (
        <ul className="menu-items" aria-label={`${c.restaurant_name} 담긴 메뉴`}>
          {c.items.map((i) => (
            <li key={`${i.name}-${i.quantity}`}>
              <span>
                {i.name} × {i.quantity}
              </span>
              <b>{i.unit_price_krw !== null ? krw(i.unit_price_krw * i.quantity) : "—"}</b>
            </li>
          ))}
        </ul>
      )}
    </button>
  );
}

// "짜장면 외 1개 · 3개": 첫 메뉴 이름, 나머지 메뉴 수, 전체 수량
function menuSummary(c: Candidate): string {
  if (c.items.length === 0) return "담긴 메뉴 없음";
  const total = c.items.reduce((n, i) => n + i.quantity, 0);
  const rest = c.items.length - 1;
  return `${c.items[0].name}${rest > 0 ? ` 외 ${rest}개` : ""} · ${total}개`;
}
