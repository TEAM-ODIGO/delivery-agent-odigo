// 요기요 장바구니 담기 (O-13 시작 · O-14 결과 폴링)
// 요기요 장바구니까지만 채우고 멈춘다. 요기요의 주문하기·결제는 누르지 않는다.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { YogiyoCartJob } from "../api/types";
import { krw } from "../lib/format";
import { Banner, Button, Row, Tag } from "./ui";

const ACTIVE = new Set(["QUEUED", "RUNNING"]);

const STATUS_TAG: Record<string, { tone: "ok" | "warn" | "bad" | "pri"; text: string }> = {
  QUEUED: { tone: "pri", text: "대기 중" },
  RUNNING: { tone: "pri", text: "담는 중" },
  CART_READY: { tone: "ok", text: "견적과 일치" },
  CART_MISMATCH: { tone: "warn", text: "견적과 다름" },
  FAILED: { tone: "bad", text: "실패" },
};

// 승인 화면과 장바구니 카드가 같은 결과를 본다 (같은 queryKey라 요청은 한 번)
export function useYogiyoCart(requestId: string) {
  return useQuery({
    queryKey: ["yogiyo-cart", requestId],
    queryFn: () => api.getYogiyoCart(requestId),
    refetchInterval: (q) => (ACTIVE.has(q.state.data?.status ?? "") ? 1500 : false),
  });
}

// 승인을 막아야 하는 이유 (없으면 null). 담기를 안 했거나 금액만 다르면 막지 않는다.
export function cartBlockReason(job: YogiyoCartJob | undefined, starting = false): string | null {
  if (starting || ACTIVE.has(job?.status ?? "")) return "요기요 장바구니를 담는 중이에요. 끝난 뒤 승인할 수 있어요.";
  if (job?.status === "FAILED")
    return "요기요 장바구니 담기에 실패해서 결제할 수 없어요. 다시 담거나 주문을 취소해 주세요.";
  return null;
}

export default function YogiyoCart({ requestId }: { requestId: string }) {
  const qc = useQueryClient();
  const key = ["yogiyo-cart", requestId];
  const job = useYogiyoCart(requestId);
  const start = useMutation({
    mutationKey: ["yogiyo-cart-start", requestId],
    mutationFn: () => api.startYogiyoCart(requestId),
    onSuccess: (data: YogiyoCartJob) => qc.setQueryData(key, data),
  });

  const data = job.data;
  const status = data?.status ?? "NONE";
  const active = ACTIVE.has(status) || start.isPending;
  const tag = STATUS_TAG[status];
  const startError = start.error instanceof ApiError ? start.error.message : start.error ? "요청을 처리하지 못했어요." : null;

  return (
    <div className="card">
      <div className="title-row">
        <b>요기요 장바구니</b>
        {tag && <Tag tone={tag.tone}>{tag.text}</Tag>}
      </div>
      <span className="muted">
        같은 메뉴를 요기요 장바구니에 담아 금액을 맞춰 봐요. 요기요에서 주문·결제는 하지 않아요.
      </span>

      {active && <span className="muted">요기요에서 담는 중… (5~20초)</span>}

      {data && !active && status !== "NONE" && (
        <>
          {data.cart_items?.map((i) => (
            <Row
              key={String(i.menu_id)}
              label={`${i.name ?? i.menu_id} × ${i.quantity}`}
              value={i.unit_price_krw != null ? krw(i.unit_price_krw * i.quantity) : "—"}
            />
          ))}
          {data.menu_subtotal_krw != null && <Row label="요기요 메뉴 합계" value={krw(data.menu_subtotal_krw)} />}
          {data.delivery_fee_krw != null && <Row label="요기요 배달비" value={krw(data.delivery_fee_krw)} />}
          {data.mismatches?.map((m) => (
            <Banner key={m}>{m}</Banner>
          ))}
          {data.error && <Banner tone="bad">{data.error}</Banner>}
          {data.screenshot && (
            <img
              src={data.screenshot}
              alt="요기요 장바구니 화면"
              style={{ width: "100%", borderRadius: 8, border: "1px solid var(--line)" }}
            />
          )}
          {data.mode === "fake" && <span className="faint">시연 모드(fake): 실제 요기요에 접속하지 않았어요.</span>}
        </>
      )}

      {startError && <Banner tone="bad">{startError}</Banner>}

      <Button kind="secondary" style={{ flex: "none", width: "100%" }} disabled={active} onClick={() => start.mutate()}>
        {active ? "담는 중…" : status === "NONE" ? "요기요 장바구니에 담기" : "다시 담기"}
      </Button>
    </div>
  );
}
