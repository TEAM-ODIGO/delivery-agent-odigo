// 결제 한도·자동 주문 설정 (이슈 #66): 예전 주문 화면의 'Agent에게 맡길 범위'를 ☰ 메뉴로 옮겼다.
// 예) 자녀가 부모님 계정에 한도(3만원)와 쓸 결제수단을 정해 두면, 부모님은 한 문장으로 주문한다.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, getUserToken } from "../api/client";
import type { Action, Delegation, PaymentMethod } from "../api/types";
import { CardImage } from "../components/CardImage";
import { Button, Screen } from "../components/ui";
import { ACTION_LABEL, krw } from "../lib/format";
import { ALL_ACTIONS, loadDelegation, saveDelegation } from "../lib/delegation";
import { useToast } from "../lib/hooks";

export default function Settings() {
  const nav = useNavigate();
  const toast = useToast();
  const methods = useQuery({ queryKey: ["payment-methods", getUserToken()], queryFn: api.paymentMethods });
  return (
    <DelegationEditor
      initial={loadDelegation()}
      methods={methods.data?.results ?? []}
      loadingMethods={methods.isLoading}
      onBack={() => nav(-1)}
      onApply={(d) => {
        saveDelegation(d);
        toast("저장했어요. 다음 주문부터 적용돼요.");
        nav("/");
      }}
    />
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
      title="결제 한도·자동 주문 설정"
      sub="다음 주문부터 적용돼요 (진행 중인 주문은 그대로)"
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
      <div className="section-title">오디고가 대신 해도 되는 일</div>
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
        <div className="banner warn">결제 요청을 끄면 주문안까지만 만들고 결제 전에 멈춰요.</div>
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
          <input
            className="round"
            type="checkbox"
            checked={isOn(m.payment_method_id)}
            onChange={() => toggleMethod(m.payment_method_id)}
          />
          <CardImage id={m.payment_method_id} name={m.name} type={m.type} long={64} />
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
