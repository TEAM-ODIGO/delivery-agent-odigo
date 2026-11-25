// 01 주문 입력 (결제 한도·자동 주문 설정은 ☰ 메뉴 → /settings, 이슈 #66)
import { Fragment, useEffect, useState, type ReactNode } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, DEMO_ADDRESS, getUserToken } from "../api/client";
import { Button, Screen } from "../components/ui";
import { FAIL_TEXT, krw } from "../lib/format";
import { ALL_ACTIONS } from "../lib/delegation";
import type { Action } from "../api/types";
import { loadDelegation } from "../lib/delegation";
import { rememberMessage } from "../lib/lastMessage";
import { useToast } from "../lib/hooks";

// 예시 (이슈 #64): 수집 데이터로 점심·저녁·밤 10시 모두 조건에 딱 맞는 가게가 3곳 나오는 문장
// (scripts/check_queries.py로 확인). 맛·메뉴 조합·브랜드·인원을 한 번씩 보여 준다
const EXAMPLES = [
  { text: "둘이서 안 매운 걸로 3만원", img: "korean" },
  { text: "둘이서 짜장면 탕수육 3만원", img: "chinese" },
  { text: "혼자 베라 2만원", img: "cafe" },
  { text: "넷이서 치킨 6만원", img: "chicken" },
];
export default function NewOrder() {
  // 후보 화면에서 [이전]으로 돌아오면 방금 쓴 문장을 다시 채운다 (이슈 #68)
  const location = useLocation();
  const [message, setMessage] = useState(() => (location.state as { message?: string } | null)?.message ?? "");
  // 결제 한도·자동 주문 설정은 ☰ 메뉴(/settings)에서 바꾼다 (이슈 #66). 여기서는 저장된 값을 쓰기만 한다
  const [delegation] = useState(loadDelegation);
  const toast = useToast();
  const nav = useNavigate();
  const qc = useQueryClient();
  // 결제 중이 아닐 때 카드를 댄 경우 (아이폰: 카드 URL → /tap → 여기로 온다): 안내만 한다 (이슈 #83).
  // 카드 혜택은 ☰ 메뉴 '내 카드·주문 설정'의 카드 옆 ⋮ 로 본다
  const [params, setParams] = useSearchParams();
  useEffect(() => {
    if (!params.has("tap") && !params.has("card_t")) return;
    toast("카드는 결제할 때 대 주세요. 카드 혜택은 ☰ 메뉴 '내 카드·주문 설정'에서 볼 수 있어요.");
    setParams({}, { replace: true }); // 주소창·방문 기록에 남기지 않는다
  }, [params, setParams, toast]);

  // ☰ 메뉴 맨 아래 '테스트 시나리오'에서 고른 태그를 문장 끝에 붙인다 (이슈 #101)
  const appendTag = (location.state as { appendTag?: string } | null)?.appendTag;
  useEffect(() => {
    if (!appendTag) return;
    setMessage((m) => (m.includes(appendTag) ? m : `${m.trim()} ${appendTag}`.trim()));
    nav(".", { replace: true, state: null });
  }, [appendTag, nav]);

  const start = useMutation({
    mutationFn: () => api.startRun(message.trim(), delegation),
    onSuccess: (view) => {
      qc.setQueryData(["run", view.request_id], view);
      rememberMessage(view.request_id, message.trim());
      nav(`/runs/${view.request_id}`);
    },
    onError: (e) => toast(e instanceof ApiError ? FAIL_TEXT[e.code] ?? e.message : "주문을 시작하지 못했어요."),
  });

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
      {/* 배달 주소: 배달 앱처럼 맨 위 위치 줄로 (이슈 #101) */}
      <div className="addr-bar" aria-label="배달 주소">
        <span className="addr-pin" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="18" height="18">
            <path d="M12 2a7 7 0 0 0-7 7c0 5 7 13 7 13s7-8 7-13a7 7 0 0 0-7-7zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z" fill="currentColor" />
          </svg>
        </span>
        <span className="addr-text">
          <span className="addr-label">배달 주소</span>
          <b>{DEMO_ADDRESS.label.replace(/\s*\(.*\)$/, "")}</b>
          {DEMO_ADDRESS.label.match(/\((.*)\)$/)?.[1] && <span className="addr-sub">{DEMO_ADDRESS.label.match(/\((.*)\)$/)?.[1]}</span>}
        </span>
      </div>
      <textarea
        className="input"
        placeholder="예) 둘이서 안 매운 걸로 3만원"
        value={message}
        maxLength={300}
        onChange={(e) => setMessage(e.target.value)}
        aria-label="주문 문장"
      />
      <div className="counter">{len} / 300자</div>
      <div className="muted">이렇게 말해 보세요</div>
      {/* 예시: 음식 사진 칩, 2줄까지만 보이고 옆으로 넘겨 본다 (이슈 #101) */}
      <div className="example-rail">
        <div className="example-grid" role="list">
        {EXAMPLES.map((ex) => (
          <button key={ex.text} role="listitem" className="chip example-chip" onClick={() => setMessage(ex.text)}>
            <img src={`/images/cuisine/${ex.img}.webp`} alt="" aria-hidden="true" loading="lazy" />
            {ex.text}
          </button>
        ))}
        </div>
      </div>

      <DelegationSummary onOpen={() => nav("/settings")} />
    </Screen>
  );
}

