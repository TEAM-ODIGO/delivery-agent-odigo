// 화면은 O만 부른다 (/orchestrator/...). 개발 서버가 백엔드로 넘겨 준다(vite.config.ts).
import type { AuditLog, Delegation, PaymentMethod, Reply, RunEvent, RunRow, RunView } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

// MVP 1 임시 로그인: 백엔드 .env DEV_USER_TOKENS의 토큰 (dev-token-U001 → U001)
export const DEV_USERS = [
  { token: "dev-token-U001", userId: "U001" },
  { token: "dev-token-U002", userId: "U002" },
];

const USER_KEY = "agent.userToken";
const ADMIN_KEY = "agent.adminToken";

function store(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function getUserToken(): string {
  return store()?.getItem(USER_KEY) || DEV_USERS[0].token;
}
export function setUserToken(token: string): void {
  store()?.setItem(USER_KEY, token);
}
export function getAdminToken(): string {
  return store()?.getItem(ADMIN_KEY) || "";
}
export function setAdminToken(token: string): void {
  store()?.setItem(ADMIN_KEY, token);
}

function newKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function call<T>(
  path: string,
  init: RequestInit & { token?: string; idempotent?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    Authorization: `Bearer ${init.token ?? getUserToken()}`,
  };
  if (init.body) headers["Content-Type"] = "application/json";
  if (init.idempotent) headers["Idempotency-Key"] = newKey();
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "서버에 연결할 수 없어요. 잠시 후 다시 시도해 주세요.");
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = data?.error ?? {};
    throw new ApiError(
      res.status,
      err.code ?? "UNKNOWN_ERROR",
      err.message ?? "요청을 처리하지 못했어요.",
      err.details ?? {},
    );
  }
  return data as T;
}

export const DEMO_ADDRESS = {
  label: "서울특별시 중구 세종대로 110 (서울시청)",
  value: { region_code: "11140", lat: 37.5665868, lng: 126.97819753 },
};

export const api = {
  // O-01
  startRun: (message: string, delegation: Delegation) =>
    call<RunView>("/orchestrator/runs", {
      method: "POST",
      idempotent: true,
      body: JSON.stringify({ message, delivery_address: DEMO_ADDRESS.value, delegation }),
    }),
  // O-02
  reply: (requestId: string, expectedVersion: number, reply: Reply) =>
    call<RunView>(`/orchestrator/runs/${requestId}/reply`, {
      method: "POST",
      idempotent: true,
      body: JSON.stringify({ ...reply, expected_state_version: expectedVersion }),
    }),
  // O-03
  getRun: (requestId: string) => call<RunView>(`/orchestrator/runs/${requestId}`),
  // O-09
  myRuns: (limit = 20) =>
    call<{ count: number; results: RunRow[] }>(`/orchestrator/runs?limit=${limit}`),
  // O-10
  paymentMethods: () =>
    call<{ count: number; results: PaymentMethod[] }>("/orchestrator/payment-methods"),
  // O-11 (관리자)
  adminRuns: (token: string, state?: string) =>
    call<{ count: number; results: RunRow[] }>(
      `/orchestrator/admin/runs?limit=100${state ? `&state=${state}` : ""}`,
      { token },
    ),
  // O-04 (관리자)
  runEvents: (token: string, requestId: string) =>
    call<{ request_id: string; count: number; events: RunEvent[] }>(
      `/orchestrator/runs/${requestId}/events`,
      { token },
    ),
  // O-12 (관리자) — D-09 감사 로그
  auditLogs: (token: string, requestId: string) =>
    call<{ count: number; results: AuditLog[] }>(
      `/orchestrator/admin/runs/${requestId}/audit-logs`,
      { token },
    ),
};
