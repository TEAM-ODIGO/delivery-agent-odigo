import type { RunState } from "../api/types";

export const krw = (n: number | null | undefined) =>
  n === null || n === undefined ? "—" : `${n.toLocaleString("ko-KR")}원`;

export function clock(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function when(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const yest = new Date(now);
  yest.setDate(now.getDate() - 1);
  if (sameDay) return `오늘 ${clock(iso)}`;
  if (d.toDateString() === yest.toDateString()) return `어제 ${clock(iso)}`;
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

export function secondsLeft(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null;
  return Math.max(0, Math.floor((new Date(iso).getTime() - now) / 1000));
}

// 진행 상태 → 배지 문구·색
export const STATE_BADGE: Record<RunState, { label: string; tone: "ok" | "pri" | "warn" | "bad" | "gray" }> = {
  RECEIVED: { label: "접수", tone: "gray" },
  NEED_CLARIFICATION: { label: "조건 확인 중", tone: "pri" },
  CANDIDATES_READY: { label: "후보 선택 중", tone: "pri" },
  ORDER_BUILT: { label: "주문안 생성", tone: "gray" },
  PAYMENT_METHOD_SELECTION: { label: "결제수단 선택 중", tone: "pri" },
  QUOTE_CONFIRMED: { label: "견적 확정", tone: "gray" },
  VALIDATING: { label: "검증 중", tone: "gray" },
  AWAITING_APPROVAL: { label: "승인 대기", tone: "pri" },
  PAYING: { label: "결제 중", tone: "pri" },
  PAYMENT_UNKNOWN: { label: "결제 확인 중", tone: "warn" },
  COMPLETED: { label: "결제 완료", tone: "ok" },
  BLOCKED: { label: "진행 차단", tone: "bad" },
  FAILED: { label: "처리 실패", tone: "bad" },
  CANCELLED: { label: "취소됨", tone: "gray" },
};

// 사용자가 대답해야 하는 상태 (이때만 취소 버튼)
export const WAITING: RunState[] = [
  "NEED_CLARIFICATION",
  "CANDIDATES_READY",
  "PAYMENT_METHOD_SELECTION",
  "AWAITING_APPROVAL",
];
// O가 혼자 다음 단계로 넘어가는 상태 → 화면은 다시 조회
export const TRANSIENT: RunState[] = ["RECEIVED", "ORDER_BUILT", "QUOTE_CONFIRMED", "VALIDATING", "PAYING", "PAYMENT_UNKNOWN"];

// 차단 사유 (D-02·B 409) → 문구
export const BLOCK_REASON: Record<string, string> = {
  BUDGET_EXCEEDED: "예산 초과",
  PAYMENT_METHOD_NOT_ALLOWED: "허용하지 않은 결제수단",
  ACTION_NOT_ALLOWED: "맡기지 않은 작업",
  QUOTE_EXPIRED: "견적 유효시간 지남",
  AMOUNT_MISMATCH: "금액이 맞지 않음",
  INGREDIENT_CONFLICT: "제외한 재료가 들어 있음",
  INGREDIENT_UNKNOWN: "재료를 확인할 수 없음",
  QUOTE_DATA_UNVERIFIED: "혜택 금액을 확인할 수 없음",
  POLICY_VERSION_MISMATCH: "맡긴 조건이 바뀜",
  CONSTRAINTS_NOT_CONFIRMED: "조건이 확정되지 않음",
  STALE_CALCULATION: "혜택 금액이 바뀜",
  VERSION_CONFLICT: "주문 정보가 바뀜",
  BENEFIT_MISMATCH: "혜택 금액이 바뀜",
};

// 결제수단 제외 사유 (C-01 excluded)
export const EXCLUDE_REASON: Record<string, string> = {
  PERFORMANCE_NOT_MET: "전월 실적 미달",
  LOCAL_CURRENCY_NOT_ACCEPTED: "지역화폐 가맹점 아님",
  LOCAL_CURRENCY_UNVERIFIED: "지역화폐 가맹 여부 확인 불가",
  LOCAL_CURRENCY_INSUFFICIENT_BALANCE: "잔액 부족",
  PAYMENT_METHOD_NOT_ACTIVE: "사용 중지된 수단",
  PAYMENT_REQUIREMENT_NOT_MET: "결제 조건 불충족",
};

// 실패 코드 → 기본 문구 (error.message가 없을 때)
export const FAIL_TEXT: Record<string, string> = {
  NO_CANDIDATE: "조건에 맞는 가게를 찾지 못했어요.",
  NO_PAYMENT_METHOD: "맡긴 결제수단 중 쓸 수 있는 게 없어요.",
  PAYMENT_FAILED: "결제가 거절됐어요.",
  PAYMENT_NOT_STARTED: "결제가 시작되지 않았어요. 다시 주문해 주세요.",
  DEPENDENCY_UNAVAILABLE: "서비스에 일시적인 문제가 있어요. 잠시 후 다시 시도해 주세요.",
  REQUEST_REJECTED: "결제 요청이 거절됐어요.",
};

// B-06 견적의 unverified_conditions → 문구 (같은 문구는 한 번만)
export function unverifiedTexts(conds: string[]): string[] {
  const out = conds.map((c) => {
    if (/^items\[\d+\]\.description$/.test(c)) return "메뉴 설명이 없어 제외 재료를 확인하지 못한 메뉴가 있어요";
    if (c.toLowerCase() === "local_currency_merchant") return "지역화폐 가맹 여부를 확인하지 못했어요";
    return c;
  });
  return [...new Set(out)];
}

export const ACTION_LABEL = {
  SEARCH: "가게·메뉴 찾기",
  BUILD_ORDER: "주문안 만들기",
  REQUEST_PAYMENT: "결제 요청",
} as const;

// 시연용 태그 (백엔드 app/fakes/scenario.py)
export const DEMO_TAGS = [
  { tag: "#품절", desc: "첫 후보 품절" },
  { tag: "#모두품절", desc: "모든 후보 품절" },
  { tag: "#혜택불일치", desc: "혜택 금액 바뀜 → 차단" },
  { tag: "#결제거절", desc: "결제 거절" },
  { tag: "#결제미확정", desc: "결제 결과 늦게 옴" },
  { tag: "#응답유실", desc: "결제 응답 유실" },
  { tag: "#A장애", desc: "탐색 서비스 장애" },
];
