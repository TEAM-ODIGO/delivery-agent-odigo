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
    // 음식 종류 (A의 CUISINES 키: 치킨, 피자, 한식 ...). 카드 오른쪽 이미지에 쓴다
    cuisine?: string | null;
  };
  items: Item[];
  violations: { code: string; message: string | null }[];
  // 최소주문금액 미달이면 orderable=false (로컬 확장). min_order에 필요한 금액과 현재 주문 금액이 온다.
  orderable?: boolean;
  min_order?: { required_krw: number; order_krw: number } | null;
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

// NFC 실물 카드 태그 결과 (로컬 확장). token: 카드 URL의 t 값, serial_number: 카드 일련번호(안드로이드만)
export interface CardTap {
  token?: string;
  serial_number?: string;
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
  | { type: "APPROVE"; card_tap?: CardTap } // card_tap: NFC 실물 카드 태그(로컬 확장)
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

// O-13 / O-14 요기요 장바구니 담기
export type YogiyoCartStatus =
  | "NONE"
  | "QUEUED"
  | "RUNNING"
  | "CART_READY"
  | "CART_MISMATCH"
  | "FAILED";

export interface YogiyoCartItem {
  menu_id: number | string;
  name: string | null;
  quantity: number;
  unit_price_krw: number | null;
  options?: (string | null)[];
}

export interface YogiyoCartJob {
  status: YogiyoCartStatus;
  job_id?: string;
  mode?: "fake" | "real" | string;
  restaurant_id?: string;
  cart_items?: YogiyoCartItem[];
  menu_subtotal_krw?: number | null;
  delivery_fee_krw?: number | null;
  mismatches?: string[];
  error?: string | null;
  yogiyo_url?: string | null;
  screenshot?: string | null; // data URL (끝난 작업만)
  created_at?: string;
  finished_at?: string | null;
}

// O-15 / O-16 요기요 실시간 식당 확인
export type LiveStatus =
  | "CHECKING"
  | "OK"
  | "CHANGED"
  | "UNAVAILABLE"
  | "BLOCKED"
  | "ERROR"
  | "SKIPPED"
  | "NOT_CHECKED";

export interface LiveResult {
  candidate_index: number;
  restaurant_id: string;
  status: LiveStatus;
  changes: string[];
  reason: string | null;
  checked_at: string | null;
  visible?: boolean; // 화면에 보일 후보 (고를 수 있는 후보가 want곳 나올 때까지 순위대로)
  selectable?: boolean; // 주문 불가로 확인되면 false
}

export interface LiveCheckView {
  mode: "fake" | "real" | "off" | string;
  state: "RUNNING" | "DONE" | "NONE" | "OFF";
  total: number;
  done: number;
  passed?: number;
  want?: number;
  all_unavailable?: boolean;
  started_at?: string | null;
  results: LiveResult[];
}

// ---- 내 카드 혜택 화면 (로컬 확장, 팀 합의 전) ----
export interface CardTier {
  min_krw: number; // 전월 이용금액 문턱
  cap_krw: number; // 그 구간의 배달 할인이 들어가는 월 한도
}

export interface CardBenefitLine {
  title: string;
  rate: string;
  detail: string;
  delivery: boolean;
}

export interface RecentPayment {
  payment_id: string;
  request_id: string | null;
  status: string;
  charge_krw: number;
  share_krw: number; // 이 카드가 낸 몫 (분할결제가 아니면 charge_krw와 같다)
  refunded_krw: number;
  refundable_krw: number;
  split: boolean;
  approved_at: string | null;
  created_at: string | null;
  restaurant_name: string | null;
  message: string | null;
}

export interface CardDashboard {
  payment_method_id: string;
  product_id: string;
  type: "CARD" | "LOCAL_CURRENCY" | string;
  name: string;
  status: string;
  benefit_rule_version: string;
  product: {
    product_name: string | null;
    card_kind: string | null;
    annual_fee: string | null;
    issuance: string | null;
    headline: string | null;
    tier_label: string | null;
    benefits: CardBenefitLine[] | null;
    notes: string[] | null;
    source_url: string | null;
  };
  checked_at: string | null;
  monthly_benefit: {
    cap_krw: number | null;
    used_krw: number;
    remaining_krw: number | null;
    min_performance_krw: number;
  };
  performance: {
    monthly_performance_krw: number;
    basis: string | null;
    eligible: boolean;
    tiers: CardTier[];
    current: CardTier | null;
    next: CardTier | null;
    to_next_krw: number | null;
    message: string | null;
  } | null;
  balance_krw: number | null;
  recent_payments: RecentPayment[];
}

export interface RefundResult {
  payment_id: string;
  refund_id: string;
  status: "PENDING" | "REFUNDED" | "FAILED" | "UNKNOWN" | string;
  amount_krw: number;
}
