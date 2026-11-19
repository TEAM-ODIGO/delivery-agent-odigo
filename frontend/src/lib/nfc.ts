// NFC 실물 카드 결제 (로컬 확장, 팀 합의 전)
//
// 카드(NTAG213)에는 URL 하나를 써 둔다:  http://PC주소:5173/tap?t=토큰
//  - 안드로이드 크롬: 승인 화면에서 Web NFC로 카드를 직접 읽는다 (토큰 + 일련번호)
//  - 아이폰: 웹에서 NFC를 직접 읽을 수 없다. 대신 iOS가 카드의 URL을 알림으로 띄우고,
//           누르면 Safari가 /tap 화면을 연다 → 그 화면이 기다리던 주문을 토큰으로 결제한다
import type { CardTap } from "../api/types";

export const NFC_PAY = import.meta.env.VITE_NFC_PAY === "1";

const PENDING_KEY = "agent.nfcPendingRun"; // 아이폰: 카드 태그를 기다리는 주문
const PENDING_MINUTES = 5;

export function canReadNfcInPage(): boolean {
  return "NDEFReader" in window;
}

export function isIPhone(): boolean {
  return /iPhone|iPad|iPod/.test(navigator.userAgent);
}

// 카드 URL에서 토큰(t)을 꺼낸다. URL이 아니면 빈 문자열
export function tokenFromUrl(text: string): string {
  try {
    return new URL(text).searchParams.get("t") || "";
  } catch {
    return "";
  }
}

// 안드로이드 크롬: 카드가 닿으면 토큰과 일련번호를 돌려준다
export function readCardTap(signal: AbortSignal): Promise<CardTap> {
  if (!canReadNfcInPage()) {
    return Promise.reject(new Error("이 브라우저에서는 NFC를 직접 읽을 수 없어요."));
  }
  const reader = new (window as any).NDEFReader();
  return new Promise((resolve, reject) => {
    signal.addEventListener("abort", () => reject(new Error("태그를 취소했어요.")));
    reader.onreading = (event: any) => {
      let token = "";
      for (const record of event.message.records) {
        if (record.recordType === "url" || record.recordType === "absolute-url") {
          token = tokenFromUrl(new TextDecoder().decode(record.data));
          break;
        }
      }
      const tap: CardTap = {};
      if (token) tap.token = token;
      if (event.serialNumber) tap.serial_number = event.serialNumber;
      if (!tap.token && !tap.serial_number) {
        reject(new Error("카드 정보를 읽지 못했어요. 다시 대 주세요."));
        return;
      }
      resolve(tap);
    };
    reader.onreadingerror = () => {
      reject(new Error("카드를 읽지 못했어요. 카드를 휴대폰 뒷면 가운데에 다시 대 주세요."));
    };
    reader.scan({ signal }).catch(reject);
  });
}

// ---- 아이폰: 승인 화면 → (카드 태그 → Safari가 /tap 열기) → /tap 화면 사이에 주문 번호를 넘긴다
export function savePendingTap(requestId: string): void {
  try {
    localStorage.setItem(PENDING_KEY, JSON.stringify({ requestId, savedAt: Date.now() }));
  } catch {
    // 저장소를 못 쓰는 브라우저(사생활 보호 모드 등)는 /tap 화면에서 안내한다
  }
}

export function clearPendingTap(): void {
  try {
    localStorage.removeItem(PENDING_KEY);
  } catch {
    // 무시
  }
}

export function loadPendingTap(): string | null {
  try {
    const raw = localStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    if (Date.now() - saved.savedAt > PENDING_MINUTES * 60 * 1000) {
      localStorage.removeItem(PENDING_KEY);
      return null;
    }
    return saved.requestId;
  } catch {
    return null;
  }
}

export function isPendingKey(key: string | null): boolean {
  return key === PENDING_KEY;
}
