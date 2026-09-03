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
  // When the advisor last confirmed this profile is current (ISO date).
  // Drives the BR-GAP-006 "verify current info" staleness badge. Distinct
  // from join_date (enrollment). May be absent on legacy records.
  last_reviewed?: string;
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

export type Page = 'assistant' | 'voice' | 'comparator' | 'data';

// ---------------------------------------------------------------------------
// Data explorer — read-only browser over the reference documents that ground
// the assistant's answers. Served by GET /data/files and GET /data/file,
// which read the six knowledge buckets (see lambda/data/index.py).
// ---------------------------------------------------------------------------

/** Folder aliases the backend will serve. Mirrors FOLDER_ORDER in the Lambda. */
export type DataFolder =
  | 'portfolio'
  | 'company'
  | 'competitive'
  | 'competitors'
  | 'promotion'
  | 'forms';

export type DataContentType = 'markdown' | 'json' | 'text';

/** One browsable document, as returned by the listing endpoint. */
export interface DataFile {
  folder: DataFolder;
  /** S3 key within the folder's bucket, e.g. "products/rainbow-life.md". */
  key: string;
  /** Filename only, no directories. */
  name: string;
  /** Directory portion of the key ('' for root-level files). */
  path: string;
  content_type: DataContentType;
  size_bytes: number;
  last_modified?: string;
}

/** A folder and the documents it contains. */
export interface DataFolderGroup {
  folder: DataFolder;
  files: DataFile[];
}

export interface DataFileListResponse {
  folders: DataFolderGroup[];
  total: number;
}

/** A single document's content, with front-matter split out as metadata. */
export interface DataFileContent {
  folder: DataFolder;
  key: string;
  name: string;
  content_type: DataContentType;
  content: string;
  /** Parsed front-matter (e.g. `last_updated`). Empty when the doc has none. */
  metadata: Record<string, string>;
  size_bytes: number;
}

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
  // BR-COMP-003: source-document freshness, populated server-side from the
  // product doc's `last_updated` front-matter. Absent when the source has no
  // date. `stale` is true when the source is older than the 90-day window.
  last_updated?: string;
  stale?: boolean;
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

// ---------------------------------------------------------------------------
// Application form auto-fill (voice-driven)
//
// The schema is data, not code: it describes which fields exist, how to render
// them, and which are required. It lives in S3 (s3-data/forms/<product>.json)
// and is served by the `get_form_schema` gateway tool. The voice agent fetches
// it when an application starts and relays it to the browser, so the frontend
// holds no schema of its own and new products need no frontend change.
// ---------------------------------------------------------------------------

export type FormFieldType = 'text' | 'date' | 'number' | 'currency' | 'select' | 'boolean';

export interface FormFieldOption {
  value: string;
  label: string;
}

export interface FormFieldDef {
  /** Dot path, e.g. "applicant.full_name". Also the key used by fill events. */
  path: string;
  label: string;
  type: FormFieldType;
  required: boolean;
  hint?: string;
  options?: FormFieldOption[];
}

export interface FormSectionDef {
  id: string;
  title: string;
  fields: FormFieldDef[];
}

export interface FormSchema {
  form_id: string;
  product_type: string;
  product_name: string;
  title: string;
  sections: FormSectionDef[];
}

/**
 * Confidence tiers:
 * - high   (>= 0.85) taken as-is, green
 * - review (< 0.85)  filled but flagged for the advisor to confirm, amber
 *
 * Everything extracted gets written into the form — a weak guess the advisor
 * can see and correct beats a suggestion they have to go hunting for.
 */
export type FieldConfidence = 'high' | 'review';

/** Where a field's current value came from. Drives the indicator styling. */
export type FieldOrigin = 'profile' | 'voice' | 'manual';

export interface FormFieldState {
  value: string;
  /** Raw model confidence, retained so the UI can show the exact number. */
  confidence: number;
  tier: FieldConfidence;
  /** The utterance the value was extracted from — shown on hover. */
  source?: string;
  origin: FieldOrigin;
}

/** One extraction result. Shape mirrors the planned `fill_form_field` tool. */
export interface FormFillEvent {
  path: string;
  value: string;
  confidence: number;
  source?: string;
}
