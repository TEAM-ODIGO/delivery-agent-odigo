// 01 주문 입력 (결제 한도·자동 주문 설정은 ☰ 메뉴 → /settings, 이슈 #66)
import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, DEMO_ADDRESS } from "../api/client";
import type { CardTap } from "../api/types";
import { Button, Screen, Tag } from "../components/ui";
import CardTapEntry from "../components/CardTapEntry";
import CardFlipModal from "../components/CardFlipModal";
import { DEMO_TAGS, FAIL_TEXT, krw } from "../lib/format";
import { loadDelegation } from "../lib/delegation";
import { rememberMessage } from "../lib/lastMessage";
import { useToast } from "../lib/hooks";

// 예시 (이슈 #64): 수집 데이터로 점심·저녁·밤 10시 모두 조건에 딱 맞는 가게가 3곳 나오는 문장
// (scripts/check_queries.py로 확인). 맛·메뉴 조합·브랜드·인원을 한 번씩 보여 준다
const EXAMPLES = ["둘이서 안 매운 걸로 3만원", "둘이서 짜장면 탕수육 3만원", "혼자 베라 2만원", "넷이서 치킨 6만원"];
export default function NewOrder() {
  // 후보 화면에서 [이전]으로 돌아오면 방금 쓴 문장을 다시 채운다 (이슈 #68)
  const location = useLocation();
  const [message, setMessage] = useState(() => (location.state as { message?: string } | null)?.message ?? "");
  // 결제 한도·자동 주문 설정은 ☰ 메뉴(/settings)에서 바꾼다 (이슈 #66). 여기서는 저장된 값을 쓰기만 한다
  const [delegation] = useState(loadDelegation);
  const toast = useToast();
  // 시연 태그가 이 서버 구성에서 동작할 때만 '시연 시나리오 넣기'를 보여 준다 (이슈 #46)
  const demoTags = useQuery({ queryKey: ["demo-tags"], queryFn: api.demoTags, staleTime: 60_000 });
  const nav = useNavigate();
  const qc = useQueryClient();
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
      <textarea
        className="input"
        placeholder="예) 둘이서 안 매운 걸로 3만원"
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

      {delegation.max_charge_krw ? (
        <button className="limit-line" onClick={() => nav("/settings")}>
          결제 한도 {krw(delegation.max_charge_krw)} · 바꾸기 ›
        </button>
      ) : null}

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
    </Screen>
  );
}
