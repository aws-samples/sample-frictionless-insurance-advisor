# Frictionless Insurance Advisor — React Frontend

Single-page React app that talks to the AgentCore Runtimes (text + voice) and the API Gateway backend. Runs locally for development; deployed to S3 + CloudFront with Origin Access Control by `insadv-05-frontend` for production.

## What it does

Five tabs, switched from the top nav (no router — page state plus lazy-loaded chunks). **Home** is the first tab and the first screen after sign-in, except when the address carries a Sources link (`?page=data&folder=…&key=…`, opened by the chat's Sources chips), which starts on Data Explorer with that document selected. The active tab button has `aria-current="page"`, every tab button has an `aria-label` and a tooltip, and Left/Right (wrapping) and Home/End move focus along the row; Enter or Space then activates the focused tab. The nav adapts to the window width:

- Below 1024 px the tabs show only their icons; the text labels appear from 1024 px.
- The brand name and the signed-in email show from 1280 px. The logo is always there and does the same as the Home tab.
- Below 640 px the language switcher shrinks to an icon and the pitch deck button is hidden.

The tabs:

| Tab | Label | What it does |
|---|---|---|
| `home` | **Home** | Customer list on the left and the Cockpit (today's priorities) filling the rest of the width, with no assistant panel. A customer chosen in the list, a Cockpit button or **+ New Prospect** opens the Text Assistant. The Home tab and the logo behave the same: both go to Home and clear the selection (customer, prospect and any pending link), and both keep the open Text chats and each customer's unsent message. |
| `assistant` | **Text Assistant** | Streaming chat with the AgentCore text runtime (Claude Haiku 5.5). Per-customer history and session id. "New Prospect" mode for onboarding someone not yet on file. A **Sources** footer on answers that use documents or the web, a one-click **meeting-prep** quick prompt, pricing estimates, and application forms that open beside the chat and fill from the conversation. |
| `voice` | **Voice Assistant** | Real-time voice over WebSocket to the voice runtime (Nova 2.5 Sonic), with barge-in and pause / resume. A recording-consent bar gates memory, forms and transcripts. Application forms open and fill themselves from the conversation. The voice runtime has no guardrail (see the root README). |
| `comparator` | **Comparator** | Side-by-side table for 2-4 products with carrier financial-strength ratings and a customer handout PDF, an optional uploaded competitor brochure (or a sample one), plus a per-customer coverage-gap recommender with a dollar exposure per gap. Both pass the active locale so the model answers in the UI language. |
| `data` | **Data Explorer** | Read-only explorer over the `s3-data` corpus that grounds the agent's answers: searchable file list, rendered markdown, rendered form schemas and rate cards, PDF brochures, and the advisor's own voice transcripts and saved applications. |

Across all tabs:

- Sign in / sign up through a **custom auth UI** (`AuthGate`) backed by the Cognito user pool. The "Create account" tab appears only if the backend was deployed with the opt-in public sign-up route; by default there is sign-in only, and the demo users are created once with `scripts/create-demo-users.sh`.
- Collapsible customer sidebar on the Home, Text and Voice tabs (no Home button in it: Home is a tab; scoped server-side via the DynamoDB `advisor-id-index` GSI), with profile, Awesome policies, third-party coverage, and a **Notes** card (life events and interactions; add and delete by hand, notes added by the assistant are labelled). A **Verify current info** badge appears on the profile when it was last reviewed more than 90 days ago (`CustomerPanel`); it is about the customer record and has nothing to do with answer sources.
- Document upload (PDF / image / markdown / text) → structured policy extraction → field-by-field confirmation → save as a third-party policy.
- Sample third-party policies downloadable as `.md` or `.pdf` so the upload flow can be exercised without external files.
- Resizable split panes, with the divider position and sidebar state persisted per surface.
- **Ten UI locales** with a switcher in the top nav, persisted to `localStorage`.
- Dark-mode toggle, persisted to `localStorage` (defaults to light regardless of OS preference).
- Pitch deck button that opens the standalone deck at `/presentation/index.html` (see [Pitch deck](#pitch-deck)).

## Prerequisites

- Node.js 20.19+ (or 22.12+) and npm, as Vite 8 requires
- AWS credentials in the shell
- All backend stacks deployed (`insadv-01-auth` through `insadv-04-voice`)

## Run

```bash
cd react-frontend
./run_app.sh
```

The script will:

1. Run `./scripts/setup-env.sh` if `.env.local` is missing (reads SSM parameters into `VITE_*` vars)
2. `npm ci`
3. Start Vite at http://localhost:5173

## npm scripts

| Script | What it does |
|---|---|
| `dev` | Vite dev server. `predev` syncs sample policies first. |
| `build` | `tsc -b && vite build`. `prebuild` syncs sample policies first. |
| `preview` | Serve the production build locally |
| `typecheck` | `tsc --noEmit -p .` — key and type checking of `src/` without emitting (`vite.config.ts` is checked by `build`) |

There is no frontend test suite in this repository (no test runner or test files); `npm run typecheck` and `npm run build` are the checks. The build runs `tsc -b`, so it type-checks `src/` and `vite.config.ts` before Vite bundles.

`scripts/sync-mock-policies.sh` runs automatically on both `predev` and `prebuild`. It copies `s3-data/mock-policies/*.{md,pdf}` into `public/mock-policies/` and prunes orphans, so the in-app "sample policies" menu always matches the source of truth. The PDFs themselves are generated artefacts — `s3-data/mock-policies/build-pdfs.sh` (called from the repo-root `deploy.sh`) builds them from the markdown.

## Environment variables

`scripts/setup-env.sh` writes `.env.local` from SSM. Re-run it after any backend redeploy:

```bash
./scripts/setup-env.sh
```

It **overwrites** `.env.local` every time, so keep personal additions elsewhere. `.env.local` is git-ignored; `.env.example` lists the variable names.

| Variable | Source | Read by |
|---|---|---|
| `VITE_AWS_REGION` | `$AWS_REGION`, defaults to `us-east-1` | runtime URLs (`src/lib/runtimeEndpoint.ts`, `src/lib/voice.ts`) and the `preconnect` link in `index.html` |
| `VITE_COGNITO_USER_POOL_ID` | `/insurance-advisor/cognito/user-pool-id` | `src/amplify-config.ts` |
| `VITE_COGNITO_CLIENT_ID` | `/insurance-advisor/cognito/app-client-id` | `src/amplify-config.ts` |
| `VITE_API_BASE_URL` | `/insurance-advisor/api/gateway-url` | `src/lib/api.ts` |
| `VITE_AGENT_RUNTIME_ARN` | `/insurance-advisor/agentcore/runtime-arn` | Text Assistant chat and warm-up (`src/lib/runtimeEndpoint.ts`) |
| `VITE_VOICE_RUNTIME_ARN` | `/insurance-advisor/voice/runtime-arn` (optional, written empty if missing) | `src/lib/voice.ts` |
| `VITE_ENABLE_SIGNUP` | `/insurance-advisor/frontend/signup-enabled` (`false` if missing) | `AuthGate`: `true` shows the "Create account" tab |

The runtime ARNs and the region are baked in at build time, so a runtime replacement means rebuilding the SPA — which is why `deploy.sh` always runs `setup-env.sh` before `npm run build`. Without `.env.local`, `npm run build` still succeeds but warns that `%VITE_AWS_REGION%` is not defined in `index.html`, and the built app cannot reach the backend.

## Auth flow

Auth UI is hand-rolled (`src/components/AuthGate.tsx`), not Amplify's `<Authenticator>`. Two reasons: the user pool is closed to self-service sign-up (`AllowAdminCreateUserOnly`), which Amplify's component does not expect; and the custom UI matches the rest of the design system. Only `aws-amplify/auth` functions (`signIn`, `signOut`, `getCurrentUser`, `fetchAuthSession`) are used — `@aws-amplify/ui-react` is not a dependency.

Public sign-up is off by default. With `-c enable_public_signup=true` the backend adds a `/signup` endpoint (`lambda/signup/`) that creates and confirms users for the allowlisted addresses only, rate-limited by WAF; the build then sets `VITE_ENABLE_SIGNUP=true` (written by `scripts/setup-env.sh`) and the sign-in screen shows a "Create account" tab.

Sign in with a demo advisor (created once with `scripts/create-demo-users.sh`) and its password (upper / lower / digit / symbol). The browser only ever holds a Cognito JWT; no AWS IAM credentials are present in the SPA. The same token authenticates both the direct AgentCore Runtime calls and the API Gateway calls.

## Pages in detail

### Text Assistant

`streamChat()` posts straight to `bedrock-agentcore.<region>.amazonaws.com/runtimes/<arn>/invocations` with `Accept: text/event-stream` and yields tokens as they arrive. `advisorId` is deliberately never sent — the runtime derives identity from the verified JWT, so the browser cannot impersonate another advisor. History and session id are kept per customer (`react-<customerId>-<uuid>`) in memory, inside the page (`useAgentChat`). Once the Text page has been opened, `App` keeps it mounted and hides it (`hidden` + `inert`) while another tab is active, so the on-screen chat and a response that is still streaming survive a visit to another tab; a reload or signing out clears it. The chat history and the unsent text of the message box (the draft, kept per customer with its own slot for a new prospect, dropped when the message is sent, never written to `localStorage`) are held by `AssistantPage` (`useAgentChat`, passed to `ChatPanelView`), not by the chat panel, so they also survive the Home tab and the logo, which are the same action: both clear the selection, unmount the panel and leave the page mounted. The Voice, Comparator and Data pages are rendered only while active, so leaving the Voice tab ends its session. Each turn is prefixed with a hidden customer-context block that isn't shown in the bubble. The agent writes a short lead-in, calls its tools, then writes the answer as a separate turn; `streamChat()` also yields a `tool_use` item, and the chat puts a blank line between the text before and after a tool call so a Markdown heading in the answer still renders.

`src/lib/sse.ts` turns the stream into three kinds of item: text chunks, tool-use markers and `app_event` objects. Everything else the runtime sends (stop reason, error lines) is ignored by the browser. `app_event` carries what the page renders beyond text:

- `form_open` / `form_fill`: open an application form beside the chat and fill fields. The chat panel and the form panel share the `useApplicationForm` state with the voice page, with the same precedence (manual > conversation > profile).
- `sources`: sent after the text. `SourcesFooter` shows one chip per source. Document chips open Data Explorer on that file in a **new browser tab** (`?page=data&folder=…&key=…`), so the chat on screen is left as it is; web chips open the page in a new tab. Only `http(s)` links and known folders are accepted (`src/lib/sources.ts`), at most 8 chips, and a trailing "Sources:" line in the answer text is hidden when chips are shown.
- `guardrail_blocked`: the compliance guardrail replaced the answer. See below.
- A `pricing`-folder file named in an answer opens as a rate card in Data Explorer.

The quick prompts include **meeting prep** ("Help me prepare for my meeting with this customer", or "…this prospect"). `useAgentChat.ts` folds these events into the message list.

**Guardrail and streaming.** By default the text runtime streams the guardrail in `async` mode (see [Guardrail stream mode](../README.md#guardrail-stream-mode-async-by-default) in the root README), so the answer appears progressively while the guardrail checks it in the background. If the check blocks the answer, a `guardrail_blocked` event arrives after the text: the bubble is then replaced by a translated notice (`assistant.chat.guardrailBlocked`) and the Sources list for that answer is not shown. A message the guardrail blocks before the model runs (for example one that contains a password, or asks for legal or tax advice) is answered at once: the runtime sends the guardrail's sentence and the same event, so the advisor sees the notice immediately. A backend that does not send the event shows the guardrail's own sentence instead. After a block, Strands stores the user's message in AgentCore Memory as "[User input redacted.]", so the next turn of the same chat continues from a redacted history.

**Warm-up.** A chat session id has its own container on the runtime, and starting one takes a few seconds. When the Text Assistant is open and visible, `ChatPanelView` calls `warmUpSession()` (`src/lib/warmup.ts`): a silent `POST` of `{"warmup": true}` to the runtime with the chat's session id and the access token. The request carries no question, so nothing enters the chat history or AgentCore Memory. It is sent at most once per session id every 5 minutes (a real message counts as a warm-up), not while signed out or while the Text tab is hidden, and any error is swallowed. `index.html` also has a `<link rel="preconnect">` to `bedrock-agentcore.<region>.amazonaws.com` (Vite fills the region from `.env.local`) so the TLS connection is already open for the first request.

**Personal data in the browser.** The browser masks nothing in the text of a chat message. The agent masks typed PII (SSN, card numbers and the like) before it stores or logs the message, as described in the root README under [Typed PII is masked before it is stored](../README.md#typed-pii-is-masked-before-it-is-stored). The Social Security number on an application form is different: it is typed in a masked field on the form screen (`FormPanel`, `sensitivity: secret` in `src/lib/formRules.ts`), is left out of the form context sent with chat messages, is never sent to either assistant, and is not part of the saved application.

### Voice Assistant

One WebSocket per session. Browsers can't set headers on a WebSocket, so the JWT travels base64url-encoded in the `Sec-WebSocket-Protocol` subprotocol. Mic capture runs through an audio worklet; assistant transcript chunks are held back until the matching audio actually plays so text and speech stay in sync. Switching customer mid-session sends a control message rather than reconnecting. Speaking over the assistant interrupts it (barge-in): the runtime sends an interruption event and the page drops the text still waiting for its audio. **Pause** switches the microphone off and silences the assistant at once, but the session stays open (typing, the transcript, consent and the form carry on, with replies as text). The voice model ends a session after about 5 minutes without speech or typing, so while paused a countdown runs, and resuming after that starts a new session.

A **consent bar** sits above the chat (`ConsentBar`). When the advisor starts the microphone the assistant asks whether the customer has agreed to the conversation being transcribed. The advisor answers with the Confirmed / Declined buttons; a spoken answer does not change the state. The page tracks `pending`, `confirmed` and `declined` from `consent` events the runtime sends; changing customer sends `voice_set_customer` and the state returns to pending. Declined closes the socket and clears the on-screen conversation. Nothing is written to the customer's notes when consent is confirmed; the consent time is recorded in the transcript header only.

The left panel hosts the **application form**. When the agent calls `open_application_form`, the schema is pushed down the socket and fields arrive individually as the conversation covers them. Per-field confidence drives the styling — ≥0.85 fills green, below that fills amber with a "Looks right" confirmation. Fields already known from the customer record are prefilled, the conversation can override them, and anything typed by hand is never overwritten. **Download PDF** builds a printable application with the answers so far; the PDF libraries (`jspdf`, `html2canvas-pro`) are imported only when the button is used, so they stay out of the main bundle.

### Comparator

Pick a product type, select 2-4 products, and the comparator Lambda returns a structured side-by-side table. The per-customer recommender returns a coverage-gap analysis with the estimated dollar exposure for each gap. Both requests carry the active `locale`, so the generated prose comes back in the UI language rather than English.

- **Carrier financial strength.** Every compared carrier shows its AM Best / S&P / Moody's ratings in a dedicated row, in the table and in the PDF, with an "as of" date. Competitor ratings are labelled as illustrative demonstration data.
- **Customer PDF.** "Download PDF" produces an Awesome-branded handout (date, advisor, summary, ratings, tables, disclaimer) with no "Internal Use Only" marking. The on-screen badge stays. The printable view is mounted only while exporting.
- **Competitor brochure.** `BrochureUpload` lets the advisor upload one PDF from another insurer, or pick one of eleven sample brochures offered as downloads. The upload goes through the normal document-upload route; `POST /comparator/ingest` reads it into a fixed structure and returns a `temp_id`, and the comparison is requested with that id, never with brochure content. The uploaded product is shown last with an "Uploaded brochure" badge, a notice that it was read automatically and is unverified, and its own ratings labelled as stated in the brochure. The reading is kept for a day.

### Data

Two panes over the reference corpus. The listing is fetched once on mount from `GET data/files` (metadata only); document bodies come from `GET data/file` lazily per selection and are cached for the session. Content is served by the `data` Lambda reading the S3 knowledge-base buckets — nothing is bundled into the SPA and the browser never touches S3 directly.

Ten folders are surfaced: Product Portfolio, Company Info, Competitive Positioning, Competitor Products, Promotions, Application Forms, Pricing Tables, Brochures, Voice transcripts and Saved applications. The first eight are allowlisted and ordered server-side; `transcripts` and `applications` are per advisor and never listed for anyone else. `mock-policies` is deliberately excluded — those ship as static files for the download menu instead. Text folders show only `.md` and `.json`; `brochures` holds PDFs.

- **Search** filters case-insensitively across filename, sub-path, folder id, and the translated folder label, with a live match count.
- **Markdown** renders through the shared `MarkdownMessage` component in its `doc` variant (full document type scale rather than chat scale). Front matter is stripped server-side and surfaced as a "Last updated" line.
- **Form-schema JSON** gets a proper rendered view: title, product name, form id, section/field/required counts, then one card per section listing each field's label, type, required flag, dotted path, hint, and select options. A Rendered/Raw pill toggles to the pretty-printed source. JSON that isn't a form schema falls back to source only, so the toggle never offers an empty form.
- **Carrier ratings** (`competitors/carrier_ratings.json`) render as a table (`CarrierRatingsView`): a row per carrier, a column per agency, rating with label and outlook, an "Illustrative" tag on the fictitious competitors, and the as-of date. The Raw toggle shows the JSON.
- **Folders fold.** Every folder header in the list is a button with a chevron (`aria-expanded`), and Collapse all / Expand all sits beside the file count. The folded set is kept in `localStorage` (`insadv.data.collapsedFolders.v1`). While a search is active all matches show, and opening a document from outside the list (a Sources link) reopens its folder.
- **Pricing tables** render as a rate card (`PricingCardView`): age bands, rates per unit of cover, factors, add-ons, what the price depends on, and an "Indicative only" label with the effective date. The Raw toggle shows the JSON.
- **Brochures** open in an in-page PDF viewer with Open in new tab and Download PDF. The PDFs come from 2-minute presigned S3 links returned by `GET data/file`, so no binary goes through API Gateway.
- **Voice transcripts** (`TranscriptView`) list only the signed-in advisor's sessions, newest first, with date and time, customer or Prospect, turn count and an "in progress" marker. The viewer is a chat-style transcript with times, a "typed" marker for typed turns, the customer for each stretch, and a banner with the expiry rule, last-saved time and consent time. Refresh reloads a session that is still running; Raw shows the JSON. Transcripts expire after one day (24 to 48 hours after the last save).
- Only `competitive` and `competitors` documents carry the **Internal Use Only** badge, so an advisor doesn't screen-share a battlecard to a customer. Pricing tables, brochures and transcripts do not.

## Localization

Ten locales ship today, in switcher order:

| Code | Label |
|---|---|
| `en` | English |
| `ja` | 日本語 |
| `ko` | 한국어 |
| `es` | Español |
| `fr` | Français |
| `de` | Deutsch |
| `zh` | 简体中文 |
| `ms` | Bahasa Melayu |
| `th` | ไทย |
| `id` | Bahasa Indonesia |

All ten have the same keys as `en` — there are no partial locales. No script or build step enforces this parity; compare the key sets yourself after editing a locale. The switcher sits in the top-right nav cluster and enumerates `SUPPORTED_LOCALES` directly, so its order is the array order.

Language is detected from `localStorage` then `navigator`, cached to `localStorage` under `i18nextLng`, and restored on the next visit. Region variants are stripped before matching (`en-US` → `en`), with `en` as the fallback.

The pitch deck is translated into the same ten languages, independently of the app; see [Pitch deck](#pitch-deck) below.

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
    de/{common,auth,assistant,domain}.json
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
3. Run `npm run typecheck`. Keys are typed from the English files (`i18next.d.ts`), so a `t()` call with a misspelled or missing key fails; a key missing from the new locale does not, so compare its keys with `en` yourself.
4. Add the code to `SUPPORTED_LOCALES` and the language name to the name map in `lambda/comparator`, `lambda/recommend` and `lambda/meeting_summary` (the last one also has the three section labels of a saved meeting note), then redeploy the tools stack. Without this those three features answer in English for the new language.
5. Also add `public/presentation/i18n/<code>.json` (same keys as `en.json`), an `<option>` in the deck's `#deck-lang` picker in `public/presentation/index.html`, and the code to the `SUPPORTED` list in `public/presentation/deck-i18n.js`. Without this the pitch deck stays in English for the new language.
6. Add the code and the language's English name to `UI_REPLY_LANGUAGES` in `agent/app.py`, and the language's name, instruction and fixed lead-in sentence to `UI_REPLY_LANGUAGE_NATIVE`, then redeploy the text runtime. Without this the Text Assistant does not get the new language and replies in the language of the advisor's message.

No component changes are needed.

### Notes

- Customer data (names, addresses, free text) is rendered as-is. Not translated.
- Currency stays USD everywhere. Only formatting (thousand separators, symbol position) changes with locale.
- Enum values coming from the API (`Active`, `Married`, `Auto Insurance`, etc.) are mapped through `domain.json` before rendering — never displayed raw.

## Pitch deck

The 12-slide deck is a static page, `public/presentation/index.html`, copied as is into `dist/` and served at `/presentation/index.html` (the top nav's slideshow button opens it in a new tab with the app's current language). See [Sales pitch deck](../README.md#sales-pitch-deck) in the root README for how it is meant to be used.

- **Self-hosted.** reveal.js 5.1.0, its speaker-notes plugin and the fonts are under `public/presentation/vendor/` (see the README there), so the deck makes no third-party request.
- **Own Content-Security-Policy.** CloudFront applies a strict CSP to `/presentation/*` only (`DECK_CSP` in `cdk/frontend_stack.py`; every directive limited to `'self'`, with inline script and style allowed, no `unsafe-eval`). The React app has its own policy (`build_app_csp` in the same file, see Security headers below).
- **Controls** (`deck-ui.js`): a full screen button (shown only where the browser allows full screen), `F` to toggle full screen, `?` or `F1` for a translated shortcut overlay, `Esc` to close it, and an auto-hide of the controls in full screen. `S` opens reveal.js speaker notes, `O` the overview, `B` pauses, and the arrows move between slides.
- **Speaker notes** are `<aside class="notes">` lists in each slide. Their items use the keys `n<slide>.b<n>`.
- **Languages** (`deck-i18n.js`, `i18n/<code>.json`): the same ten as the app. The text keys are the `data-i18n` attributes in `index.html`, and the English text stays in the HTML as a fallback. The language is chosen in this order: `?lang=` in the URL, the deck picker's saved choice (`deckLang` in `localStorage`), the app's `i18nextLng` (read only), the browser language, English. Only `span.grad-text`, `strong`, `em`, `br` and `code` are allowed as markup in the JSON values, and every language needs the same keys as `en.json`, speaker notes included.

## Build notes

- **React 18**, **TypeScript 5.9**, **Vite 8** (Rolldown-based). `package.json` pins every dependency to an exact version and the lockfile is committed, so a version bump is always a reviewable change; an `overrides` entry holds `esbuild` at 0.25.x. Vite 8 needs Node.js 20.19+ or 22.12+.
- **Vite 8 chunking.** Code splitting is configured through `build.rollupOptions.output.codeSplitting.groups` with regex matchers, because Vite 8 (Rolldown) uses that form rather than the object form of `manualChunks`. `aws-amplify` and `i18n` are pulled into their own vendor chunks.
- **Fonts.** The app (not the deck) serves Inter (400 to 700) and JetBrains Mono (400 to 500) from `public/fonts/` as variable woff2 files, Latin and Latin-extended subsets, with their SIL Open Font License texts next to them. The `@font-face` rules are at the top of `src/index.css`. The file names carry the font version, so CloudFront can cache them as immutable. Japanese, Korean, Chinese and Thai text uses system fonts.
- **Audio worklet.** `vite.config.ts` keeps `audio-processor.worklet.js` out of Vite's data: URL inlining, because `audioWorklet.addModule()` has to load it as a same-origin file under `script-src 'self'`.
- **Security headers.** CloudFront sends the app's Content-Security-Policy, Permissions-Policy (microphone for the app's own origin only), `X-Frame-Options: DENY` and HSTS (18 months, subdomains) with every response. The CSP is built in `cdk/frontend_stack.py` from the stack's own region, REST API id and the documents and brochures buckets: scripts and styles from `'self'` only (no inline script, no `unsafe-eval`), `connect-src` limited to Cognito, the AgentCore runtimes (HTTPS and `wss://`), the REST API and the documents bucket, `object-src`/`frame-src` limited to the brochures bucket (PDF viewer), `frame-ancestors 'none'`. A new call to another host needs a matching entry there.
- **Caching.** `assets/*` and `fonts/*` are uploaded with `Cache-Control: public, max-age=31536000, immutable`; every other object, `index.html` included, with `no-cache`. An unknown path returns `404.html` with status 404 (the app has no client-side routes, so there is no fallback to `index.html`).
- **Tailwind CSS 4** via `@tailwindcss/vite`. There is no `tailwind.config.js` or `postcss.config.js` — v4 uses CSS-first config in `src/index.css`.
- **PDF export is lazy.** `src/lib/pdf.ts` imports `html2canvas-pro` and `jspdf` on first use, so neither is in the entry or tab chunks. Both the comparator handout and the form PDF go through it.
- All five pages are lazy-loaded. Auth, nav, and the context providers ship in the main chunk; each tab is fetched on first visit. After its first visit the Text page stays mounted (hidden and `inert`) behind the other tabs; the other four pages (Home, Voice, Comparator, Data) are mounted only while active.
- `react-markdown` + `remark-gfm` for tables, task lists, strikethrough, and autolinks. Raw HTML is deliberately not enabled. Links get `target="_blank"` and `rel="noopener noreferrer"`. The renderer is memoized on content because streaming re-renders on every token.
