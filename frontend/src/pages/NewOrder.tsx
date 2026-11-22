// 01 주문 입력 + 01-1 맡길 범위
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, DEMO_ADDRESS, getUserToken } from "../api/client";
import type { Action, CardTap, Delegation, PaymentMethod } from "../api/types";
import { Button, Screen, Tag } from "../components/ui";
import CardTapEntry from "../components/CardTapEntry";
import CardFlipModal from "../components/CardFlipModal";
import { ACTION_LABEL, DEMO_TAGS, FAIL_TEXT, krw } from "../lib/format";
import { useToast } from "../lib/hooks";

const EXAMPLES = ["둘이서 2만원 이하로 안 매운 치킨 빨리", "혼자 1만5천원 이하 떡볶이", "셋이서 피자 3만원"];
const ALL_ACTIONS: Action[] = ["SEARCH", "BUILD_ORDER", "REQUEST_PAYMENT"];
const DEFAULT_DELEGATION: Delegation = {
  allowed_actions: ALL_ACTIONS,
  allowed_payment_method_ids: null,
  max_charge_krw: null,
};

function loadDelegation(): Delegation {
  try {
    const raw = localStorage.getItem(`agent.delegation.${getUserToken()}`);
    return raw ? { ...DEFAULT_DELEGATION, ...JSON.parse(raw) } : DEFAULT_DELEGATION;
  } catch {
    return DEFAULT_DELEGATION;
  }
}
function saveDelegation(d: Delegation) {
  try {
    localStorage.setItem(`agent.delegation.${getUserToken()}`, JSON.stringify(d));
  } catch {
    /* 저장 못 해도 이번 주문에는 그대로 쓴다 */
  }
}

export default function NewOrder() {
  const [message, setMessage] = useState("");
  const [delegation, setDelegation] = useState<Delegation>(loadDelegation);
  const [editing, setEditing] = useState(false);
  const toast = useToast();
  // 시연 태그가 이 서버 구성에서 동작할 때만 '시연 시나리오 넣기'를 보여 준다 (이슈 #46)
  const demoTags = useQuery({ queryKey: ["demo-tags"], queryFn: api.demoTags, staleTime: 60_000 });
  const nav = useNavigate();
  const qc = useQueryClient();
  const methods = useQuery({ queryKey: ["payment-methods", getUserToken()], queryFn: api.paymentMethods });
  // 결제 중이 아닐 때 댄 NFC 카드 (로컬 확장): 있으면 카드 모달을 띄운다
  const [tappedCard, setTappedCard] = useState<CardTap | null>(null);
  const [params, setParams] = useSearchParams();
  useEffect(() => {
    const token = params.get("card_t"); // 아이폰: 카드 URL → /tap → 여기로 온다
    if (!token) return;
    setTappedCard({ token });
    setParams({}, { replace: true }); // 주소창·방문 기록에 토큰을 남기지 않는다
  }, [params, setParams]);
  const closeCard = useCallback(() => {
    setTappedCard(null);
    qc.invalidateQueries({ queryKey: ["payment-methods"] }); // 환불했으면 남은 혜택 표시가 바뀐다
  }, [qc]);

  const start = useMutation({
    mutationFn: () => api.startRun(message.trim(), delegation),
    onSuccess: (view) => {
      qc.setQueryData(["run", view.request_id], view);
      nav(`/runs/${view.request_id}`);
    },
    onError: (e) => toast(e instanceof ApiError ? FAIL_TEXT[e.code] ?? e.message : "주문을 시작하지 못했어요."),
  });

  if (editing) {
    return (
      <DelegationEditor
        initial={delegation}
        methods={methods.data?.results ?? []}
        loadingMethods={methods.isLoading}
        onBack={() => setEditing(false)}
        onApply={(d) => {
          setDelegation(d);
          saveDelegation(d);
          setEditing(false);
        }}
      />
    );
  }

  const nameOf = (id: string) => methods.data?.results.find((m) => m.payment_method_id === id)?.name ?? id;
  const allowedNames = delegation.allowed_payment_method_ids?.map(nameOf).join(", ");
  const len = [...message].length;

  return (
    <Screen
      title="무엇을 주문할까요?"
      step={{ current: 1, total: 8, label: "요청 접수" }}
      footer={
        <Button disabled={!message.trim() || len > 300 || start.isPending} onClick={() => start.mutate()}>
          {start.isPending ? "가게를 찾는 중…" : "주문 시작"}
        </Button>
      }
    >
      <textarea
        className="input"
        placeholder="예) 둘이서 2만원 이하로 안 매운 치킨 빨리"
        value={message}
        maxLength={300}
        onChange={(e) => setMessage(e.target.value)}
        aria-label="주문 문장"
      />
      <div className="counter">{len} / 300자</div>
      <div className="muted">예시</div>
      <div className="chips">
        {EXAMPLES.map((ex) => (
          <button key={ex} className="chip" onClick={() => setMessage(ex)}>
            {ex}
          </button>
        ))}
      </div>

      <div className="card">
        <div className="title-row">
          <b>배달 주소</b>
          <Tag tone="warn">시연 고정</Tag>
        </div>
        <span className="muted">{DEMO_ADDRESS.label}</span>
      </div>

      <div className="card">
        <div className="title-row">
          <b>Agent에게 맡길 범위</b>
          <button className="link" onClick={() => setEditing(true)}>
            변경 ›
          </button>
        </div>
        <span className="muted">
          {delegation.allowed_actions.map((a) => ACTION_LABEL[a]).join(" · ") || "맡긴 작업 없음"}
          <br />
          {delegation.max_charge_krw ? `최대 ${krw(delegation.max_charge_krw)}` : "금액 제한 없음"} ·{" "}
          {allowedNames ? allowedNames : "결제수단 제한 없음"}
        </span>
      </div>

      <CardTapEntry onCard={setTappedCard} />
      {tappedCard && <CardFlipModal cardTap={tappedCard} onClose={closeCard} />}

      {demoTags.data?.available && (
        <div className="demo">
          <details>
            <summary>시연 시나리오 넣기</summary>
            <div className="chips">
              {DEMO_TAGS.map((t) => (
                <button
                  key={t.tag}
                  className="chip"
                  title={t.desc}
                  onClick={() => setMessage((m) => (m.includes(t.tag) ? m : `${m.trim()} ${t.tag}`.trim()))}
                >
                  {t.tag} <span className="faint">&nbsp;{t.desc}</span>
                </button>
              ))}
            </div>
          </details>
        </div>
      )}
      <Link to="/orders" className="nav-link">
        내 주문 보기 ›
      </Link>
    </Screen>
  );
}

