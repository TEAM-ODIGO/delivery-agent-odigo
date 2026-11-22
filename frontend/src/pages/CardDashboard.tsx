// 내 카드 혜택 화면 (로컬 확장, 팀 합의 전): 결제 중이 아닐 때 NFC 카드를 대면 그 카드의 대시보드를 보여 준다.
//  - 안드로이드 크롬: 첫 화면에서 카드를 읽고 state.cardTap으로 넘어온다
//  - 아이폰(및 스캔 중이 아닌 안드로이드): 카드의 URL(/tap?t=토큰)이 열리고, 기다리는 주문이 없으면 /card?t=토큰으로 온다
import { useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { CardDashboard, CardTap, RecentPayment } from "../api/types";
import { Banner, Button, Modal, Row, Screen, Spinner, Tag } from "../components/ui";
import { krw, when } from "../lib/format";
import { useToast } from "../lib/hooks";

export default function CardDashboardPage() {
  const nav = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  // 카드 정보는 처음 들어올 때 한 번만 읽는다 (환불 뒤 다시 불러올 때도 같은 값을 쓴다)
  const [cardTap] = useState<CardTap | null>(() => {
    const fromState = (location.state as { cardTap?: CardTap } | null)?.cardTap;
    if (fromState) return fromState;
    const t = params.get("t");
    return t ? { token: t } : null;
  });

  const dashboard = useQuery({
    queryKey: ["card-dashboard", cardTap?.token ?? cardTap?.serial_number ?? ""],
    queryFn: () => api.cardDashboard(cardTap!),
    enabled: cardTap !== null,
    retry: false,
    refetchOnWindowFocus: false,
  });

  const home = <Button onClick={() => nav("/", { replace: true })}>처음으로</Button>;

  if (cardTap === null) {
    return (
      <Screen title="내 카드 혜택" back={() => nav("/")} footer={home}>
        <p className="muted">카드 정보가 없어요. 첫 화면에서 카드를 다시 대 주세요.</p>
      </Screen>
    );
  }
  if (dashboard.isLoading) {
    return (
      <Screen title="내 카드 혜택">
        <Spinner label="카드 혜택을 불러오고 있어요…" />
      </Screen>
    );
  }
  if (dashboard.isError || !dashboard.data) {
    const err = dashboard.error;
    return (
      <Screen title="카드 혜택을 불러오지 못했어요" back={() => nav("/")} footer={home}>
        <p className="muted">{err instanceof ApiError ? err.message : "잠시 후 다시 시도해 주세요."}</p>
      </Screen>
    );
  }

  const d = dashboard.data;
  return (
    <Screen
      title="내 카드 혜택"
      sub="카드를 대서 열었어요"
      back={() => nav("/")}
      footer={<Button onClick={() => nav("/")}>이 카드로 주문하러 가기</Button>}
    >
      <CardHero d={d} />
      {d.type === "CARD" ? <PerformanceCard d={d} /> : <BalanceCard d={d} />}
      <BenefitLimitCard d={d} />
      <RecentPayments d={d} onRefunded={() => dashboard.refetch()} />
      <BenefitList d={d} />
      <ProductInfo d={d} />
    </Screen>
  );
}

function CardHero({ d }: { d: CardDashboard }) {
  return (
    <div className={`card-hero ${d.type === "CARD" ? "" : "local"}`}>
      <div className="title-row">
        <b>{d.name}</b>
        {d.product.card_kind && <span className="card-hero-kind">{d.product.card_kind}</span>}
      </div>
      {d.product.headline && <span className="card-hero-sub">{d.product.headline}</span>}
    </div>
  );
}

function PerformanceCard({ d }: { d: CardDashboard }) {
  const p = d.performance!;
  // 다음 구간이 있으면 그 문턱까지, 없으면 가장 높은 구간 문턱 기준으로 채운다
  const goal = p.next?.min_krw ?? p.current?.min_krw ?? 1;
  const fill = Math.min(100, Math.round((p.monthly_performance_krw / goal) * 100));
  return (
    <div className="card">
      <div className="title-row">
        <b>이번 달 실적</b>
        {p.current ? <Tag tone="ok">혜택 구간</Tag> : <Tag tone="warn">실적 미달</Tag>}
      </div>
      <span className="big-price">{krw(p.monthly_performance_krw)}</span>
      <div className="meter" aria-label={`다음 구간까지 ${fill}%`}>
        <i style={{ width: `${fill}%` }} />
      </div>
      {p.message && <Banner tone={p.next ? "pri" : "ok"}>{p.message}</Banner>}
      {d.product.tier_label && (
        <div className="tier-list">
          {p.tiers.map((t) => {
            const on = p.current?.min_krw === t.min_krw;
            return (
              <div key={t.min_krw} className={`tier${on ? " on" : ""}`}>
                <span>{(t.min_krw / 10000).toLocaleString("ko-KR")}만원 이상</span>
                <span>
                  {d.product.tier_label} {krw(t.cap_krw)}
                </span>
              </div>
            );
          })}
        </div>
      )}
      {!p.eligible && (
        <Banner tone="warn">
          실적 조건({krw(d.monthly_benefit.min_performance_krw)})을 채우지 못해 지금은 배달 할인이 적용되지 않아요.
        </Banner>
      )}
      {p.basis && <span className="faint">실적 기준: {p.basis}</span>}
    </div>
  );
}

function BalanceCard({ d }: { d: CardDashboard }) {
  return (
    <div className="card">
      <b>남은 잔액</b>
      <span className="big-price">{krw(d.balance_krw)}</span>
      <span className="muted">지역화폐 가맹점에서만 쓸 수 있어요.</span>
    </div>
  );
}

function BenefitLimitCard({ d }: { d: CardDashboard }) {
  const m = d.monthly_benefit;
  if (m.cap_krw === null) return null;
  const left = m.remaining_krw ?? 0;
  const fill = m.cap_krw > 0 ? Math.round((left / m.cap_krw) * 100) : 0;
  return (
    <div className="card">
      <div className="title-row">
        <b>이번 달 남은 배달 할인</b>
        <span className="price">{krw(left)}</span>
      </div>
      <div className="meter ok" aria-label={`남은 한도 ${fill}%`}>
        <i style={{ width: `${fill}%` }} />
      </div>
      <Row label="월 한도" value={krw(m.cap_krw)} />
      <Row label="이번 달 받은 할인" value={krw(m.used_krw)} />
      {left === 0 && <Banner tone="warn">이번 달 배달 할인 한도를 다 썼어요.</Banner>}
    </div>
  );
}

function RecentPayments({ d, onRefunded }: { d: CardDashboard; onRefunded: () => void }) {
  const toast = useToast();
  const [confirm, setConfirm] = useState<RecentPayment | null>(null);
  const refund = useMutation({
    mutationFn: (p: RecentPayment) => api.refund(p.payment_id, d.payment_method_id),
    onSuccess: (res) => {
      setConfirm(null);
      toast(
        res.status === "REFUNDED"
          ? `${krw(res.amount_krw)} 환불했어요.`
          : res.status === "FAILED"
            ? "환불이 거절됐어요."
            : "환불을 접수했어요. 처리 결과는 곧 반영돼요.",
      );
      onRefunded();
    },
    onError: (e) => {
      setConfirm(null);
      toast(e instanceof ApiError ? e.message : "환불 요청을 보내지 못했어요.");
    },
  });

  return (
    <>
      <div className="section-title">최근 결제</div>
      {d.recent_payments.length === 0 && <div className="card soft muted">이 카드로 결제한 내역이 아직 없어요.</div>}
      {d.recent_payments.map((p) => (
        <div className="card pay-row" key={p.payment_id}>
          <div className="title-row">
            <b>{p.restaurant_name ?? p.message ?? "배달 주문"}</b>
            <span className="price">{krw(p.share_krw)}</span>
          </div>
          <div className="title-row">
            <span className="muted">
              {when(p.approved_at ?? p.created_at ?? new Date().toISOString())}
              {p.split && ` · 분할결제(전체 ${krw(p.charge_krw)})`}
            </span>
            <PaymentTag p={p} />
          </div>
          {p.refundable_krw > 0 && (
            <button className="link-btn small refund-btn" disabled={refund.isPending} onClick={() => setConfirm(p)}>
              환불하기
            </button>
          )}
        </div>
      ))}
      {confirm && (
        <Modal
          label="환불 확인"
          actions={
            <>
              <Button kind="secondary" onClick={() => setConfirm(null)} disabled={refund.isPending}>
                아니오
              </Button>
              <Button onClick={() => refund.mutate(confirm)} disabled={refund.isPending}>
                {refund.isPending ? "환불 요청 중…" : "환불할게요"}
              </Button>
            </>
          }
        >
          <b>{krw(confirm.refundable_krw)}을 환불할까요?</b>
          <span className="muted">
            {confirm.restaurant_name ?? "이 주문"} 결제를 취소해요.
            {confirm.split && " 지역화폐로 낸 몫도 함께 돌려받아요."}
          </span>
        </Modal>
      )}
    </>
  );
}

function PaymentTag({ p }: { p: RecentPayment }) {
  if (p.status === "APPROVED" && p.refunded_krw > 0) {
    return p.refundable_krw > 0 ? <Tag tone="warn">부분 환불</Tag> : <Tag tone="gray">환불 완료</Tag>;
  }
  if (p.status === "APPROVED") return <Tag tone="ok">결제 완료</Tag>;
  if (p.status === "UNKNOWN" || p.status === "PENDING") return <Tag tone="warn">확인 중</Tag>;
  return <Tag tone="bad">결제 실패</Tag>;
}

function BenefitList({ d }: { d: CardDashboard }) {
  const list = d.product.benefits ?? [];
  if (list.length === 0) return null;
  return (
    <>
      <div className="section-title">카드 혜택</div>
      <div className="card">
        {list.map((b) => (
          <div className="benefit-row" key={b.title}>
            <div className="title-row">
              <span>
                <b>{b.title}</b> {b.delivery && <Tag tone="pri">배달</Tag>}
              </span>
              <span className="benefit-rate">{b.rate}</span>
            </div>
            <span className="muted">{b.detail}</span>
          </div>
        ))}
      </div>
    </>
  );
}

function ProductInfo({ d }: { d: CardDashboard }) {
  const p = d.product;
  if (!p.annual_fee && !p.issuance && !(p.notes ?? []).length) return null;
  return (
    <div className="card soft">
      {p.annual_fee && <Row label="연회비" value={p.annual_fee} />}
      {p.issuance && <span className="small warn-text">{p.issuance}</span>}
      {(p.notes ?? []).map((n) => (
        <span className="small muted" key={n}>
          · {n}
        </span>
      ))}
      {p.source_url && (
        <a className="nav-link small" href={p.source_url} target="_blank" rel="noreferrer">
          신한카드 공식 안내 보기 ›{d.checked_at && <span className="faint"> ({d.checked_at} 확인)</span>}
        </a>
      )}
    </div>
  );
}
