// 06a 결제 완료 · 06b 결제 확인 중 · 05b 진행 차단 · 06c 실패/취소
import { useNavigate } from "react-router-dom";
import type { StepProps } from "../pages/RunPage";
import type { OrderView } from "../api/types";
import { Button, Row, Screen, StatusIcon, Tag } from "../components/ui";
import YogiyoCart from "../components/YogiyoCart";
import { BLOCK_REASON, EXCLUDE_REASON, FAIL_TEXT, krw } from "../lib/format";

function OrderRows({ order, hideMethod }: { order: OrderView; hideMethod?: boolean }) {
  return (
    <>
      <Row label="가게" value={order.restaurant_name} />
      <Row label="메뉴" value={order.items.map((i) => `${i.name} × ${i.quantity}`).join(", ")} />
      <Row label="메뉴 + 배달비" value={krw(order.menu_subtotal_krw + order.delivery_fee_krw)} />
      {order.instant_discount_krw > 0 && <Row label="즉시할인" value={`−${krw(order.instant_discount_krw)}`} tone="ok" />}
      {!hideMethod && <Row label="결제수단" value={order.payment_method.name ?? "—"} />}
    </>
  );
}

export function Done({ view }: StepProps) {
  const nav = useNavigate();
  const r = view.result!;
  const order = r.order;
  return (
    <Screen
      title="결제 완료"
      step={{ ...view.step, label: `${view.step.label} (모의결제)` }}
      footer={
        <>
          <Button kind="secondary" onClick={() => nav("/orders")}>
            내 주문
          </Button>
          <Button onClick={() => nav("/")}>새 주문</Button>
        </>
      }
    >
      <StatusIcon tone="ok">✓</StatusIcon>
      <h2 className="center" style={{ margin: 0 }}>
        {krw(r.charge_krw)} 결제됐어요
      </h2>
      {order?.delivery_minutes != null && (
        <p className="center muted" style={{ margin: 0 }}>
          약 {order.delivery_minutes}분 후 도착 예상 (추천 시점 기준)
        </p>
      )}
      <div className="card">
        {order && <OrderRows order={order} hideMethod={!!r.payments?.length} />}
        {r.payments?.map((leg) => (
          <Row key={leg.payment_method_id} label={`결제 · ${leg.name ?? leg.payment_method_id}`} value={krw(leg.charge_krw)} />
        ))}
        <Row label="결제 번호" value={<span className="mono">{r.payment_id}</span>} />
        <Row label="상태" value={<Tag tone="ok">{r.payment_status}</Tag>} />
      </div>
      <YogiyoCart requestId={view.request_id} />
      <p className="center faint">실제 요기요 주문·결제는 하지 않아요 (E 모의결제).</p>
    </Screen>
  );
}

export function Paying({ view }: StepProps) {
  const order = view.result?.order;
  const status = view.result?.payment_status ?? "PENDING";
  return (
    <Screen
      title="결제를 확인하고 있어요"
      step={view.step}
      footer={<Button disabled>결제 중에는 취소할 수 없어요</Button>}
    >
      <StatusIcon tone="pri">…</StatusIcon>
      <h2 className="center" style={{ margin: 0, fontSize: 17 }}>
        {view.state === "PAYING" ? "결제를 요청하고 있어요" : "결제 결과를 아직 받지 못했어요"}
      </h2>
      <p className="center muted">중복 결제를 막기 위해 다시 결제하지 않고 결과를 확인하는 중이에요. 이 화면을 닫아도 돼요.</p>
      <div className="card">
        {order && <Row label="주문" value={`${order.restaurant_name} · ${krw(order.charge_krw)}`} />}
        <Row label="결제 상태" value={<Tag tone="pri">{status}</Tag>} />
        <Row label="자동 새로고침" value="3초마다" />
      </div>
    </Screen>
  );
}

export function Blocked({ view }: StepProps) {
  const nav = useNavigate();
  const b = view.blocked!;
  const details = b.details as Record<string, unknown>;
  // D-02 details: {charge_krw, limit_krw} (예산 초과일 때)
  const num = (k: string) => (typeof details[k] === "number" ? (details[k] as number) : null);
  const actual = num("charge_krw");
  const limit = num("limit_krw");
  const msg = typeof details.message === "string" ? details.message : null;
  return (
    <Screen
      title="주문을 멈췄어요"
      step={view.step}
      danger
      footer={<Button onClick={() => nav("/")}>조건 바꿔 다시 주문</Button>}
    >
      <StatusIcon tone="bad">!</StatusIcon>
      <h2 className="center" style={{ margin: 0, fontSize: 17 }}>
        맡긴 조건을 벗어나서 결제하지 않았어요
      </h2>
      {b.reason_codes.map((code) => (
        <div className="card" key={code}>
          <div className="title-row">
            <b>{BLOCK_REASON[code] ?? "진행할 수 없는 주문"}</b>
          </div>
          {actual !== null && limit !== null && (
            <span className="muted">
              {krw(actual)} &gt; 한도 {krw(limit)}
            </span>
          )}
        </div>
      ))}
      {msg && <p className="muted">{msg}</p>}
    </Screen>
  );
}

export function Failed({ view }: StepProps) {
  const nav = useNavigate();
  const cancelled = view.state === "CANCELLED";
  const e = view.error;
  const title = cancelled ? "주문을 취소했어요" : e?.message ?? (e ? FAIL_TEXT[e.code] : null) ?? "주문하지 못했어요.";
  return (
    <Screen
      title={cancelled ? "주문 취소" : "주문하지 못했어요"}
      step={view.step}
      danger={!cancelled}
      footer={<Button onClick={() => nav("/")}>다시 주문</Button>}
    >
      <StatusIcon tone={cancelled ? "pri" : "bad"}>{cancelled ? "–" : "×"}</StatusIcon>
      <h2 className="center" style={{ margin: 0, fontSize: 17 }}>
        {title}
      </h2>
      {/* 오류 코드·서비스 이름은 고객에게 보이지 않는다 (이슈 #66). 관리자 화면(/admin)의 주문 상세에서 본다 */}
      {e && reasonTexts(e.reason_codes).length > 0 && (
        <div className="card">
          {reasonTexts(e.reason_codes).map((t) => (
            <span className="muted" key={t}>
              · {t}
            </span>
          ))}
        </div>
      )}
      {e?.excluded && e.excluded.length > 0 && (
        <div className="card">
          <b className="small">쓸 수 없던 결제수단</b>
          {e.excluded.map((x) => (
            <span className="muted" key={x.payment_method_id}>
              {x.name} · {x.reason_codes.map((c) => EXCLUDE_REASON[c] ?? c).join(", ")}
            </span>
          ))}
        </div>
      )}
    </Screen>
  );
}

// 사유 코드 → 고객 문장 (모르는 코드는 보이지 않는다)
function reasonTexts(codes: string[] | undefined | null): string[] {
  const out: string[] = [];
  for (const c of codes ?? []) {
    const text = BLOCK_REASON[c] ?? EXCLUDE_REASON[c];
    if (text && !out.includes(text)) out.push(text);
  }
  return out;
}
