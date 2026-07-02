# Frictionless Insurance Advisor — React Frontend

Single-page React app that talks to the AgentCore Runtime (text + voice) and API Gateway backend. Runs locally for development; deployed to S3 + CloudFront with Origin Access Control by `insadv-05-frontend` for production.

## What it does

- Sign up / sign in through Amplify's `<Authenticator>` (backed by the Cognito user pool, gated by a hard-coded allowlist on the `/signup` Lambda)
- Lists the advisor's customers (scoped server-side via the DynamoDB `advisor-id-index` GSI)
- Shows each customer's profile and policies, including third-party coverage
- Streaming text chat with the AgentCore text runtime (Claude Haiku 4.5)
- Real-time voice chat with the AgentCore voice runtime (Nova Sonic 2)
- Side-by-side product comparator and per-customer recommender (direct Bedrock Converse Lambdas)
- Document upload (PDF / image / markdown) → structured policy extraction → field-by-field confirmation → save as third-party policy
- Five UI locales (English, Spanish, French, Japanese, Korean), language switcher in the top nav, persisted to `localStorage`

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
1. Run `./scripts/setup-env.sh` if `.env.local` is missing (reads SSM parameters into env vars)
2. `npm install`
3. Start Vite at http://localhost:5173

## Re-populate env vars after a backend redeploy

```bash
./scripts/setup-env.sh
```

## Auth flow

The Cognito user pool is provisioned with `self_sign_up_enabled=True`, but the AWS account's org policy blocks Cognito self-service signup. The React app posts to a backend `/signup` endpoint (`lambda/signup/`) which creates and confirms the user via Cognito admin APIs — guarded by a hard-coded allowlist (currently `john.doe@example.com`, `jane.doe@example.com`) and WAF rate-limited at 100 req / 5min / source IP.

Sign in with any allowlisted email + 12+ char password (upper/lower/digit/symbol). The browser only ever holds a Cognito JWT; no AWS IAM credentials are present in the SPA.

## Localization

Five locales ship today: English, Spanish, French, Japanese, Korean. Language switcher in the top-right of the nav, persisted to `localStorage` (`i18nextLng`) and restored on next visit.

Per design, backend datastores and S3 markdown content stay English-only. The LLM handles multilingual prompts natively, so typing or clicking a Japanese quick-question produces a Japanese reply; an English question produces an English reply.

### Structure

```
src/i18n/
  config.ts                        ← i18next init + Amplify Authenticator wiring
  locales/
    en/{common,auth,assistant,domain}.json
    es/{common,auth,assistant,domain}.json
    fr/{common,auth,assistant,domain}.json
    ja/{common,auth,assistant,domain}.json
    ko/{common,auth,assistant,domain}.json
  i18next.d.ts                     ← typed keys (autocomplete + compile-time checks)
```

Namespaces:

- `common` — generic UI (nav, buttons, states, errors)
- `auth` — Amplify Authenticator vocabulary overrides (keep minimal — Amplify ships its own translations)
- `assistant` — Assistant page strings, chat UI, quick-question prompts, "more questions" advisor-vs-competitor section
- `domain` — enum value translations (customer status, policy status, policy type, marital status, premium frequency)

Formatting (dates, numbers, currency) lives in `src/lib/format.ts` and is driven by the active locale via `Intl.*`.

### Adding a new locale

1. Create `src/i18n/locales/<code>/{common,auth,assistant,domain}.json` — copy the English bundles and translate values. Keys must match exactly.
2. Add the JSON imports and a `resources` entry to `src/i18n/config.ts`, plus an entry to `SUPPORTED_LOCALES` (code + display label).
3. Run `npm run build` — if any key is missing, the typed `t()` will surface it at compile time.

No component code changes needed — the language switcher enumerates options from `SUPPORTED_LOCALES`, and the Amplify `I18n` vocabulary is merged automatically on language change.

### Notes

- Customer data (names, addresses, free-text) is rendered as-is. Not translated.
- Currency stays USD everywhere. Only formatting (thousand separators, symbol position) changes with locale.
- Enum values coming from the API (`Active`, `Married`, `Auto Insurance`, etc.) are mapped through `domain.json` before rendering — never displayed raw.
