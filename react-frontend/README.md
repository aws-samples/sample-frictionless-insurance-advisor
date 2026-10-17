# Frictionless Insurance Advisor — React Frontend

Single-page React app that talks to the AgentCore Runtimes (text + voice) and the API Gateway backend. Runs locally for development; deployed to S3 + CloudFront with Origin Access Control by `insadv-05-frontend` for production.

## What it does

Four tabs, switched from the top nav (no router — page state plus lazy-loaded chunks):

| Tab | Label | What it does |
|---|---|---|
| `assistant` | **Text Assistant** | Streaming chat with the AgentCore text runtime (Claude Haiku 5.5). Per-customer history and session id. "New Prospect" mode for onboarding someone not yet on file. A **Sources** footer on answers that use documents or the web, a one-click **meeting-prep** quick prompt, pricing estimates, and application forms that open beside the chat and fill from the conversation. |
| `voice` | **Text+Voice Assistant** | Real-time voice over WebSocket to the voice runtime (Nova 2.5 Sonic), with barge-in. A recording-consent bar gates memory, forms and transcripts. Application forms open and fill themselves from the conversation. |
| `comparator` | **Comparator** | Side-by-side table for 2-4 products with carrier financial-strength ratings and a customer handout PDF, an optional uploaded competitor brochure (or a sample one), plus a per-customer coverage-gap recommender with a dollar exposure per gap. Both pass the active locale so the model answers in the UI language. |
| `data` | **Data** | Read-only explorer over the `s3-data` corpus that grounds the agent's answers: searchable file list, rendered markdown, rendered form schemas and rate cards, PDF brochures, and the advisor's own voice transcripts. |

Across all tabs:

- Sign in / sign up through a **custom auth UI** (`AuthGate`) backed by the Cognito user pool. The "Create account" tab appears only if the backend was deployed with the opt-in public sign-up route; by default there is sign-in only, and the demo users are created once with `scripts/create-demo-users.sh`.
- Collapsible customer sidebar (scoped server-side via the DynamoDB `advisor-id-index` GSI), with profile, Awesome policies, third-party coverage, and a **Notes** card (life events and interactions; add and delete by hand, notes added by the assistant are labelled).
- Document upload (PDF / image / markdown / text) → structured policy extraction → field-by-field confirmation → save as a third-party policy.
- Sample third-party policies downloadable as `.md` or `.pdf` so the upload flow can be exercised without external files.
- Resizable split panes, with the divider position and sidebar state persisted per surface.
- **Nine UI locales** with a switcher in the top nav, persisted to `localStorage`.
- Dark-mode toggle, persisted to `localStorage` (defaults to light regardless of OS preference).
- Pitch deck button that opens the standalone deck at `/presentation/index.html`.

## Prerequisites

- Node.js 18+ and npm
- AWS credentials in the shell
- All backend stacks deployed (`insadv-01-auth` through `insadv-04-voice`)

## Run

```bash
cd react-frontend
./run_app.sh
```

The script will:

1. Run `./scripts/setup-env.sh` if `.env.local` is missing (reads SSM parameters into `VITE_*` vars)
2. `npm install`
3. Start Vite at http://localhost:5173

## npm scripts

| Script | What it does |
|---|---|
| `dev` | Vite dev server. `predev` syncs sample policies first. |
| `build` | `tsc -b && vite build`. `prebuild` syncs sample policies first. |
| `preview` | Serve the production build locally |
| `typecheck` | `tsc -b --noEmit` — key and type checking without emitting |

`scripts/sync-mock-policies.sh` runs automatically on both `predev` and `prebuild`. It copies `s3-data/mock-policies/*.{md,pdf}` into `public/mock-policies/` and prunes orphans, so the in-app "sample policies" menu always matches the source of truth. The PDFs themselves are generated artefacts — `s3-data/mock-policies/build-pdfs.sh` (called from the repo-root `deploy.sh`) builds them from the markdown.

## Environment variables

`scripts/setup-env.sh` writes `.env.local` from SSM. Re-run it after any backend redeploy:

```bash
./scripts/setup-env.sh
```

