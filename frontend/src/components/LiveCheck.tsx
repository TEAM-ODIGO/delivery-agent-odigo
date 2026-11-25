// 요기요 실시간 식당 확인 (O-15 결과 폴링 · O-16 다시 확인)
// 후보 화면은 확인을 기다리지 않고 뜨고, 결과가 오는 대로 진행 바와 카드 배지가 바뀐다.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { LiveCheckView, LiveResult } from "../api/types";
import { clock } from "../lib/format";
import { Tag } from "./ui";

export function useLiveCheck(requestId: string) {
  return useQuery({
    queryKey: ["live-check", requestId],
    queryFn: () => api.getLiveCheck(requestId),
    refetchInterval: (q) => (q.state.data?.state === "RUNNING" ? 1500 : false),
  });
}

export function useRecheckLive(requestId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.recheckLive(requestId),
    onSuccess: (data: LiveCheckView) => qc.setQueryData(["live-check", requestId], data),
  });
}

const lastChecked = (lc: LiveCheckView) =>
  lc.results
    .map((r) => r.checked_at)
    .filter((t): t is string => !!t)
    .sort()
    .at(-1) ?? lc.started_at ?? null;

// 후보 목록 위의 진행 바 (확인 중일 때만): "요기요에서 지금 주문 가능한지 확인 중 (2/3)"
// 확인 중에는 배지가 바뀌고 다음 순번 가게가 나타나 목록이 움직이므로 위에 크게 알린다.
// 확인이 끝나면 위에서 사라지고, 목록 맨 아래 LiveCheckNote 한 줄로 내려간다 (이슈 #68)
export function LiveCheckBar({ lc }: { lc: LiveCheckView }) {
  if (lc.state !== "RUNNING") return null;
  const shown = lc.results.filter((r) => r.visible !== false);
  const done = shown.filter((r) => r.status !== "CHECKING").length;
  const pct = shown.length ? Math.round((done / shown.length) * 100) : 0;
  return (
    <div className="live-bar" role="status" aria-live="polite">
      <div className="live-bar-row">
        <span className="small">{`요기요에서 지금 주문 가능한지 확인 중 (${done}/${shown.length})`}</span>
      </div>
      <div className="live-track" aria-hidden>
        <i style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// 목록 맨 아래 작은 한 줄 (확인이 끝난 뒤): "요기요 기준 10:31 확인 ⟳" (이슈 #68)
// 판단은 카드 배지로 충분해서 눈에 띄지 않게 두고, 끝의 아이콘으로 다시 확인할 수 있다
export function LiveCheckNote({ lc, onRecheck, rechecking }: { lc: LiveCheckView; onRecheck: () => void; rechecking: boolean }) {
  if (lc.state !== "DONE") return null;
  return (
    <div className="live-note" role="status">
      <span>요기요 기준 {clock(lastChecked(lc))} 확인</span>
      <button
        className={`icon-btn${rechecking ? " spinning" : ""}`}
        onClick={onRecheck}
        disabled={rechecking}
        aria-label="요기요에서 다시 확인"
        title="요기요에서 다시 확인"
      >
        <RefreshIcon />
      </button>
    </div>
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" aria-hidden>
      <path
        d="M4 11V9a4 4 0 0 1 4-4h11m-3-3 3 3-3 3M20 13v2a4 4 0 0 1-4 4H5m3 3-3-3 3-3"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 카드 배지
export function LiveBadge({ r }: { r: LiveResult | undefined }) {
  if (!r) return null;
  switch (r.status) {
    case "CHECKING":
      return <Tag tone="gray">요기요 확인 중…</Tag>;
    case "OK":
      return <Tag tone="ok">지금 주문 가능</Tag>;
    case "CHANGED":
      return <Tag tone="warn">요기요 정보 변경</Tag>;
    case "UNAVAILABLE":
      return <Tag tone="bad">지금 주문 불가</Tag>;
    case "BLOCKED":
    case "ERROR":
      return <Tag tone="gray">요기요 확인 못 함</Tag>;
    default:
      return null;
  }
}
