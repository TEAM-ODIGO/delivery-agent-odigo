// 아이폰 NFC 결제 (로컬 확장): 카드를 태그하면 iOS가 카드의 URL(/tap?t=토큰)을 Safari로 연다.
// 이 화면은 승인 화면에서 '카드 태그'를 누른 주문을 찾아, 그 토큰으로 결제(O-02 APPROVE)를 보낸다.
// 기다리는 주문이 없으면(결제 중이 아니면) 첫 화면(/?card_t=토큰)으로 보내 카드 모달을 띄운다.
import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { Button, Screen, Spinner } from "../components/ui";
import { clearPendingTap, loadPendingTap, savePendingTap } from "../lib/nfc";

async function payWithToken(requestId: string, token: string): Promise<void> {
  const run = await api.getRun(requestId);
  if (run.state !== "AWAITING_APPROVAL") {
    return; // 이미 결제가 시작됐거나 끝난 주문: 주문 화면으로만 보낸다
  }
  await api.reply(requestId, run.state_version, { type: "APPROVE", card_tap: { token } });
}

export default function Tap() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("t") || "";
  const [error, setError] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);
  const started = useRef(false); // 개발 모드에서 effect가 두 번 돌아도 결제는 한 번만 보낸다

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    if (!token) {
      setError("카드에 결제 정보가 없어요. 카드에 쓴 URL 끝에 ?t=토큰이 있는지 확인해 주세요.");
      return;
    }
    const pending = loadPendingTap();
    if (!pending) {
      // 결제 중이 아닐 때 카드를 댔다 → 첫 화면으로 가서 카드 모달을 띄운다 (로컬 확장)
      nav(`/?card_t=${encodeURIComponent(token)}`, { replace: true });
      return;
    }
    setRequestId(pending);
    clearPendingTap(); // 같은 카드를 또 대도 두 번 결제하지 않게 먼저 지운다
    payWithToken(pending, token)
      .then(() => nav(`/runs/${pending}`, { replace: true }))
      .catch((err) => {
        savePendingTap(pending); // 다른 카드를 댄 경우 등: 맞는 카드로 다시 태그할 수 있게 되돌린다
        setError(err instanceof ApiError ? err.message : "결제 요청을 보내지 못했어요.");
      });
  }, [token, nav]);

  if (!error) {
    return (
      <Screen title="카드 결제 중">
        <Spinner label="NFC 카드로 결제하고 있어요…" />
      </Screen>
    );
  }
  return (
    <Screen
      title="카드 결제를 하지 못했어요"
      footer={
        requestId ? (
          <Button onClick={() => nav(`/runs/${requestId}`, { replace: true })}>주문 화면으로</Button>
        ) : (
          <Button onClick={() => nav("/", { replace: true })}>처음으로</Button>
        )
      }
    >
      <p className="muted">{error}</p>
    </Screen>
  );
}
