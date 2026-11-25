import { fetchAuthSession } from 'aws-amplify/auth';

import i18n from '../i18n/config';
import { apiErrorFromResponse } from './errors';
import type { GapLine } from './productLines';

import type {
  ApplicationFieldInput,
  CatalogProduct,
  ComparisonResponse,
  Customer,
  CustomerNote,
  CustomerNoteType,
  DataFileContent,
  DataFileListResponse,
  IngestedBrochure,
  MeetingSummaryResponse,
  Policy,
  PrioritiesResponse,
  RecommendationResponse,
  SampleBrochure,
  SubmitApplicationResponse,
} from '../types';

async function authHeaders(): Promise<Record<string, string>> {
  const session = await fetchAuthSession();
  const token = session.tokens?.accessToken?.toString();
  if (!token) {
    throw new Error('Not authenticated');
  }
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  };
}

function apiUrl(endpoint: string): string {
  const base = import.meta.env.VITE_API_BASE_URL.replace(/\/$/, '');
  return `${base}/${endpoint.replace(/^\//, '')}`;
}

async function getJson<T>(endpoint: string): Promise<T> {
  const response = await fetch(apiUrl(endpoint), { headers: await authHeaders() });
  if (!response.ok) {
    throw apiErrorFromResponse(endpoint, response.status, await response.text());
  }
  return (await response.json()) as T;
}