| Variable | Source |
|---|---|
| `VITE_AWS_REGION` | `$AWS_REGION`, defaults to `us-east-1` |
| `VITE_COGNITO_USER_POOL_ID` | `/insurance-advisor/cognito/user-pool-id` |
| `VITE_COGNITO_CLIENT_ID` | `/insurance-advisor/cognito/app-client-id` |
| `VITE_API_BASE_URL` | `/insurance-advisor/api/gateway-url` |
| `VITE_AGENT_RUNTIME_ARN` | `/insurance-advisor/agentcore/runtime-arn` |
| `VITE_VOICE_RUNTIME_ARN` | `/insurance-advisor/voice/runtime-arn` (optional) |

The runtime ARNs are baked in at build time, so a runtime replacement means rebuilding the SPA — which is why `deploy.sh` always runs `setup-env.sh` before `npm run build`.

## Auth flow

Auth UI is hand-rolled (`src/components/AuthGate.tsx`), not Amplify's `<Authenticator>`. Two reasons: the user pool is closed to self-service sign-up (`AllowAdminCreateUserOnly`), which Amplify's component does not expect; and the custom UI matches the rest of the design system. Only `aws-amplify/auth` functions (`signIn`, `signOut`, `getCurrentUser`, `fetchAuthSession`) are used — `@aws-amplify/ui-react` is not a dependency.

Public sign-up is off by default. With `-c enable_public_signup=true` the backend adds a `/signup` endpoint (`lambda/signup/`) that creates and confirms users for the allowlisted addresses only, rate-limited by WAF; the build then sets `VITE_ENABLE_SIGNUP=true` (written by `scripts/setup-env.sh`) and the sign-in screen shows a "Create account" tab.

Sign in with a demo advisor (created once with `scripts/create-demo-users.sh`) and its password (upper / lower / digit / symbol). The browser only ever holds a Cognito JWT; no AWS IAM credentials are present in the SPA. The same token authenticates both the direct AgentCore Runtime calls and the API Gateway calls.

## Pages in detail

### Text Assistant

`streamChat()` posts straight to `bedrock-agentcore.<region>.amazonaws.com/runtimes/<arn>/invocations` with `Accept: text/event-stream` and yields tokens as they arrive. `advisorId` is deliberately never sent — the runtime derives identity from the verified JWT, so the browser cannot impersonate another advisor. History and session id are kept per customer (`react-<customerId>-<uuid>`), and each turn is prefixed with a hidden customer-context block that isn't shown in the bubble.

The runtime streams only text, tool use, stop reason, `app_event` objects and errors. `app_event` carries what the page renders beyond text:

- `form_open` / `form_fill`: open an application form beside the chat and fill fields. The chat panel and the form panel share the `useApplicationForm` state with the voice page, with the same precedence (manual > conversation > profile).
- `sources`: sent after the text. `SourcesFooter` shows one chip per source. Document chips open Data Explorer on that file in a **new browser tab** (`?page=data&folder=…&key=…`), because the chat lives inside the Assistant page and switching tabs would lose it; web chips open the page in a new tab.
- A `pricing`-folder file named in an answer opens as a rate card in Data Explorer.

The quick prompts include **meeting prep** ("Help me prepare for my meeting with this customer", or "…this prospect"). `useAgentChat.ts` folds these events into the message list.

### Text+Voice Assistant

One WebSocket per session. Browsers can't set headers on a WebSocket, so the JWT travels base64url-encoded in the `Sec-WebSocket-Protocol` subprotocol. Mic capture runs through an audio worklet; assistant transcript chunks are held back until the matching audio actually plays so text and speech stay in sync. Switching customer mid-session sends a control message rather than reconnecting.

A **consent bar** sits above the chat (`ConsentBar`). When the advisor starts the microphone the assistant asks whether the customer has agreed to the conversation being transcribed. The advisor answers aloud or presses Confirmed / Declined. The page tracks `pending`, `confirmed` and `declined` from `consent` events the runtime sends; changing customer sends `voice_set_customer` and the state returns to pending. Declined closes the socket and clears the on-screen conversation. On the first confirmation per customer per session the browser writes a short "Recording consent confirmed" interaction note to the profile through the notes API.

The left panel hosts the **application form**. When the agent calls `open_application_form`, the schema is pushed down the socket and fields arrive individually as the conversation covers them. Per-field confidence drives the styling — ≥0.85 fills green, below that fills amber with a "Looks right" confirmation. Fields already known from the customer record are prefilled, the conversation can override them, and anything typed by hand is never overwritten. **Download PDF** builds a printable application with the answers so far; the PDF libraries (`jspdf`, `html2canvas-pro`) are imported only when the button is used, so they stay out of the main bundle.