// 첫 화면의 'ODIGO에게 맡긴 일' 요약 (이슈 #101): 주문 전에 어디까지 맡겼는지 바로 본다. 누르면 설정으로
function DelegationSummary({ onOpen }: { onOpen: () => void }) {
  const [d] = useState(loadDelegation);
  const methods = useQuery({ queryKey: ["payment-methods", getUserToken()], queryFn: api.paymentMethods, staleTime: 60_000 });
  const total = methods.data?.count;
  const cards =
    d.allowed_payment_method_ids === null
      ? total !== undefined
        ? `사용할 결제수단 ${total}개 모두`
        : "사용할 결제수단 모두"
      : `사용할 결제수단 ${d.allowed_payment_method_ids.length}개`;
  return (
    <button className="card delegation-summary" onClick={onOpen}>
      <div className="title-row">
        <b>ODIGO에게 맡긴 일</b>
        <span className="faint">바꾸기 ›</span>
      </div>
      {/* 찾기 → 주문안 → 결제 단계를 아이콘으로 잇는다. 맡기지 않은 단계는 회색 (이슈 #101) */}
      <div className="delegation-flow">
        {ALL_ACTIONS.map((a, i) => {
          const on = a === "SEARCH" || d.allowed_actions.includes(a); // 가게 찾기는 항상 (이슈 #111)
          const prevOn = i > 0 && (ALL_ACTIONS[i - 1] === "SEARCH" || d.allowed_actions.includes(ALL_ACTIONS[i - 1]));
          return (
            <Fragment key={a}>
              {i > 0 && <i className={`flow-line${on && prevOn ? " on" : ""}`} />}
              <span className={`flow-step${on ? " on" : ""}`}>
                <FlowIcon action={a} on={on} />
                <em>{on ? FLOW_LABEL[a] : `${FLOW_LABEL[a]} 직접`}</em>
              </span>
            </Fragment>
          );
        })}
      </div>
      <span className="muted small">
        결제 한도 {d.max_charge_krw ? krw(d.max_charge_krw) : "없음"} · {cards}
      </span>
    </button>
  );
}

const FLOW_LABEL: Record<Action, string> = { SEARCH: "가게 찾기", BUILD_ORDER: "주문안", REQUEST_PAYMENT: "결제 요청" };

function FlowIcon({ action, on }: { action: Action; on: boolean }) {
  const paths: Record<Action, ReactNode> = {
    SEARCH: (
      <>
        <circle cx="11" cy="11" r="6" />
        <path d="M20 20l-4.5-4.5" />
      </>
    ),
    BUILD_ORDER: (
      <>
        <rect x="5" y="4" width="14" height="17" rx="2" />
        <path d="M9 9h6M9 13h6M9 17h3" />
      </>
    ),
    REQUEST_PAYMENT: on ? (
      <>
        <rect x="3" y="6" width="18" height="12" rx="2" />
        <path d="M3 10h18" />
      </>
    ) : (
      <>
        <rect x="6" y="11" width="12" height="9" rx="2" />
        <path d="M9 11V8a3 3 0 0 1 6 0v3" />
      </>
    ),
  };
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[action]}
    </svg>
  );
}
