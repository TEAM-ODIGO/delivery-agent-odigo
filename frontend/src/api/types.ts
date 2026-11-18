// O(오케스트레이터) 응답 모양. 백엔드 app/modules/orchestrator/service.py의 _view·prompt와 같다.

export type RunState =
  | "RECEIVED"
  | "NEED_CLARIFICATION"
  | "CANDIDATES_READY"
  | "ORDER_BUILT"
  | "PAYMENT_METHOD_SELECTION"
  | "QUOTE_CONFIRMED"
  | "VALIDATING"
  | "AWAITING_APPROVAL"
  | "PAYING"
  | "PAYMENT_UNKNOWN"
  | "COMPLETED"
  | "BLOCKED"
  | "FAILED"
  | "CANCELLED";

export type Action = "SEARCH" | "BUILD_ORDER" | "REQUEST_PAYMENT";

export interface Delegation {
  allowed_actions: Action[];
  allowed_payment_method_ids: string[] | null;
  max_charge_krw: number | null;
}

export interface Step {
  current: number;
  total: number;
  label: string;
}

export interface Item {
  name: string;
  quantity: number;
  unit_price_krw: number | null;
}

export interface Question {
  slot: string;
  question: string;
  input_type: "SELECT" | "TEXT";
  options: (number | string)[];
  required: boolean;
}

export interface Candidate {
  index: number;
  restaurant_name: string;
  summary: string;
  total_krw: number;
  reasons: string[];
  meets_all_constraints: boolean;
  restaurant: {
    rating: number | null;
    delivery_minutes: number | null;
    delivery_fee_krw: number | null;
    local_currency_merchant: boolean | null;
    local_currency_basis: "ASSUMED_FROM_VOUCHER" | "KOMSCO_MATCHED" | null;
  };
  items: Item[];
  violations: { code: string; message: string | null }[];
}

export interface MethodRef {
  payment_method_id: string | null;
  name: string | null;
  type: string | null;
}

// 분할결제(로컬 확장, 팀 합의 전): 지역화폐로 낸 몫 + 나머지 몫
export interface SplitLeg {
  payment_method_id: string;
  name?: string | null;
  charge_krw: number;
}

export interface PaymentOption {
  rank: number;
  payment_method_id: string;
  instant_discount_krw: number;
  charge_krw: number;
  reasons: string[];
  name: string;
  method_type: string | null;
}

export interface Excluded extends MethodRef {
  reason_codes: string[];
}

export interface OrderView {
  restaurant_name: string;
  delivery_minutes: number | null;
  items: Item[];
  menu_subtotal_krw: number;
  delivery_fee_krw: number;
  instant_discount_krw: number;
  charge_krw: number;
  payment_method: MethodRef;
}

export type Prompt =
  | { type: "CLARIFICATION"; questions: Question[] }
  | { type: "SELECT_CANDIDATE"; candidates: Candidate[] }
  | {
      type: "SELECT_PAYMENT_METHOD";
      options: PaymentOption[];
      excluded: Excluded[];
      order: {
        restaurant_name: string;
        items: Item[];
        menu_subtotal_krw: number | null;
        delivery_fee_krw: number | null;
        total_krw: number | null;
      };
      expires_at: string | null;
    }
  | {
      type: "APPROVAL";
      summary: string;
      split?: { local_currency: SplitLeg; remainder: SplitLeg };
      // 사용자가 확인하고 예산을 넘겨 진행하는 주문일 때만 온다
      over_budget?: { limit_krw: number; charge_krw: number; over_krw: number };
      checks: string[];
      unverified_conditions: string[];
      expires_at: string | null;
      order: OrderView;
    };

export interface RunView {
  request_id: string;
  state: RunState;
  state_version: number;
  step: Step;
  prompt: Prompt | null;
  notice?: { code: string; message: string; issues?: unknown[] };
  result: {
    payment_id: string | null;
    payment_status: string;
    charge_krw: number | null;
    payments?: SplitLeg[]; // 분할결제일 때 수단별 몫 (지역화폐 먼저)
    order: OrderView | null;
  } | null;
  blocked: { reason_codes: string[]; details: Record<string, unknown> } | null;
  error: {
    code: string;
    message?: string;
    reason_codes?: string[];
    excluded?: Excluded[];
    service?: string;
    api?: string;
  } | null;
}

export type Reply =
  | { type: "CLARIFICATION"; answers: { slot: string; value: number | string }[] }
  | {
      type: "SELECT_CANDIDATE";
      candidate_index: number;
      allow_over_budget?: boolean; // 예산 초과 확인(로컬 확장): 사용자가 "예"를 눌렀을 때만 true
    }
  | {
      type: "SELECT_PAYMENT_METHOD";
      payment_method_id: string;
      local_currency_amount_krw?: number; // 있으면 payment_method_id는 나머지를 낼 카드
    }
  | { type: "APPROVE" }
  | { type: "CANCEL" };

export interface PaymentMethod {
  payment_method_id: string;
  name: string;
  type: "CARD" | "LOCAL_CURRENCY" | string;
  status: string;
  balance_krw: number | null;
  monthly_benefit_remaining_krw: number | null;
}

export interface RunRow {
  request_id: string;
  message: string;
  state: RunState;
  step: Step;
  restaurant_name: string | null;
  charge_krw: number | null;
  created_at: string;
  updated_at: string;
  user_id?: string;
  reason_codes?: string[];
}

export interface RunEvent {
  seq: number;
  event_type: string;
  from_state: string | null;
  to_state: string | null;
  service: string | null;
  detail: Record<string, unknown>;
  sync_b08: string | null;
  created_at: string;
}

export interface AuditLog {
  log_id: string;
  event_type: string;
  actor: string;
  result: string;
  reason_codes: string[];
  details: Record<string, unknown>;
  created_at: string;
}
