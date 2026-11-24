// 내 카드 관리 · 추가 시트 (로컬 확장, 팀 합의 전).
// 카드를 넣는 길은 두 가지다: ① 모의 마이데이터로 불러오기 ② 직접 입력.
// 진짜 마이데이터가 아니다 (본인신용정보관리업 허가가 있어야 진짜 연동을 할 수 있다). 카드 번호 전체·유효기간·CVC는 받지 않는다.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, getUserToken } from "../api/client";
import type { CardAddBody, MyCard, MyDataCard } from "../api/types";
import { krw } from "../lib/format";
import { useToast } from "../lib/hooks";
import { CardImage } from "./CardImage";
import { Banner, Button, Tag } from "./ui";
import "./MyCards.css";

type View = { name: "list" } | { name: "choose" } | { name: "consent" } | { name: "mydata"; consentId: string } | { name: "manual" };

const SOURCE_LABEL = { MYDATA: "마이데이터", MANUAL: "직접 추가" } as const;
const OTHER = "OTHER";

function message(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback;
}

export default function MyCardsSheet({ onClose }: { onClose: () => void }) {
  const [view, setView] = useState<View>({ name: "list" });
  const qc = useQueryClient();

  // 뒤 화면이 스크롤되지 않게 하고 Esc로 닫는다
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prev;
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  // 카드를 넣거나 뺐으면 결제수단 목록(맡길 범위 화면)도 새로 읽는다
  const refreshCards = () => {
    qc.invalidateQueries({ queryKey: ["my-cards"] });
    qc.invalidateQueries({ queryKey: ["payment-methods"] });
  };

  const titles: Record<View["name"], string> = {
    list: "내 카드",
    choose: "카드 추가",
    consent: "마이데이터로 불러오기",
    mydata: "불러온 카드",
    manual: "직접 입력",
  };
  const back = () => {
    if (view.name === "mydata") setView({ name: "consent" });
    else if (view.name === "consent" || view.name === "manual") setView({ name: "choose" });
    else setView({ name: "list" });
  };

  return createPortal(
    <div className="mc-backdrop" onClick={onClose}>
      <div className="mc-sheet" role="dialog" aria-modal="true" aria-label="내 카드" onClick={(e) => e.stopPropagation()}>
        <div className="mc-head">
          {view.name !== "list" ? (
            <button className="mc-icon" onClick={back} aria-label="뒤로">
              ‹
            </button>
          ) : (
            <span className="mc-icon-gap" />
          )}
          <b>{titles[view.name]}</b>
          <button className="mc-icon" onClick={onClose} aria-label="닫기">
            ×
          </button>
        </div>
        <div className="mc-body">
          {view.name === "list" && <CardList onAdd={() => setView({ name: "choose" })} onChanged={refreshCards} />}
          {view.name === "choose" && <Choose onPick={setView} />}
          {view.name === "consent" && <Consent onConsented={(consentId) => setView({ name: "mydata", consentId })} />}
          {view.name === "mydata" && (
            <MyDataList
              consentId={view.consentId}
              onAgain={() => setView({ name: "consent" })}
              onChanged={refreshCards}
              onDone={() => setView({ name: "list" })}
            />
          )}
          {view.name === "manual" && (
            <Manual
              onAdded={() => {
                refreshCards();
                setView({ name: "list" });
              }}
            />
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ---------------------------------------------------------------- 내 카드 목록
function CardList({ onAdd, onChanged }: { onAdd: () => void; onChanged: () => void }) {
  const toast = useToast();
  const cards = useQuery({ queryKey: ["my-cards", getUserToken()], queryFn: api.myCards });
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const remove = useMutation({
    mutationFn: (id: string) => api.removeCard(id),
    onSuccess: () => {
      setConfirmId(null);
      onChanged();
      toast("카드를 뺐어요.");
    },
    onError: (e) => {
      setConfirmId(null);
      toast(message(e, "카드를 빼지 못했어요."));
    },
  });

  if (cards.isLoading) return <p className="muted">불러오는 중…</p>;
  if (cards.isError) return <Banner tone="bad">{message(cards.error, "내 카드를 불러오지 못했어요.")}</Banner>;
  const list = cards.data?.results ?? [];
  const canAdd = cards.data?.can_add ?? false;

  return (
    <>
      {list.map((m) => (
        <CardRow
          key={m.payment_method_id}
          card={m}
          confirming={confirmId === m.payment_method_id}
          busy={remove.isPending}
          onAskRemove={() => setConfirmId(m.payment_method_id)}
          onCancel={() => setConfirmId(null)}
          onRemove={() => remove.mutate(m.payment_method_id)}
        />
      ))}
      {list.some((m) => !m.has_benefit_rule) && (
        <Banner tone="warn">혜택 규칙이 없는 카드는 목록에는 보이지만, 추천과 결제에는 쓰이지 않아요.</Banner>
      )}
      {!canAdd && (
        <Banner tone="warn">카드 추가는 모의결제 서버(E)를 연결해야 쓸 수 있어요.</Banner>
      )}
      <Button disabled={!canAdd} onClick={onAdd}>
        + 카드 추가
      </Button>
    </>
  );
}

function CardRow({
  card: m,
  confirming,
  busy,
  onAskRemove,
  onCancel,
  onRemove,
}: {
  card: MyCard;
  confirming: boolean;
  busy: boolean;
  onAskRemove: () => void;
  onCancel: () => void;
  onRemove: () => void;
}) {
  const detail =
    m.type === "LOCAL_CURRENCY"
      ? m.balance_krw !== null && `잔액 ${krw(m.balance_krw)}`
      : m.monthly_performance_krw !== null && `이번 달 실적 ${krw(m.monthly_performance_krw)}`;
  return (
    <div className="mc-row">
      <CardImage id={m.payment_method_id} name={m.name} type={m.type} productId={m.product_id} long={64} />
      <div className="mc-row-main">
        <b>{m.name}</b>
        <span className="mc-tags">
          <Tag tone="gray">{m.type === "LOCAL_CURRENCY" ? "지역화폐" : "카드"}</Tag>
          {m.source && <Tag tone="pri">{SOURCE_LABEL[m.source]}</Tag>}
          {!m.has_benefit_rule && <Tag tone="warn">혜택 규칙 없음</Tag>}
        </span>
        {detail && <span className="muted">{detail}</span>}
        {confirming && (
          <span className="mc-confirm">
            <span>이 카드를 뺄까요?</span>
            <button className="mc-mini bad" disabled={busy} onClick={onRemove}>
              빼기
            </button>
            <button className="mc-mini" disabled={busy} onClick={onCancel}>
              취소
            </button>
          </span>
        )}
      </div>
      {m.removable && !confirming && (
        <button className="link" onClick={onAskRemove} aria-label={`${m.name} 빼기`}>
          빼기
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 추가 방법 고르기
function Choose({ onPick }: { onPick: (v: View) => void }) {
  return (
    <>
      <button className="card mc-choice" onClick={() => onPick({ name: "consent" })}>
        <b>마이데이터로 불러오기</b>
        <span className="muted">동의하면 가진 카드 목록을 불러와서 골라 넣어요. (시연용 모의 마이데이터)</span>
      </button>
      <button className="card mc-choice" onClick={() => onPick({ name: "manual" })}>
        <b>직접 입력</b>
        <span className="muted">카드 이름만 골라 넣거나 적어요. 카드 번호는 받지 않아요.</span>
      </button>
    </>
  );
}

// ---------------------------------------------------------------- 마이데이터 동의
function Consent({ onConsented }: { onConsented: (consentId: string) => void }) {
  const toast = useToast();
  const [agreed, setAgreed] = useState(false);
  const consent = useMutation({
    mutationFn: api.mydataConsent,
    onSuccess: (c) => onConsented(c.consent_id),
    onError: (e) => toast(message(e, "동의를 처리하지 못했어요.")),
  });
  return (
    <>
      <Banner tone="pri">
        진짜 마이데이터가 아니에요. 실제 카드사 정보는 가져오지 않고, 시연용으로 만든 카드 목록을 보여 줘요.
      </Banner>
      <div className="card soft">
        <b>가져올 정보</b>
        <ul className="mc-list">
          <li>카드 목록 (카드사, 카드 이름, 끝 4자리)</li>
          <li>이번 달 이용 금액</li>
        </ul>
        <span className="muted">동의는 10분 동안만 유효하고, 카드 번호 전체는 가져오지 않아요.</span>
      </div>
      <label className="mc-agree">
        <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
        <span>위 정보를 불러오는 데 동의해요</span>
      </label>
      <Button disabled={!agreed || consent.isPending} onClick={() => consent.mutate()}>
        {consent.isPending ? "불러오는 중…" : "동의하고 카드 불러오기"}
      </Button>
    </>
  );
}

// ---------------------------------------------------------------- 불러온 카드 고르기
function MyDataList({
  consentId,
  onAgain,
  onChanged,
  onDone,
}: {
  consentId: string;
  onAgain: () => void;
  onChanged: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const cards = useQuery({
    queryKey: ["mydata-cards", getUserToken(), consentId],
    queryFn: () => api.mydataCards(consentId),
    retry: false,
  });
  const add = useMutation({
    mutationFn: (c: MyDataCard) => api.addCard({ source: "MYDATA", consent_id: consentId, mydata_card_id: c.card_id }),
    onSuccess: (res) => {
      onChanged();
      qc.invalidateQueries({ queryKey: ["mydata-cards"] });
      toast(`카드를 추가했어요. (${res.payment_method.name})`);
    },
    onError: (e) => {
      toast(message(e, "카드를 추가하지 못했어요."));
      qc.invalidateQueries({ queryKey: ["mydata-cards"] });
    },
  });

  if (cards.isLoading) return <p className="muted">카드를 불러오는 중…</p>;
  if (cards.isError) {
    const expired = cards.error instanceof ApiError && ["CONSENT_EXPIRED", "CONSENT_NOT_FOUND"].includes(cards.error.code);
    return (
      <>
        <Banner tone="bad">{message(cards.error, "카드를 불러오지 못했어요.")}</Banner>
        {expired && <Button onClick={onAgain}>다시 동의하기</Button>}
      </>
    );
  }
  const list = cards.data?.results ?? [];
  return (
    <>
      <p className="muted">내 카드에 넣을 카드를 골라 주세요.</p>
      {list.map((c) => (
        <div className="mc-row" key={c.card_id}>
          <div className="mc-row-main">
            <b>{c.name}</b>
            <span className="muted">
              {c.issuer} · 끝 {c.last4} · 이번 달 {krw(c.monthly_performance_krw)}
            </span>
            <span className="mc-tags">
              {c.supported ? <Tag tone="ok">혜택 규칙 있음</Tag> : <Tag tone="warn">혜택 규칙 없음</Tag>}
            </span>
          </div>
          {c.already_added ? (
            <span className="muted">이미 있어요</span>
          ) : (
            <button className="mc-mini pri" disabled={add.isPending} onClick={() => add.mutate(c)}>
              추가
            </button>
          )}
        </div>
      ))}
      {list.some((c) => !c.supported && !c.already_added) && (
        <Banner tone="warn">혜택 규칙이 없는 카드는 넣어도 추천과 결제에는 쓰이지 않아요.</Banner>
      )}
      <Button onClick={onDone}>완료</Button>
    </>
  );
}

// ---------------------------------------------------------------- 직접 입력
function Manual({ onAdded }: { onAdded: () => void }) {
  const toast = useToast();
  const catalog = useQuery({ queryKey: ["card-catalog"], queryFn: api.cardCatalog, staleTime: 60_000 });
  const [choice, setChoice] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [issuer, setIssuer] = useState("");
  const [last4, setLast4] = useState("");

  const add = useMutation({
    mutationFn: (body: CardAddBody) => api.addCard(body),
    onSuccess: (res) => {
      toast(`카드를 추가했어요. (${res.payment_method.name})`);
      onAdded();
    },
    onError: (e) => toast(message(e, "카드를 추가하지 못했어요.")),
  });

  const other = choice === OTHER;
  const last4Ok = last4 === "" || /^[0-9]{4}$/.test(last4);
  const ready = choice !== null && (!other || (name.trim().length > 0 && last4Ok));
  const submit = () => {
    if (!ready || choice === null) return;
    if (!other) return add.mutate({ source: "MANUAL", product_id: choice });
    add.mutate({
      source: "MANUAL",
      name: name.trim(),
      ...(issuer.trim() ? { issuer: issuer.trim() } : {}),
      ...(last4 ? { last4 } : {}),
    });
  };

  return (
    <>
      <p className="muted">혜택 규칙이 있는 카드를 고르거나, 다른 카드를 직접 적어요.</p>
      {catalog.isLoading && <p className="muted">불러오는 중…</p>}
      {catalog.isError && <Banner tone="bad">{message(catalog.error, "카드 목록을 불러오지 못했어요.")}</Banner>}
      {catalog.data?.supported.map((p) => (
        <label className="pm-row" key={p.product_id}>
          <input className="round" type="radio" name="mc-card" checked={choice === p.product_id} onChange={() => setChoice(p.product_id)} />
          <span>
            <b>{p.name}</b>
            <span className="muted">혜택 규칙 있음</span>
          </span>
        </label>
      ))}
      <label className="pm-row">
        <input className="round" type="radio" name="mc-card" checked={other} onChange={() => setChoice(OTHER)} />
        <span>
          <b>그 밖의 카드</b>
          <span className="muted">혜택 규칙이 없어서 추천과 결제에는 쓰이지 않아요</span>
        </span>
      </label>
      {other && (
        <div className="mc-form">
          <input className="input" placeholder="카드 이름 (예: 삼성 iD ON 카드)" maxLength={40} value={name} onChange={(e) => setName(e.target.value)} aria-label="카드 이름" />
          <input className="input" placeholder="카드사 (선택)" maxLength={20} value={issuer} onChange={(e) => setIssuer(e.target.value)} aria-label="카드사" />
          <input
            className="input"
            placeholder="카드 끝 4자리 (선택)"
            inputMode="numeric"
            maxLength={4}
            value={last4}
            onChange={(e) => setLast4(e.target.value.replace(/[^0-9]/g, ""))}
            aria-label="카드 끝 4자리"
          />
          {!last4Ok && <span className="mc-err">끝 4자리 숫자만 적어 주세요.</span>}
        </div>
      )}
      <p className="muted">카드 번호 전체·유효기간·CVC는 받지 않아요.</p>
      <Button disabled={!ready || add.isPending} onClick={submit}>
        {add.isPending ? "추가하는 중…" : "추가"}
      </Button>
    </>
  );
}
