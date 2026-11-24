// 내 카드·주문 설정 (예전 'Agent에게 맡길 범위'). 사용자별로 이 브라우저에 저장하고 다음 주문부터 쓴다.
import { getUserToken } from "../api/client";
import type { Action, Delegation } from "../api/types";

export const ALL_ACTIONS: Action[] = ["SEARCH", "BUILD_ORDER", "REQUEST_PAYMENT"];
export const DEFAULT_DELEGATION: Delegation = {
  allowed_actions: ALL_ACTIONS,
  allowed_payment_method_ids: null,
  max_charge_krw: null,
};

export function loadDelegation(): Delegation {
  try {
    const raw = localStorage.getItem(`agent.delegation.${getUserToken()}`);
    return raw ? { ...DEFAULT_DELEGATION, ...JSON.parse(raw) } : DEFAULT_DELEGATION;
  } catch {
    return DEFAULT_DELEGATION;
  }
}

export function saveDelegation(d: Delegation) {
  try {
    localStorage.setItem(`agent.delegation.${getUserToken()}`, JSON.stringify(d));
  } catch {
    /* 저장 못 해도 이번 화면에서는 그대로 쓴다 */
  }
}
