// 주문 1건 진행 화면: O-03 상태(state)에 맞는 화면을 그리고, 사용자 응답은 O-02로 보낸다.
import { useParams, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { Reply, RunView } from "../api/types";
import { Button, Screen, Spinner } from "../components/ui";
import { TRANSIENT } from "../lib/format";
import { useToast } from "../lib/hooks";
import { recallMessage } from "../lib/lastMessage";
import Clarify from "../screens/Clarify";
import Candidates from "../screens/Candidates";
import PaymentSelect from "../screens/PaymentSelect";
import Approval from "../screens/Approval";
import { Blocked, Done, Failed, Paying } from "../screens/Results";

export interface StepProps {
  view: RunView;
  busy: boolean;
  send: (reply: Reply) => void;
  cancel: () => void; // 확인창을 거쳐 취소
  decline: () => void; // 사용자가 팝업에서 "아니오" → 확인창 없이 취소하고 처음 화면으로
  back: () => void; // [이전] (이슈 #68): 이 주문을 취소하고 같은 문장이 채워진 입력 화면으로
  again: () => void; // [다른 가게 추천] (이슈 #68): 지금까지 보여 준 가게를 빼고 다시 추천
}

export default function RunPage() {
  const { requestId = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const key = ["run", requestId];

  const run = useQuery({
    queryKey: key,
    queryFn: () => api.getRun(requestId),
    // O가 혼자 넘어가는 상태(결제 확인 중 등)는 주기적으로 다시 본다
    refetchInterval: (q) => {
      const s = q.state.data?.state;
      if (!s) return false;
      if (s === "PAYMENT_UNKNOWN") return 3000;
      return TRANSIENT.includes(s) ? 1500 : false;
    },
  });

  const reply = useMutation({
    mutationFn: (r: Reply) => api.reply(requestId, run.data!.state_version, r),
    onSuccess: (view) => qc.setQueryData(key, view),
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) {
        toast(e.message || "화면이 최신 상태가 아니에요. 다시 불러올게요.");
      } else if (e instanceof ApiError && e.code === "DEPENDENCY_UNAVAILABLE") {
        toast("서비스에 일시적인 문제가 있어요. 잠시 후 다시 시도해 주세요.");
      } else {
        toast(e instanceof ApiError ? e.message : "요청을 보내지 못했어요.");
      }
      qc.invalidateQueries({ queryKey: key });
    },
  });

  if (run.isLoading) {
    return (
      <Screen title="주문 불러오는 중">
        <Spinner label="잠시만요…" />
      </Screen>
    );
  }
  if (run.isError || !run.data) {
    const notFound = run.error instanceof ApiError && run.error.status === 404;
    return (
      <Screen title="주문을 열 수 없어요" footer={<Button onClick={() => nav("/")}>새 주문</Button>}>
        <p className="muted">{notFound ? "없는 주문이거나 다른 사용자의 주문이에요." : "서버에 연결할 수 없어요."}</p>
      </Screen>
    );
  }

  const view = run.data;
  const props: StepProps = {
    view,
    busy: reply.isPending,
    send: (r) => reply.mutate(r),
    cancel: () => {
      if (window.confirm("주문을 취소할까요?")) reply.mutate({ type: "CANCEL" });
    },
    decline: () => reply.mutate({ type: "CANCEL" }, { onSuccess: () => nav("/") }),
    back: () =>
      reply.mutate(
        { type: "CANCEL" },
        { onSuccess: () => nav("/", { state: { message: recallMessage(requestId) } }) },
      ),
    again: () =>
      reply.mutate(
        { type: "RECOMMEND_AGAIN" },
        // 새 후보로 요기요 확인을 다시 시작했으니 확인 결과도 새로 받는다
        { onSuccess: () => qc.invalidateQueries({ queryKey: ["live-check", requestId] }) },
      ),
  };

  switch (view.state) {
    case "NEED_CLARIFICATION":
      return <Clarify {...props} />;
    case "CANDIDATES_READY":
      return <Candidates {...props} />;
    case "PAYMENT_METHOD_SELECTION":
      return <PaymentSelect {...props} />;
    case "AWAITING_APPROVAL":
      return <Approval {...props} />;
    case "PAYING":
    case "PAYMENT_UNKNOWN":
      return <Paying {...props} />;
    case "COMPLETED":
      return <Done {...props} />;
    case "BLOCKED":
      return <Blocked {...props} />;
    case "FAILED":
    case "CANCELLED":
      return <Failed {...props} />;
    default:
      return (
        <Screen title="처리 중이에요" step={view.step}>
          <Spinner label={`${view.step.label} 중…`} />
        </Screen>
      );
  }
}