async function postJson<T>(endpoint: string, payload: unknown): Promise<T> {
  const response = await fetch(apiUrl(endpoint), {
    method: 'POST',
    headers: await authHeaders(),
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw apiErrorFromResponse(endpoint, response.status, await response.text());
  }
  return (await response.json()) as T;
}

/** The advisor's ranked to-do list for the Cockpit (GET /priorities). */
export async function loadPriorities(limit = 8): Promise<PrioritiesResponse> {
  return getJson<PrioritiesResponse>(`priorities?limit=${limit}`);
}
/**
 * Fetch customers assigned to the authenticated advisor and join them with
 * their policies client-side. Joins customer profiles with their policies client-side.
 */
export async function loadCustomersWithPolicies(): Promise<Customer[]> {
  const [customers, policies] = await Promise.all([
    getJson<Omit<Customer, 'policies'>[]>('profile'),
    getJson<Policy[]>('policy'),
  ]);

  return customers.map((c) => ({
    ...c,
    policies: policies.filter((p) => p.customer_id === c.customer_id),
  }));
}

/** Add a note to a customer's profile. Returns the note as stored. */
export async function addNote(
  customerId: string,
  note: { type: CustomerNoteType; text: string; event_date?: string }
): Promise<CustomerNote> {
  const result = await postJson<{ note: CustomerNote }>('profile/notes', {
    customer_id: customerId,
    ...note,
  });
  return result.note;
}

/**
 * Ask for a draft Summary / Concerns / Next actions note from a conversation.
 * Nothing is saved: the advisor reviews the draft and saves it with addNote.
 */
export async function summariseMeeting(
  customerId: string,
  turns: { role: 'user' | 'assistant'; text: string }[],
  locale: string
): Promise<MeetingSummaryResponse> {
  return postJson<MeetingSummaryResponse>('meeting-summary', {
    customer_id: customerId,
    turns: trimTurnsForSummary(turns),
    locale,
  });
}

// The server (lambda/meeting_summary) reads the last 400 turns, 2,000 characters each, 24,000 in all,
// and API Gateway and the WAF refuse a larger request (60 KB). The budget below keeps a request of
// the most recent turns well inside those limits, whatever the language (text counted in UTF-8 bytes).
const SUMMARY_MAX_TURNS = 400;
const SUMMARY_MAX_TURN_CHARS = 2000;
const SUMMARY_MAX_TOTAL_CHARS = 22000;
const SUMMARY_MAX_TEXT_BYTES = 40000;

/** The most recent turns of a conversation that fit the summary request, oldest first. */
export function trimTurnsForSummary(
  turns: { role: 'user' | 'assistant'; text: string }[]
): { role: 'user' | 'assistant'; text: string }[] {
  const encoder = new TextEncoder();
  const kept: { role: 'user' | 'assistant'; text: string }[] = [];
  let chars = 0;
  let bytes = 0;
  for (let i = turns.length - 1; i >= 0 && kept.length < SUMMARY_MAX_TURNS; i--) {
    const text = turns[i].text.replace(/\s+/g, ' ').trim().slice(0, SUMMARY_MAX_TURN_CHARS);
    if (!text) continue;
    const size = encoder.encode(text).length;
    if (kept.length > 0 && (chars + text.length > SUMMARY_MAX_TOTAL_CHARS || bytes + size > SUMMARY_MAX_TEXT_BYTES)) break;
    kept.push({ role: turns[i].role, text });
    chars += text.length;
    bytes += size;
  }
  return kept.reverse();
}
/**
 * "Send for Signature": save the whole form as one JSON object (POST /applications).
 * `customerId` is null for a prospect. Secret fields are dropped on the server.
 * `ref` is the reference of an earlier save of this application: it is overwritten, not duplicated.
 */
export async function submitApplication(
  productType: string,
  customerId: string | null,
  fields: Record<string, ApplicationFieldInput>,
  ref?: string | null
): Promise<SubmitApplicationResponse> {
  return postJson<SubmitApplicationResponse>('applications', {
    product_type: productType,
    customer_id: customerId,
    fields,
    ...(ref ? { ref } : {}),
  });
}
/** Delete one note. Only the advisor's own sign-in token is accepted for this. */
export async function deleteNote(customerId: string, noteId: string): Promise<void> {
  const endpoint =
    `profile/notes/${encodeURIComponent(noteId)}` +
    `?customer_id=${encodeURIComponent(customerId)}`;
  const response = await fetch(apiUrl(endpoint), {
    method: 'DELETE',
    headers: await authHeaders(),
  });
  if (!response.ok) {
    throw apiErrorFromResponse(endpoint, response.status, await response.text());
  }
}

/** Coerce a DynamoDB-stringified numeric to a number for display. */
export function toNumber(value: number | string | undefined): number {
  if (value === undefined || value === null) return 0;
  if (typeof value === 'number') return value;
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** Distinct product types present in the catalog, sorted. */
export function loadProductTypes(): Promise<string[]> {
  // Kept for the session: the list is the same for every advisor and the call
  // costs about a second on a cold backend. A failure is not kept, so the next
  // call (a retry) asks again.
  if (!productTypesPromise) {
    productTypesPromise = getJson<{ product_types: string[] }>('catalog/product-types')
      .then((data) => data.product_types)
      .catch((err: unknown) => {
        productTypesPromise = null;
        throw err;
      });
  }
  return productTypesPromise;
}
let productTypesPromise: Promise<string[]> | null = null;

/** Catalog entries filtered to a single product type. */
export async function loadProductsByType(productType: string): Promise<CatalogProduct[]> {
  const qs = new URLSearchParams({ type: productType }).toString();
  const data = await getJson<{ products: CatalogProduct[] }>(`catalog/products?${qs}`);
  return data.products;
}

/**
 * Produce a side-by-side comparison of 2-4 products. Response is a fixed
 * JSON shape generated by the LLM; see `ComparisonResponse` in types.ts.
 * Translated into the active UI locale by the backend.
 */
export async function compareProducts(
  productIds: string[],
  locale: string,
  tempIds: string[] = []
): Promise<ComparisonResponse> {
  return postJson<ComparisonResponse>('comparator/compare', {
    product_ids: productIds,
    ...(tempIds.length > 0 && { temp_ids: tempIds }),
    locale,
  });
}

/**
 * Read an uploaded competitor brochure for a temporary comparison. The file is
 * uploaded first with uploadDocument(file, null). The reading is stored for a
 * day under the advisor's own prefix and used by compareProducts via temp_id.
 */
export async function ingestBrochure(
  documentId: string,
  productType: string
): Promise<IngestedBrochure> {
  return postJson<IngestedBrochure>('comparator/ingest', {
    document_id: documentId,
    product_type: productType,
  });
}

/** Sample competitor brochures (a fictitious carrier) offered as downloads. */
export function listSampleBrochures(): Promise<SampleBrochure[]> {
  if (!sampleBrochuresPromise) {
    sampleBrochuresPromise = getJson<{ samples: SampleBrochure[] }>('data/samples')
      .then((result) => result.samples)
      .catch((err: unknown) => {
        sampleBrochuresPromise = null;
        throw err;
      });
  }
  return sampleBrochuresPromise;
}
let sampleBrochuresPromise: Promise<SampleBrochure[]> | null = null;

/**
 * Warm the two lists the Comparator needs, so its dropdown is usually ready
 * when the advisor first opens the tab. Fire and forget: errors are ignored
 * here and surface (with a Retry) only if the advisor actually opens the page.
 */
export function prefetchComparatorData(): void {
  void loadProductTypes().catch(() => undefined);
  void listSampleBrochures().catch(() => undefined);
}

/**
 * The text to show for an error thrown by getJson/postJson. An ApiError already carries a safe
 * message (the backend's short message for a client error, or a fixed sentence); any other
 * Error is a problem on this side (no network, not signed in) and its message is shown.
 */
export function friendlyApiError(err: unknown): string {
  return err instanceof Error ? err.message : i18n.t('common.errors.requestFailed');
}
/**
 * List every browsable reference document, grouped by folder and returned in
 * the backend's display order. Cheap (S3 listings only, no document bodies),
 * so the Data page loads this once on mount.
 */
export async function listDataFiles(): Promise<DataFileListResponse> {
  return getJson<DataFileListResponse>('data/files');
}

/**
 * Fetch one reference document's content. Markdown front-matter is split off
 * server-side into `metadata`; JSON is returned pretty-printed.
 *
 * `folder` and `key` are validated against an allowlist server-side, so an
 * unknown or malformed key comes back as a 404 rather than reading an
 * arbitrary object.
 */
export async function loadDataFile(
  folder: string,
  key: string
): Promise<DataFileContent> {
  const qs = new URLSearchParams({ folder, key }).toString();
  const raw = await getJson<Omit<DataFileContent, 'content'> & { content?: string }>(
    `data/file?${qs}`
  );
  return { ...raw, content: raw.content ?? '' };
}

/**
 * Generate an LLM-backed coverage-gap analysis + Awesome product
 * recommendation for the selected customer. Pulls the customer's full
 * context server-side (profile + policies + catalog + promotions). Returns
 * a structured payload — see RecommendationResponse for the exact shape.
 *
 * `line` limits the analysis to Life, General or Health products (or all).
 *
 * Latency is dominated by the Bedrock call, typically 4-8 seconds. Callers
 * should render a loading state while this is in flight.
 */
export async function loadRecommendation(
  customerId: string,
  locale: string,
  line: GapLine = 'all'
): Promise<RecommendationResponse> {
  return postJson<RecommendationResponse>('recommend', {
    customer_id: customerId,
    locale,
    line,
  });
}

/**
 * Public sign-up. Calls the unauthenticated POST /signup Lambda which uses
 * Cognito admin APIs to create + confirm the user, working around the
 * user pool's AllowAdminCreateUserOnly setting.
 *
 * Throws an Error with the server-supplied message on failure (e.g. user
 * already exists, weak password). On success, the caller can sign in with
 * the same credentials immediately — the user is already CONFIRMED.
 */
export async function signupUser(email: string, password: string): Promise<void> {
  const response = await fetch(apiUrl('signup'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    let message = `Sign-up failed (${response.status})`;
    try {
      const body = (await response.json()) as { error?: string };
      if (body?.error) message = body.error;
    } catch {
      /* ignore JSON parse errors, use default */
    }
    throw new Error(message);
  }
}

/**
 * Allowed Content-Type → file extension mapping. Mirror of the server-side
 * allowlist in lambda/documents/index.py. Anything else is rejected
 * client-side with a friendly error.
 */
const ALLOWED_DOC_CONTENT_TYPES: Record<string, string> = {
  'application/pdf': 'pdf',
  'text/markdown': 'md',
  'text/plain': 'txt',
  'image/jpeg': 'jpg',
  'image/png': 'png',
  'image/webp': 'webp',
};

const MAX_DOC_SIZE_BYTES = 10 * 1024 * 1024; // 10 MB — must match server cap

interface InitiateResponse {
  document_id: string;
  s3_key: string;
  /** Where the form is posted, and the fields (a signed policy) that go with the file. */
  upload_url: string;
  upload_fields: Record<string, string>;
  expires_in_seconds: number;
  max_size_bytes: number;
  content_type: string;
  filename: string;
}

/**
 * Two-step document upload:
 *   1. POST /documents/initiate → backend checks the customer and signs an S3 POST form
 *   2. POST the form (signed fields, then the file) directly to S3 (bypasses API Gateway 10MB cap)
 *
 * The signed policy fixes the key and Content-Type and bounds the size, so S3 itself refuses a
 * file that is empty or too large. Type and size are also checked here, before the network.
 *
 * Returns the document_id the agent uses to call
 * extract_policy_from_document. The caller then includes the document_id
 * in the next chat message (chat) or sends a synthetic transcript event
 * (voice) so the agent knows there's a document to extract.
 */
export async function uploadDocument(
  file: File,
  customerId: string | null
): Promise<{ document_id: string; filename: string; content_type: string; size_bytes: number }> {
  // Browsers sometimes report markdown as text/plain or omit content-type
  // entirely. Sniff by extension as a fallback.
  let contentType = file.type || '';
  if (!ALLOWED_DOC_CONTENT_TYPES[contentType]) {
    const ext = file.name.toLowerCase().split('.').pop() || '';
    if (ext === 'md') contentType = 'text/markdown';
    else if (ext === 'txt') contentType = 'text/plain';
    else if (ext === 'pdf') contentType = 'application/pdf';
    else if (ext === 'jpg' || ext === 'jpeg') contentType = 'image/jpeg';
    else if (ext === 'png') contentType = 'image/png';
    else if (ext === 'webp') contentType = 'image/webp';
  }
  if (!ALLOWED_DOC_CONTENT_TYPES[contentType]) {
    throw new Error(`Unsupported file type. Allowed: PDF, Markdown, JPEG, PNG, WEBP.`);
  }
  if (file.size > MAX_DOC_SIZE_BYTES) {
    const limitMb = Math.round(MAX_DOC_SIZE_BYTES / 1024 / 1024);
    throw new Error(`File is too large. Maximum size is ${limitMb} MB.`);
  }
  const initiate = await postJson<InitiateResponse>('documents/initiate', {
    filename: file.name,
    content_type: contentType,
    customer_id: customerId,
  });
  if (file.size > initiate.max_size_bytes) {
    const limitMb = Math.max(1, Math.round(initiate.max_size_bytes / 1024 / 1024));
    throw new Error(`File is too large. Maximum size is ${limitMb} MB.`);
  }
  // The signed fields go first and the file last: S3 ignores any field after the file. The
  // Content-Type is one of the signed fields, so it must not be set as a request header here.
  const form = new FormData();
  for (const [name, value] of Object.entries(initiate.upload_fields)) {
    form.append(name, value);
  }
  form.append('file', file);
  const uploadResponse = await fetch(initiate.upload_url, { method: 'POST', body: form });
  if (!uploadResponse.ok) {
    throw apiErrorFromResponse('document upload', uploadResponse.status, await uploadResponse.text());
  }
  return {
    document_id: initiate.document_id,
    filename: initiate.filename,
    content_type: initiate.content_type,
    size_bytes: file.size,
  };
}
