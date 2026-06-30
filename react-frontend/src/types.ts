// Domain types mirror the shape returned by the /profile and /policy Lambdas.
// Fields match what the API Lambdas return.

export interface Customer {
  customer_id: string;
  name: string;
  email: string;
  phone: string;
  address: string;
  date_of_birth?: string;
  marital_status?: string;
  dependents?: number;
  occupation?: string;
  employment_status?: string;
  annual_income?: number;
  home_owner?: boolean;
  smoking?: boolean;
  medical_conditions?: string;
  join_date: string;
  status: string; // "Active" | "Inactive" — used for the badge UNLESS the customer has no Unicorn-issued policies (every policy has third_party=true), in which case the UI labels them "Prospect".
  advisor_id: string;
  // Attached client-side after fetching policies:
  policies: Policy[];
}

export interface Policy {
  id: string;
  customer_id: string;
  type: string;
  product_name?: string;
  premium_amount: number | string;
  premium_frequency: string;
  coverage_amount: number | string;
  status: string;
  start_date?: string;
  renewal_date: string;
  last_updated?: string;
  advisor_id: string;

  // Third-party policies are coverage the customer has bought elsewhere.
  // Tracked alongside Unicorn policies so the advisor can see the full
  // coverage picture for gap analysis and recommendations.
  third_party?: boolean;
  insurer?: string;

  // Type-specific detail payloads. Backend returns exactly one of these
  // based on the policy `type` (and, for life policies, the `life_type`
  // nested inside `life_details`).
  vehicle?: VehicleDetails;
  property?: PropertyDetails;
  health_details?: HealthDetails;
  disability_details?: DisabilityDetails;
  life_details?: LifeDetails;
}

export interface VehicleDetails {
  make?: string;
  model?: string;
  year?: number;
  registration?: string;
}

export interface PropertyDetails {
  address?: string;
  property_type?: string;
  year_built?: number;
  square_feet?: number;
}

export interface HealthDetails {
  plan_tier?: string;
  network?: string;
  dependents?: number;
}

export interface DisabilityDetails {
  benefit_period_years?: number;
  waiting_period_days?: number;
  occupation_class?: string;
}

/**
 * Life insurance shape differs by `life_type`:
 * - Term (life_type absent or "Term Life"): term_years + beneficiary + smoker
 * - Whole: premium_schedule, cash_value_estimate, dividend_option
 * - Universal: death_benefit_option, cash_value_estimate, current_credited_rate, guaranteed_minimum_rate
 * - Variable: death_benefit_option, sub_account_allocation (map of category → percent), cash_value_estimate
 *
 * All fields are optional so the UI gracefully handles partial payloads.
 */
export interface LifeDetails {
  life_type?: string;
  beneficiary?: string;
  smoker?: boolean;
  // Term
  term_years?: number;
  // Whole + Universal + Variable
  cash_value_estimate?: number;
  // Whole
  premium_schedule?: string;
  dividend_option?: string;
  // Universal + Variable
  death_benefit_option?: string;
  // Universal
  current_credited_rate?: string;
  guaranteed_minimum_rate?: string;
  // Variable
  sub_account_allocation?: Record<string, number>;
}

export type ChatRole = 'user' | 'assistant';

export interface ChatMessage {
  role: ChatRole;
  content: string;
  error?: string;
}

export type Page = 'assistant' | 'voice' | 'comparator';

export type VoiceMessageRole = 'user' | 'assistant';

export interface VoiceMessage {
  id: string;
  role: VoiceMessageRole;
  text: string;
}

// Document attached by the advisor via the chat composer's 📎 button.
// The browser uploads the binary directly to S3 via a presigned PUT URL
// that the backend issues; this client-side type tracks the upload state
// and the eventual document_id the agent uses to call the
// extract_policy_from_document tool.
export type UploadStatus = 'uploading' | 'ready' | 'error';

export interface UploadedDocument {
  document_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: UploadStatus;
  error?: string;
}

// Coverage-gap recommendation returned by POST /recommend. Shape matches the
// tool schema enforced server-side in lambda/recommend/index.py so the front
// end can render it without prose-parsing.
export interface RecommendationProduct {
  product_name: string;
  product_type: string;
  why_helps: string;
}

export interface RecommendationGap {
  gap: string;
  why: string;
  recommendations: RecommendationProduct[];
}

export interface RecommendationResponse {
  summary: string;
  gaps: RecommendationGap[];
  disclaimer: string;
}

// Product catalog — returned by /catalog/products and /catalog/products/{id}.
// Used by the Comparator page to list products within a selected type and to
// build the compare request.
export interface CatalogProduct {
  product_id: string;
  carrier_id: string;
  carrier_name: string;
  product_name: string;
  product_type: string;
  pricing_tier: string;
  s3_bucket: string;
  s3_key: string;
}

// Shape returned by POST /comparator/compare. Matches the JSON schema the
// backend forces the LLM to emit, so every field is present and arrays line
// up with the product order in `products`.
export interface ComparisonProduct {
  id: string;
  name: string;
  carrier: string;
  pricing_tier?: string;
}

export interface ComparisonRow {
  attribute: string;
  values: string[]; // length === products.length
}

export interface ComparisonSection {
  title: string;
  rows: ComparisonRow[];
}

export interface ComparisonResponse {
  title: string;
  summary: string;
  products: ComparisonProduct[];
  sections: ComparisonSection[];
  disclaimer: string;
}
