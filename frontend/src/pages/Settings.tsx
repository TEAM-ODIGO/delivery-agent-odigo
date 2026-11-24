// 내 카드·주문 설정 (이슈 #66, 이름 변경 #72): 예전 주문 화면의 'Agent에게 맡길 범위'를 ☰ 메뉴로 옮겼다.
// 예) 자녀가 부모님 계정에 한도(3만원)와 쓸 결제수단을 정해 두면, 부모님은 한 문장으로 주문한다.
import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, getUserToken } from "../api/client";
import type { Action, Delegation, PaymentMethod } from "../api/types";
import { CardImage } from "../components/CardImage";
import CardFlipModal from "../components/CardFlipModal";
import { Button, Screen } from "../components/ui";
import { ACTION_LABEL, krw } from "../lib/format";
import { ALL_ACTIONS, loadDelegation, saveDelegation } from "../lib/delegation";
import { useToast } from "../lib/hooks";

export default function Settings() {
  const nav = useNavigate();
  const toast = useToast();
  const methods = useQuery({
    queryKey: ["payment-methods", getUserToken()],
    queryFn: api.paymentMethods,
  });
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
  const [limit, setLimit] = useState(
    initial.max_charge_krw ? String(initial.max_charge_krw) : "",
  );
  const [picked, setPicked] = useState<string[] | null>(
    initial.allowed_payment_method_ids,
  );
  const all = methods.map((m) => m.payment_method_id);
  const isOn = (id: string) => picked === null || picked.includes(id);
  const toggleMethod = (id: string) => {
    const cur = picked ?? all;
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    setPicked(next.length === all.length ? null : next);
  };
  const toggleAction = (a: Action) =>
    setActions((cur) =>
      cur.includes(a)
        ? cur.filter((x) => x !== a)
        : ALL_ACTIONS.filter((x) => x === a || cur.includes(x)),
    );
  const amount = limit ? Number(limit.replace(/[^0-9]/g, "")) : null;
  const noMethod = picked !== null && picked.length === 0;
  // 카드 옆 ⋮ 를 누르면 그 카드의 혜택 모달 (첫 화면 NFC 태그 때와 같은 모달)
  const [benefitOf, setBenefitOf] = useState<string | null>(null);
  const closeBenefit = useCallback(() => setBenefitOf(null), []);

  return (
    <Screen
      title="내 카드·주문 설정"
      sub="다음 주문부터 적용돼요 (진행 중인 주문은 그대로)"
      back={onBack}
      footer={
        <Button
          disabled={noMethod}
          onClick={() =>
            onApply({
              allowed_actions: actions,
              max_charge_krw: amount || null,
              allowed_payment_method_ids: picked,
            })
          }
        >
          적용
        </Button>
      }
    >
      {/* 메뉴 이름 "내 카드·주문 설정" 순서대로 두 묶음 (이슈 #72). 저장은 아래 '적용' 하나로 함께 */}
      <section className="settings-group">
        <div>
          <h2 className="group-title">오디고가 쓸 카드</h2>
          <p className="group-sub">체크한 카드로만 결제해요</p>
        </div>
        {loadingMethods && <span className="muted">불러오는 중…</span>}
        {methods.map((m) => (
          <label className="pm-row" key={m.payment_method_id}>
            <input
              className="round"
              type="checkbox"
              checked={isOn(m.payment_method_id)}
              onChange={() => toggleMethod(m.payment_method_id)}
            />
            <CardImage
              id={m.payment_method_id}
              name={m.name}
              type={m.type}
              long={64}
            />
            <span>
              <b>{m.name}</b>
              <span className="muted">
                {m.type === "LOCAL_CURRENCY" ? "지역화폐" : "카드"}
                {m.balance_krw !== null && ` · 잔액 ${krw(m.balance_krw)}`}
                {m.monthly_benefit_remaining_krw !== null &&
                  ` · 이번 달 남은 혜택 ${krw(m.monthly_benefit_remaining_krw)}`}
              </span>
            </span>
            <button
              type="button"
              className="pm-more"
              aria-label={`${m.name} 혜택 보기`}
              onClick={(e) => {
                e.preventDefault(); // 줄(label)을 누른 것으로 처리돼 선택이 바뀌지 않게
                e.stopPropagation();
                setBenefitOf(m.payment_method_id);
              }}
            >
              <svg
                width="20"
                height="20"
                viewBox="0 0 20 20"
                aria-hidden="true"
              >
                <circle cx="10" cy="4" r="1.8" />
                <circle cx="10" cy="10" r="1.8" />
                <circle cx="10" cy="16" r="1.8" />
              </svg>
            </button>
          </label>
        ))}
        {noMethod && (
          <div className="banner bad">결제수단을 하나 이상 골라 주세요.</div>
        )}
        <p className="muted">
          모두 선택하면 제한 없음. 실적·잔액이 안 맞는 수단은 결제 단계에서
          빠져요.
        </p>
      </section>

      <section className="settings-group">
        <div>
          <h2 className="group-title">오디고에게 맡길 범위</h2>
          <p className="group-sub">켜 둔 일만 오디고가 대신 해요</p>
        </div>
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
          <div className="banner warn">
            결제 요청을 끄면 주문안까지만 만들고 결제 전에 멈춰요.
          </div>
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
      </section>
      {benefitOf && (
        <CardFlipModal paymentMethodId={benefitOf} onClose={closeBenefit} />
      )}
    </Screen>
  );
}