### Comparator

Pick a product type, select 2-4 products, and the comparator Lambda returns a structured side-by-side table. The per-customer recommender returns a coverage-gap analysis with the estimated dollar exposure for each gap. Both requests carry the active `locale`, so the generated prose comes back in the UI language rather than English.

- **Carrier financial strength.** Every compared carrier shows its AM Best / S&P / Moody's ratings in a dedicated row, in the table and in the PDF, with an "as of" date. Competitor ratings are labelled as illustrative demonstration data.
- **Customer PDF.** "Download PDF" produces an Awesome-branded handout (date, advisor, summary, ratings, tables, disclaimer) with no "Internal Use Only" marking. The on-screen badge stays. The printable view is mounted only while exporting.
- **Competitor brochure.** `BrochureUpload` lets the advisor upload one PDF from another insurer, or pick one of ten sample brochures offered as downloads. The upload goes through the normal document-upload route; `POST /comparator/ingest` reads it into a fixed structure and returns a `temp_id`, and the comparison is requested with that id, never with brochure content. The uploaded product is shown last with an "Uploaded brochure" badge, a notice that it was read automatically and is unverified, and its own ratings labelled as stated in the brochure. The reading is kept for a day.

### Data

Two panes over the reference corpus. The listing is fetched once on mount from `GET data/files` (metadata only); document bodies come from `GET data/file` lazily per selection and are cached for the session. Content is served by the `data` Lambda reading the S3 knowledge-base buckets — nothing is bundled into the SPA and the browser never touches S3 directly.

Nine folders are surfaced: Product Portfolio, Company Info, Competitive Positioning, Competitor Products, Promotions, Application Forms, Pricing Tables, Brochures, and Voice transcripts. The first eight are allowlisted and ordered server-side; `transcripts` is per advisor and never listed for anyone else. `mock-policies` is deliberately excluded — those ship as static files for the download menu instead. Text folders show only `.md` and `.json`; `brochures` holds PDFs.

- **Search** filters case-insensitively across filename, sub-path, folder id, and the translated folder label, with a live match count.
- **Markdown** renders through the shared `MarkdownMessage` component in its `doc` variant (full document type scale rather than chat scale). Front matter is stripped server-side and surfaced as a "Last updated" line.
- **Form-schema JSON** gets a proper rendered view: title, product name, form id, section/field/required counts, then one card per section listing each field's label, type, required flag, dotted path, hint, and select options. A Rendered/Raw pill toggles to the pretty-printed source. JSON that isn't a form schema falls back to source only, so the toggle never offers an empty form.
- **Carrier ratings** (`competitors/carrier_ratings.json`) render as a table (`CarrierRatingsView`): a row per carrier, a column per agency, rating with label and outlook, an "Illustrative" tag on the fictitious competitors, and the as-of date. The Raw toggle shows the JSON.
- **Folders fold.** Every folder header in the list is a button with a chevron (`aria-expanded`), and Collapse all / Expand all sits beside the file count. The folded set is kept in `localStorage` (`insadv.data.collapsedFolders.v1`). While a search is active all matches show, and opening a document from outside the list (a Sources link) reopens its folder.
- **Pricing tables** render as a rate card (`PricingCardView`): age bands, rates per unit of cover, factors, add-ons, what the price depends on, and an "Indicative only" label with the effective date. The Raw toggle shows the JSON.
- **Brochures** open in an in-page PDF viewer with Open in new tab and Download PDF. The PDFs come from 2-minute presigned S3 links returned by `GET data/file`, so no binary goes through API Gateway.
- **Voice transcripts** (`TranscriptView`) list only the signed-in advisor's sessions, newest first, with date and time, customer or Prospect, turn count and an "in progress" marker. The viewer is a chat-style transcript with times, a "typed" marker for typed turns, the customer for each stretch, and a banner with the expiry rule, last-saved time and consent time. Refresh reloads a session that is still running; Raw shows the JSON. Transcripts are kept for 24 hours.
- Only `competitive` and `competitors` documents carry the **Internal Use Only** badge, so an advisor doesn't screen-share a battlecard to a customer. Pricing tables, brochures and transcripts do not.

## Localization

Nine locales ship today, in switcher order:

| Code | Label |
|---|---|
| `en` | English |
| `ja` | 日本語 |
| `ko` | 한국어 |
| `es` | Español |
| `fr` | Français |
| `zh` | 简体中文 |
| `ms` | Bahasa Melayu |
| `th` | ไทย |
| `id` | Bahasa Indonesia |

All nine are at full key parity (422 keys each) — there are no partial locales. The switcher sits in the top-right nav cluster and enumerates `SUPPORTED_LOCALES` directly, so its order is the array order.

Language is detected from `localStorage` then `navigator`, cached to `localStorage` under `i18nextLng`, and restored on the next visit. Region variants are stripped before matching (`en-US` → `en`), with `en` as the fallback.

Per design, backend datastores and S3 markdown content stay English-only. The LLM handles multilingual prompts natively, so typing or clicking a Japanese quick-question produces a Japanese reply against an English knowledge base. The comparator and recommender go further and pass the active locale explicitly, since their output is generated rather than conversational.

### Structure

```
src/i18n/
  config.ts                        ← SUPPORTED_LOCALES, i18next init, detection
  locales/
    en/{common,auth,assistant,domain}.json
    ja/{common,auth,assistant,domain}.json
    ko/{common,auth,assistant,domain}.json
    es/{common,auth,assistant,domain}.json
    fr/{common,auth,assistant,domain}.json
    zh/{common,auth,assistant,domain}.json
    ms/{common,auth,assistant,domain}.json
    th/{common,auth,assistant,domain}.json
    id/{common,auth,assistant,domain}.json
  i18next.d.ts                     ← typed keys (autocomplete + compile-time checks)
```

The four namespace files per locale are merged into a single `translation` namespace with the namespace separator disabled, so callers always use a dotted path: `t('common.actions.send')`, `t('assistant.pages.data.heading')`.

Namespaces:

- `common` — generic UI (nav, buttons, states, errors)
- `auth` — sign-in / sign-up strings for the custom `AuthGate`. These must be complete: Amplify ships no translations for our own UI.
- `assistant` — page strings for all four tabs, chat UI, quick-question prompts, the advisor-vs-competitor section, the Data explorer (folders, rate cards, brochures, transcripts), the application form and its PDF, notes, recording consent, the Sources footer, and the Comparator's ratings and brochure upload
- `domain` — enum value translations (customer status, policy status, policy type, marital status, premium frequency)

Formatting (dates, numbers, currency) lives in `src/lib/format.ts` and is driven by the active locale via `Intl.*`.

### Adding a new locale

1. Create `src/i18n/locales/<code>/{common,auth,assistant,domain}.json` — copy the English bundles and translate the values. Keys must match exactly.
2. Add the four JSON imports and a `resources` entry in `src/i18n/config.ts`, plus an entry in `SUPPORTED_LOCALES` (code + display label). Position in that array determines position in the switcher.
3. Run `npm run typecheck` — a missing or misspelled key surfaces as a type error against `i18next.d.ts`.

No component changes are needed.

### Notes

- Customer data (names, addresses, free text) is rendered as-is. Not translated.
- Currency stays USD everywhere. Only formatting (thousand separators, symbol position) changes with locale.
- Enum values coming from the API (`Active`, `Married`, `Auto Insurance`, etc.) are mapped through `domain.json` before rendering — never displayed raw.

## Build notes

- **Vite 8** (Rolldown-based). Code splitting is configured through `build.rollupOptions.output.codeSplitting.groups` with regex matchers, because Vite 8 dropped the object form of `manualChunks`. `aws-amplify` and `i18n` are pulled into their own vendor chunks.
- **Tailwind CSS 4** via `@tailwindcss/vite`. There is no `tailwind.config.js` or `postcss.config.js` — v4 uses CSS-first config in `src/index.css`.
- **PDF export is lazy.** `src/lib/pdf.ts` imports `html2canvas-pro` and `jspdf` on first use, so neither is in the entry or tab chunks. Both the comparator handout and the form PDF go through it.
- All four pages are lazy-loaded. Auth, nav, and the context providers ship in the main chunk; each tab is fetched on first visit.
- `react-markdown` + `remark-gfm` for tables, task lists, strikethrough, and autolinks. Raw HTML is deliberately not enabled. Links get `target="_blank"` and `rel="noopener noreferrer"`. The renderer is memoized on content because streaming re-renders on every token.
