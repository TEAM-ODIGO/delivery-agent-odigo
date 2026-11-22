// 첫 화면에서 NFC 카드를 대면 뜨는 카드 모달 (로컬 확장, 팀 합의 전).
// 앞면(세로형 카드)이 먼저 보이고, 혜택을 다 불러오면 카드가 뒤집히면서 뒷면에 내 카드 혜택이 나온다.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { ApiError } from "../api/client";
import type { CardDashboard, CardTap } from "../api/types";
import { CardDashboardSections, useCardDashboard } from "../pages/CardDashboard";
import { Button } from "./ui";
import "./CardFlipModal.css";

const FLIP_DELAY_MS = 450; // 앞면을 잠깐 보여 준 뒤 뒤집는다 (같은 카드라 바로 불러와도 뒤집히는 모습이 보이게)

// 상품마다 카드 앞면 색 (시연용 디자인, 실물 카드 디자인이 아님)
const FACE_TONE: Record<string, string> = {
  "PRD-CARD-A": "tone-a",
  "PRD-CARD-B": "tone-b",
  "PRD-CARD-C": "tone-c",
  "PRD-LOCAL-A": "tone-local",
};

export default function CardFlipModal({ cardTap, onClose }: { cardTap: CardTap; onClose: () => void }) {
  const dashboard = useCardDashboard(cardTap);
  const d = dashboard.data;
  const [flipped, setFlipped] = useState(false);

  // 다 불러오면 잠깐 뒤에 뒤집는다
  useEffect(() => {
    if (!d) return;
    const t = setTimeout(() => setFlipped(true), FLIP_DELAY_MS);
    return () => clearTimeout(t);
  }, [d]);

  // 모달이 떠 있는 동안 뒤 화면이 스크롤되지 않게 하고, Esc로 닫는다
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

  const error = dashboard.isError
    ? dashboard.error instanceof ApiError
      ? dashboard.error.message
      : "카드 혜택을 불러오지 못했어요."
    : null;

  return createPortal(
    <div className="flip-backdrop" onClick={onClose}>
      <div
        className="flip-scene"
        role="dialog"
        aria-modal="true"
        aria-label="내 카드 혜택"
        onClick={(e) => e.stopPropagation()} // 카드 안을 눌러도 닫히지 않게
      >
        <div className={`flip-card${flipped ? " flipped" : ""}`}>
          <CardFront d={d} error={error} onClose={onClose} onFlip={d ? () => setFlipped(true) : undefined} />
          <div className="flip-face flip-back" aria-hidden={!flipped}>
            {d && (
              <>
                <div className="flip-back-head">
                  <div>
                    <b>{d.name}</b>
                    {d.product.headline && <span className="muted">{d.product.headline}</span>}
                  </div>
                  <button className="flip-close" onClick={onClose} aria-label="닫기">
                    ×
                  </button>
                </div>
                <div className="flip-back-body">
                  <CardDashboardSections d={d} onRefunded={() => dashboard.refetch()} />
                </div>
                <div className="flip-back-foot">
                  <Button kind="secondary" onClick={() => setFlipped(false)}>
                    앞면
                  </Button>
                  <Button onClick={onClose}>닫고 주문하기</Button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function CardFront({
  d,
  error,
  onClose,
  onFlip,
}: {
  d?: CardDashboard;
  error: string | null;
  onClose: () => void;
  onFlip?: () => void;
}) {
  const tone = d ? FACE_TONE[d.product_id] ?? "tone-a" : "tone-wait";
  return (
    <div className={`flip-face flip-front ${tone}${onFlip ? " clickable" : ""}`} onClick={onFlip}>
      <div className="plate-top">
        <span className="plate-kind">{d?.product.card_kind ?? "NFC 카드"}</span>
        <span className="plate-wave" aria-hidden>
          )))
        </span>
      </div>
      {d?.type !== "LOCAL_CURRENCY" && <div className="plate-chip" aria-hidden />}
      <div className="plate-bottom">
        {error ? (
          <>
            <b className="plate-name">카드를 확인하지 못했어요</b>
            <span className="plate-sub">{error}</span>
            <button className="plate-close" onClick={onClose}>
              닫기
            </button>
          </>
        ) : d ? (
          <>
            <b className="plate-name">{d.name}</b>
            <span className="plate-sub">카드를 누르면 혜택이 보여요</span>
          </>
        ) : (
          <>
            <b className="plate-name">카드를 확인하고 있어요</b>
            <span className="plate-sub">
              <span className="plate-spinner" aria-hidden /> 잠시만요
            </span>
          </>
        )}
      </div>
    </div>
  );
}