function DelegationEditor({
  initial,
  methods,
  loadingMethods,
  onBack,
  onApply,
}: {
  initial: Delegation;
  methods: PaymentMethod[];
  loadingMethods: boolean;
  onBack: () => void;
  onApply: (d: Delegation) => void;
}) {
  const [actions, setActions] = useState<Action[]>(initial.allowed_actions);
  const [limit, setLimit] = useState(initial.max_charge_krw ? String(initial.max_charge_krw) : "");
  const [picked, setPicked] = useState<string[] | null>(initial.allowed_payment_method_ids);
  const all = methods.map((m) => m.payment_method_id);
  const isOn = (id: string) => picked === null || picked.includes(id);
  const toggleMethod = (id: string) => {
    const cur = picked ?? all;
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    setPicked(next.length === all.length ? null : next);
  };
  const toggleAction = (a: Action) =>
    setActions((cur) => (cur.includes(a) ? cur.filter((x) => x !== a) : ALL_ACTIONS.filter((x) => x === a || cur.includes(x))));
  const amount = limit ? Number(limit.replace(/[^0-9]/g, "")) : null;
  const noMethod = picked !== null && picked.length === 0;

  return (
    <Screen
      title="Agent에게 맡길 범위"
      sub="이번 주문부터 적용돼요"
      back={onBack}
      footer={
        <Button
          disabled={noMethod}
          onClick={() =>
            onApply({ allowed_actions: actions, max_charge_krw: amount || null, allowed_payment_method_ids: picked })
          }
        >
          적용
        </Button>
      }
    >
      <div className="section-title">맡길 작업</div>
      <div>
        {ALL_ACTIONS.map((a) => (
          <div className="toggle-row" key={a}>
            <span>{ACTION_LABEL[a]}</span>
            <button
              className={`switch${actions.includes(a) ? " on" : ""}`}
              role="switch"
              aria-checked={actions.includes(a)}
              aria-label={ACTION_LABEL[a]}
              onClick={() => toggleAction(a)}
            />
          </div>
        ))}
      </div>
      {!actions.includes("REQUEST_PAYMENT") && (
        <div className="banner warn">결제 요청을 맡기지 않으면 승인 단계에서 진행이 멈춰요.</div>
      )}

      <div className="section-title">최대 결제 금액</div>
      <div className="input-suffix">
        <input
          className="input"
          inputMode="numeric"
          placeholder="제한 없음"
          value={amount ? amount.toLocaleString("ko-KR") : ""}
          onChange={(e) => setLimit(e.target.value)}
          aria-label="최대 결제 금액"
        />
        <span>원</span>
      </div>

      <div className="section-title">쓸 수 있는 결제수단</div>
      {loadingMethods && <span className="muted">불러오는 중…</span>}
      {methods.map((m) => (
        <label className="pm-row" key={m.payment_method_id}>
          <input type="checkbox" checked={isOn(m.payment_method_id)} onChange={() => toggleMethod(m.payment_method_id)} />
          <span>
            <b>{m.name}</b>
            <span className="muted">
              {m.type === "LOCAL_CURRENCY" ? "지역화폐" : "카드"}
              {m.balance_krw !== null && ` · 잔액 ${krw(m.balance_krw)}`}
              {m.monthly_benefit_remaining_krw !== null && ` · 이번 달 남은 혜택 ${krw(m.monthly_benefit_remaining_krw)}`}
            </span>
          </span>
        </label>
      ))}
      {noMethod && <div className="banner bad">결제수단을 하나 이상 골라 주세요.</div>}
      <p className="muted">모두 선택하면 제한 없음. 실적·잔액이 안 맞는 수단은 결제 단계에서 빠져요.</p>
    </Screen>
  );
}
