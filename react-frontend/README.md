# Frictionless Insurance Advisor — React Frontend

Single-page React app that talks to the AgentCore Runtimes (text + voice) and the API Gateway backend. Runs locally for development; deployed to S3 + CloudFront with Origin Access Control by `insadv-05-frontend` for production.

## What it does

Four tabs, switched from the top nav (no router — page state plus lazy-loaded chunks):

| Tab | Label | What it does |
|---|---|---|
| `assistant` | **Text Assistant** | Streaming chat with the AgentCore text runtime (Claude Haiku 4.5). Per-customer history and session id. "New Prospect" mode for onboarding someone not yet on file. |
| `voice` | **Text+Voice Assistant** | Real-time voice over WebSocket to the voice runtime (Nova Sonic 2), with barge-in. Application forms open and fill themselves from the conversation. |
| `comparator` | **Comparator** | Side-by-side table for 2-4 products, plus a per-customer coverage-gap recommender. Both pass the active locale so the model answers in the UI language. |
| `data` | **Data** | Read-only explorer over the `s3-data` corpus that grounds the agent's answers. Searchable file list, rendered markdown, and a rendered/raw toggle for form-schema JSON. |

Across all tabs:

- Sign in / sign up through a **custom auth UI** (`AuthGate`) backed by the Cognito user pool. Sign-up posts to the `/signup` Lambda, which is gated by a hard-coded allowlist.
- Collapsible customer sidebar (scoped server-side via the DynamoDB `advisor-id-index` GSI), with profile, Unicorn policies, and third-party coverage.
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

Auth UI is hand-rolled (`src/components/AuthGate.tsx`), not Amplify's `<Authenticator>`. Two reasons: the Cognito user pool is configured with `AllowAdminCreateUserOnly`, which breaks Amplify's self-service sign-up; and the custom UI matches the rest of the design system. Only `aws-amplify/auth` functions (`signIn`, `signOut`, `getCurrentUser`, `fetchAuthSession`) are used — `@aws-amplify/ui-react` is not a dependency.

Sign-up posts to a backend `/signup` endpoint (`lambda/signup/`) which creates and confirms the user via Cognito admin APIs — guarded by a hard-coded allowlist (currently `john.doe@example.com`, `jane.doe@example.com`) and WAF rate-limited at 100 req / 5 min / source IP.

Sign in with an allowlisted email and a 12+ char password (upper / lower / digit / symbol). The browser only ever holds a Cognito JWT; no AWS IAM credentials are present in the SPA. The same token authenticates both the direct AgentCore Runtime calls and the API Gateway calls.

## Pages in detail

### Text Assistant

`streamChat()` posts straight to `bedrock-agentcore.<region>.amazonaws.com/runtimes/<arn>/invocations` with `Accept: text/event-stream` and yields tokens as they arrive. `advisorId` is deliberately never sent — the runtime derives identity from the verified JWT, so the browser cannot impersonate another advisor. History and session id are kept per customer (`react-<customerId>-<uuid>`), and each turn is prefixed with a hidden customer-context block that isn't shown in the bubble.

### Text+Voice Assistant

One WebSocket per session. Browsers can't set headers on a WebSocket, so the JWT travels base64url-encoded in the `Sec-WebSocket-Protocol` subprotocol. Mic capture runs through an audio worklet; assistant transcript chunks are held back until the matching audio actually plays so text and speech stay in sync. Switching customer mid-session sends a control message rather than reconnecting.

The left panel hosts the **application form**. When the agent calls `open_application_form`, the schema is pushed down the socket and fields arrive individually as the conversation covers them. Per-field confidence drives the styling — ≥0.85 fills green, below that fills amber with a "Looks right" confirmation. Fields already known from the customer record are prefilled, the conversation can override them, and anything typed by hand is never overwritten.

### Comparator

Pick a product type, select 2-4 products, and the comparator Lambda returns a structured side-by-side table. The per-customer recommender returns a coverage-gap analysis. Both requests carry the active `locale`, so the generated prose comes back in the UI language rather than English.

### Data

Two panes over the reference corpus. The listing is fetched once on mount from `GET data/files` (metadata only); document bodies come from `GET data/file` lazily per selection and are cached for the session. Content is served by the `data` Lambda reading the S3 knowledge-base buckets — nothing is bundled into the SPA and the browser never touches S3 directly.

Six folders are surfaced, allowlisted and ordered server-side: Product Portfolio, Company Info, Competitive Positioning, Competitor Products, Promotions, and Application Forms. `mock-policies` is deliberately excluded — those ship as static files for the download menu instead. Only `.md` and `.json` are browsable.

- **Search** filters case-insensitively across filename, sub-path, folder id, and the translated folder label, with a live match count.
- **Markdown** renders through the shared `MarkdownMessage` component in its `doc` variant (full document type scale rather than chat scale). Front matter is stripped server-side and surfaced as a "Last updated" line.
- **Form-schema JSON** gets a proper rendered view: title, product name, form id, section/field/required counts, then one card per section listing each field's label, type, required flag, dotted path, hint, and select options. A Rendered/Raw pill toggles to the pretty-printed source. JSON that isn't a form schema falls back to source only, so the toggle never offers an empty form.
- Documents from the `competitive` and `competitors` folders carry an **Internal Use Only** badge, so an advisor doesn't screen-share a battlecard to a customer.

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

All nine are at full key parity (273 keys each) — there are no partial locales. The switcher sits in the top-right nav cluster and enumerates `SUPPORTED_LOCALES` directly, so its order is the array order.

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
- `assistant` — page strings for all four tabs, chat UI, quick-question prompts, the advisor-vs-competitor section, the Data explorer, and the application form
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
- All four pages are lazy-loaded. Auth, nav, and the context providers ship in the main chunk; each tab is fetched on first visit.
- `react-markdown` + `remark-gfm` for tables, task lists, strikethrough, and autolinks. Raw HTML is deliberately not enabled. Links get `target="_blank"` and `rel="noopener noreferrer"`. The renderer is memoized on content because streaming re-renders on every token.
