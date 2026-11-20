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

// 후보 목록 위의 진행 바: "요기요에서 지금 주문 가능한지 확인 중 (2/3)" → "요기요에서 확인했어요 · 10:31"
export function LiveCheckBar({ lc, onRecheck, rechecking }: { lc: LiveCheckView; onRecheck: () => void; rechecking: boolean }) {
  if (lc.state === "OFF" || lc.state === "NONE") return null;
  const running = lc.state === "RUNNING";
  const shown = lc.results.filter((r) => r.visible !== false);
  const done = shown.filter((r) => r.status !== "CHECKING").length;
  const pct = shown.length ? Math.round((done / shown.length) * 100) : 0;
  return (
    <div className="live-bar" role="status" aria-live="polite">
      <div className="live-bar-row">
        <span className="small">
          {running
            ? `요기요에서 지금 주문 가능한지 확인 중 (${done}/${shown.length})`
            : `요기요에서 확인했어요 · ${clock(lastChecked(lc))}`}
        </span>
        {!running && (
          <button className="link-btn small" onClick={onRecheck} disabled={rechecking}>
            {rechecking ? "확인 중…" : "다시 확인"}
          </button>
        )}
      </div>
      {running && (
        <div className="live-track" aria-hidden>
          <i style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
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
