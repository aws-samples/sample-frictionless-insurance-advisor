# Frictionless Insurance Advisor

**Frictionless Insurance Advisor** is an AI co-pilot for insurance advisors at the fictional Unicorn Insurance, built end-to-end on Amazon Bedrock AgentCore.

> An AI co-pilot that gives every advisor instant, coverage-aware insight into each customer's portfolio, products, and competitors — so they walk into every conversation already prepared.

Advisors sign in to a React app and either chat with the agent or talk to it in real time. The agent reaches across nine MCP tools — customer profiles, policies, the Unicorn product catalog, current promotions, company facts, competitive talking points, competitor product references, policy-document extraction, and life-product application forms — to answer questions grounded in real customer data. It tracks third-party policies the customer holds elsewhere so cross-sell suggestions stay coverage-aware, can onboard a brand-new prospect just by talking, accepts uploaded policy documents (PDF / image / markdown) and extracts structured fields the advisor confirms before saving, and remembers context per customer across sessions through AgentCore Memory. Separate comparator and recommender Lambdas generate side-by-side product views and structured coverage-gap analyses on demand, and a built-in data explorer lets the advisor read the same reference documents the agent is grounded in.

The app is available in **nine languages**, and every surface — chat, voice, comparator, recommender, and the UI itself — responds in the advisor's chosen locale.

## Why it matters

| For the advisor | For the customer | For the business |
|---|---|---|
| No more portal-flipping. Profile, gaps, promos, and competitive answers ready the moment a customer is selected. Drag a competitor PDF in and the policy is on file in seconds. | Faster, more personal meetings. Advice grounded in actual coverage. Confident answers about competitors. | Shorter prep time. Higher-quality cross-sell. Faster new-advisor ramp. PII, compliance and "no quote / no bind" boundaries baked in. |

## Models

| Surface | Model | Why |
|---|---|---|
| Conversational text agent | Anthropic **Claude Haiku 4.5** | Fast, low-cost multi-turn reasoning with tool use |
| Voice agent | Amazon **Nova Sonic 2** | Bidirectional voice with low end-to-end latency |
| Comparator + recommender Lambdas | Anthropic **Claude Haiku 4.5** | Fast structured-output generation for tool-forced JSON |
| Document extraction (vision) | Anthropic **Claude Sonnet 5** | PDF/image vision for third-party policy field extraction |

## Architecture

![Frictionless Insurance Advisor — AWS architecture](architecture.png)

> Rendered from the `architecture.drawio` source.

```
       React + Vite SPA                                Cognito user pool
       (CloudFront-hosted, or `npm run dev` locally)   (advisors)
                │
                │  Cognito JWT (Bearer)
                │
                ├─────────────────────────────────────────────────────┐
                │                                                     │
                ▼                                                     ▼
   AgentCore Runtime (text)                              API Gateway + WAF
   AgentCore Runtime (voice)                             (profile, policy,
   container · ARM64                                     catalog, comparator,
   Strands · Haiku / Nova Sonic                          recommend, signup,
                │                                        documents, data)
                │  M2M JWT via AgentCore Identity Token Vault
                ▼
   AgentCore Gateway (MCP, 9 tool targets)
                │
                ├── OAuth2 → API Gateway → λ profile, λ policies → DynamoDB
                ├── IAM    → λ portfolio · promotions · company · competitive · competitors → S3
                └── IAM    → λ extract_policy → S3 (uploaded docs) → Bedrock vision
                ▲
                │  + AgentCore Memory (summaries · preferences · facts)
                │  + Bedrock Guardrail (content + topics + grounding + PII)
```

## CDK stacks

Five stacks, deployed in order:

| Stack | Purpose |
|---|---|
| `insadv-01-auth` | Cognito user pool (advisors) + gateway pool (M2M) + app + runtime + gateway clients |
| `insadv-02-tools` | DynamoDB profiles + policies + catalog tables; 16 Lambdas (profile, policies, portfolio, promotions, company, competitive, competitors, catalog, comparator, recommend, signup, documents, extract_policy, formschema, data, mock_data); API Gateway with WAF (rate-limited /signup); S3 markdown knowledge bases + uploads bucket; **shared Bedrock Guardrail** consumed by every model surface; AWS Budget on Bedrock + Lambda spend; KMS key for log encryption |
| `insadv-03-agentcore` | AgentCore Runtime (Claude Haiku 4.5 text agent), Gateway, nine tool targets, AgentCore Identity OAuth2 credential providers, long-term memory (summary + preference + semantic strategies, 90-day TTL) |
| `insadv-04-voice` | AgentCore Runtime (Nova Sonic 2 voice agent), shares the same gateway, memory, guardrail and Cognito as the text runtime |
| `insadv-05-frontend` | S3 site bucket + CloudFront distribution with Origin Access Control hosting the React build |

`deploy.sh` handles the ordering: backend stacks first (parallel where the DAG allows via `--concurrency 4`), then `npm run build` against fresh SSM env vars, then the frontend stack. The frontend stack only synthesises after `react-frontend/dist/` exists, so a cold `cdk deploy insadv-01-auth` still works on a clean checkout.

Both AgentCore runtimes are plain `AWS::BedrockAgentCore::Runtime` (L1 `CfnRuntime`). They previously went through an `AwsCustomResource` wrapper plus a post-deploy script, because `requestHeaderConfiguration` was not exposed by CloudFormation and the provider Lambda's deploy-time SDK silently dropped the field — leaving `Authorization` not forwarded into the container. It is now a first-class CloudFormation property, so the allowlist is declared in the stack and no post-deploy reconciliation is needed.

## Agent tools (MCP)

Nine tools exposed through the AgentCore Gateway. Read-only for Unicorn data, write-enabled for prospect onboarding and third-party policy tracking only.

| Tool | Source | Purpose |
|---|---|---|
| `get_profile` / `create_profile` / `update_profile` | Profile Lambda → DynamoDB | Customer profile read + prospect onboarding |
| `get_policy` / `create_third_party_policy` / `update_third_party_policy` / `delete_third_party_policy` | Policies Lambda → DynamoDB | Customer policy read + third-party policy CRUD (Unicorn-issued policies stay read-only) |
| `get_portfolio` | Portfolio Lambda → S3 | Unicorn product catalog |
| `get_promotions` | Promotions Lambda → S3 | Current promotions |
| `get_company_info` | Company Lambda → S3 | Unicorn — history, ratings, claims, support |
| `get_competitive_info` | Competitive Lambda → S3 | Unicorn's competitive talking points and head-to-head Q&A |
| `get_competitor_products` | Competitors Lambda → S3 | Reference info on fictional competitors BigRival, StarInsure, QuickSafe |
| `extract_policy_from_document` | Extract Policy Lambda → S3 + Bedrock vision | Reads an uploaded PDF/image/markdown and returns structured policy fields with defense-in-depth validators (numeric clamps, date sanity, injection-keyword heuristic, insurer rejection) |
| `get_form_schema` | Form Schema Lambda → S3 | Application-form schema for a life product (sections, fields, types, required flags). `product_type` is checked against an allowlist before it becomes an S3 key; unknown values return the published list so the agent can self-correct |

The comparator (side-by-side product table) and recommender (coverage-gap analysis) are direct Bedrock Converse calls from their own Lambdas behind the API Gateway, not MCP tools — they're one-shot structured generations that don't benefit from the runtime's tool-use loop.

## Frontend surfaces

Four tabs in the React app, each lazy-loaded:

| Tab | Backed by | What the advisor does |
|---|---|---|
| **Text Assistant** | AgentCore text runtime (SSE) | Streaming chat, per-customer history, document upload, "New Prospect" onboarding |
| **Text+Voice Assistant** | AgentCore voice runtime (WebSocket) | Real-time voice with barge-in, plus a self-filling application form |
| **Comparator** | Comparator + recommender Lambdas | Side-by-side view of 2-4 products; per-customer coverage-gap analysis |
| **Data** | `data` Lambda → S3 knowledge bases | Browse and read the reference documents the agent is grounded in |

The customer sidebar, dark-mode toggle, language switcher, and a pitch-deck link are shared across tabs. Panel sizes and sidebar state persist to `localStorage`.

### Data explorer

The Data tab is a read-only window onto the same `s3-data` corpus the agent reads, so an advisor can check the underlying source rather than taking a generated answer on faith.

Six folders are surfaced — Product Portfolio, Company Info, Competitive Positioning, Competitor Products, Promotions, and Application Forms. The folder list is allowlisted and ordered server-side in the `data` Lambda; `mock-policies` is deliberately excluded because those are served as static downloads instead. Only `.md` and `.json` are browsable.

- The file listing is fetched once (metadata only); document bodies load lazily per selection and are cached for the session.
- Markdown renders at full document type scale, with front matter stripped and shown as a "Last updated" line.
- **Form-schema JSON gets a real rendered view** — section cards listing each field's label, type, required flag, dotted path, hint, and options, with a Rendered/Raw toggle for the source. Reading a 20-field schema as raw JSON is unpleasant; this makes the same file legible. JSON that isn't a form schema falls back to source only.
- Search filters across filename, sub-path, and folder label.
- `competitive` and `competitors` documents carry an **Internal Use Only** badge so a battlecard doesn't get screen-shared to a customer.

Content reaches the browser through API Gateway and the `data` Lambda, authorized with the same Cognito JWT as every other API route. Nothing is bundled into the SPA and the browser never touches S3 directly, so the knowledge-base buckets stay private.

## Voice-driven application forms

In the voice assistant, when the advisor and customer agree to start an application, an application form takes over the left panel and fills itself from the conversation. The advisor keeps talking; fields land as they speak.

**Four life products** have published forms, each backed by a JSON schema in S3 (`s3-data/forms/`):

| `product_type` | Product | Shape |
|---|---|---|
| `term_life` | Rainbow Life | Fixed term, no cash value |
| `whole_life` | EverAfter Whole Life | Permanent, guaranteed cash value |
| `universal_life` | HorizonFlex Universal Life | Permanent, flexible premium |
| `variable_life` | StardustVariable Life | Permanent, sub-account investing |

**Answers carry over when the product changes.** All four schemas share an identical `applicant`, `health` and `beneficiary` section plus a common `coverage.sum_assured` and `coverage.premium_frequency` — 18 of ~20 paths. So if the conversation moves from term to whole life, only the 2-4 product-specific fields are outstanding; the advisor is never asked for a date of birth twice. Field state is keyed on path and deliberately not cleared on switch, and the tool tells the agent exactly what carried over so it doesn't re-ask.

**Three tools, split by concern:**

| Tool | Where | Why |
|---|---|---|
| `get_form_schema` | Gateway target (shared) | Read-only data lookup. The **text** agent uses it to answer "what does this application require?" |
| `open_application_form` | Local to the voice runtime | Reads the schema straight from S3 and pushes it down the open WebSocket. Needs the socket in scope, so it can't be a gateway tool |
| `fill_form_field` | Local to the voice runtime | Emits one field to the browser |

The schema is **never routed through the model**. `open_application_form` fetches from S3 server-side and sends it to the browser directly; the model only gets back a compact list of field paths. Passing 20 fields of JSON back out as a tool argument would burn tokens twice and risk silent truncation or invented fields.

**Confidence drives the UI, not the data.** The model self-reports 0.0-1.0 per field. ≥0.85 fills green; below that fills amber with a "Looks right" confirm. Nothing is hidden from the advisor. These scores are a heuristic prompt signal, **not calibrated probabilities** — they're there to draw the eye, not to be quoted as accuracy. Values are clamped server-side and default to amber if malformed.

**Guards.** `fill_form_field` rejects any path not in the schema actually opened, and refuses outright if no form is open — field paths originate from a model and the UI keys its state on them. `product_type` is allowlisted before it becomes an S3 key.

Precedence is **manual > voice > profile**: fields known from the customer's record are prefilled and marked as such, the conversation can override them (a job change, say), and anything the advisor types by hand is never overwritten.

## Bedrock Guardrail

A single shared guardrail (`insurance-advisor-guardrail-shared`) lives in `insadv-02-tools` and gates every Bedrock call in the system: text agent, voice agent, comparator Lambda, recommender Lambda, extract_policy Lambda. The same policy, applied five ways. The published version is written to SSM (`/insadv/bedrock/guardrail-version`) on every policy change so all five callers pick up updates on cold start without a runtime redeploy.

| Policy | Configuration |
|---|---|
| Content filters | `SEXUAL`, `VIOLENCE`, `HATE`, `INSULTS`, `MISCONDUCT` at HIGH input + HIGH output. `PROMPT_ATTACK` at HIGH input only. |
| PII anonymized | `US_SOCIAL_SECURITY_NUMBER`, `CREDIT_DEBIT_CARD_NUMBER`, `US_BANK_ACCOUNT_NUMBER`, `CREDIT_DEBIT_CARD_CVV`, `CREDIT_DEBIT_CARD_EXPIRY`, `PIN`, `INTERNATIONAL_BANK_ACCOUNT_NUMBER`, `SWIFT_CODE`, `US_PASSPORT_NUMBER`, `DRIVER_ID`, `US_INDIVIDUAL_TAX_IDENTIFICATION_NUMBER`. |
| PII blocked (whole request refused) | `PASSWORD`, `AWS_ACCESS_KEY`, `AWS_SECRET_KEY`. |
| PII not anonymized — by design | `NAME`, `EMAIL`, `PHONE`, `ADDRESS`, `AGE`, `DATE_OF_BIRTH`. These are integral to legitimate advisor workflows; output-side log redaction is the right place for those. |
| Word policy | AWS-managed `PROFANITY` list. |
| Denied topics | `LegalAdvice`, `MedicalAdvice`, `InvestmentRecommendations` (asset-allocation / sub-account picks), `UnderwritingDecisions` (no binding "you are approved"), `TaxAdvice`. All `DENY`. |
| Contextual grounding | `GROUNDING` ≥ 0.75, `RELEVANCE` ≥ 0.5. Active on the comparator + recommender Lambdas (which tag their source markdown / customer profile / catalog as `grounding_source`); attached but inert on the text + voice runtimes (Strands tool results aren't tagged as sources). |

Interventions surface as a clean 400 with the configured `blocked_outputs_messaging`.

## Security posture

Security was a first-class design constraint. A number of hardening items are deliberately deferred for production (Cognito MFA, Macie PII detection, a business-event audit log, etc.).

Highlights of what's enforced today:

- **Advisor identity is JWT-only.** The runtime resolves the calling advisor exclusively from the verified Cognito JWT (forwarded via `requestHeaderConfiguration`). Voice WebSocket closes with 1008 if no validated identity is available.
- **Cross-tenant isolation** at the Lambda layer: `advisor-id-index` GSI is a key condition (not a filter) on every read, plus an explicit `advisor_id == calling_user` check on every mutation.
- **Sign-up is closed.** Cognito self-signup is org-blocked; the public `/signup` Lambda enforces a hard-coded allowlist (john.doe + jane.doe). WAF rate-limits the route at 100 req / 5 min / source IP.
- **Cost-bomb defense.** AWS Budget on Bedrock + Lambda spend with 50/80/100% SNS notifications. API Gateway throttling on `/comparator/compare` and `/recommend` (5 rps steady, 10 burst).
- **Document extraction safety net.** `extract_policy_from_document` clamps coverage and premium amounts to sane ranges, rejects non-current dates, refuses "Unicorn" as the third-party insurer, and forces low extraction confidence on injection-keyword matches; the agent's system prompt then switches to field-by-field confirmation before any write.
- **Log retention bounded.** All tools-stack Lambda log groups now have ONE_MONTH retention; the API Gateway access log group is encrypted with a customer-managed KMS key with rotation enabled.
- **AgentCore Memory TTL** capped at 90 days (down from the 365-day platform default) to bound the GDPR right-to-erasure window.

## Quick start

### Prerequisites

- AWS account + credentials in your shell, region `us-east-1`
- Python 3.13, [`uv`](https://docs.astral.sh/uv/)
- Node.js 18+
- A container builder running (Finch is supported; Docker / Colima also work)
- Bedrock model access in `us-east-1` for Claude Haiku 4.5, Claude Sonnet 5, and Nova Sonic 2

### Deploy

```bash
./deploy.sh
```

Runs `uv sync`, deploys backend stacks (`insadv-01-auth` → `04-voice` with `--concurrency 4`), generates the sample-policy PDFs, pulls SSM values into `react-frontend/.env.local` via `setup-env.sh`, runs `npm install && npm run build`, then deploys `insadv-05-frontend`. First deploy ~10 minutes (Cognito pools, WAF, agent + voice container builds, AgentCore runtime + memory provisioning). Subsequent redeploys are incremental.

The frontend must be rebuilt whenever the runtime ARNs change, because `setup-env.sh` bakes them into the bundle at build time. `deploy.sh` already does this in the right order — prefer a full run over a partial `cdk deploy` of a single stack.

The CloudFront URL is emitted as `insadv-05-frontend.SiteUrl`.

### Run locally instead

```bash
cd react-frontend
./run_app.sh
```

Opens at http://localhost:5173. First run auto-populates `.env.local` from SSM via `scripts/setup-env.sh`. The browser only ever holds a Cognito JWT — no AWS IAM credentials.

### Sign in

Use one of the demo advisors to see seeded customers:

- `john.doe@example.com` → Sarah, Emily, Robert, Lisa, Daniel
- `jane.doe@example.com` → Michael, Amanda, Jessica

Password must be 12+ chars with upper, lower, digit, and symbol. Sign-up goes through a backend `/signup` Lambda that calls Cognito `admin_create_user` + `admin_set_user_password`. The Lambda enforces a hard-coded allowlist of the two demo emails.

### Sales pitch deck

In the running app, click the slideshow icon top-right of the nav. The Frictionless Insurance Advisor pitch deck opens at `/presentation/index.html`. Source files live in `react-frontend/public/presentation/`.

## Authentication and identity

Two channels, same Cognito JWT.

**1. Browser → AgentCore Runtime** (text + voice)
- React signs in via Amplify against the `insurance-advisor-user` Cognito pool.
- Text: HTTPS POST to `bedrock-agentcore.<region>.amazonaws.com/runtimes/{ARN}/invocations` with `Authorization: Bearer <jwt>`. Streams SSE. The runtime forwards the Authorization header into the container via `requestHeaderConfiguration` and verifies the JWT in-process.
- Voice: WebSocket to the same host's `/ws` path. Browsers can't set headers on a WebSocket, so the JWT travels base64url-encoded as a `Sec-WebSocket-Protocol` subprotocol — AgentCore's documented browser-OAuth path. The Authorization header is also forwarded for consistency with the text path.

**2. Browser → API Gateway** (profile, policy, catalog, comparator, recommender, signup, documents, data)
- Same JWT, this time validated by an API Gateway `CognitoUserPoolsAuthorizer` against the user pool. Methods require the `insurance-advisor-api/api.access` scope. Signup is the only `authorization_type=NONE` route — and it's allowlist-gated and WAF rate-limited.

**3. Runtime → Gateway** (machine-to-machine)
- Separate Cognito pool `insurance-advisor-gateway` with a service-only `runtime_client`.
- The runtime asks AgentCore Identity Token Vault for a short-lived M2M JWT and presents it to the gateway, whose authorizer accepts only that client.

**4. Gateway → tools**
- For profile + policy tools: OAuth2 Credential Provider `insurance-advisor-api-oauth` against the main user pool with `gateway_client` and the `insurance-advisor-api/api.access` scope.
- For everything else: direct IAM-scoped Lambda invoke.

Plus: shared Bedrock Guardrail on every model call, cdk-nag in CI, CloudWatch + X-Ray observability.

## Prospect vs Active rule

A customer is treated as a **prospect** when **every policy on file is third-party** (or there are none). The badge is computed in the browser; there is no `customer_type` field. As soon as the customer has at least one Unicorn-issued policy, the UI falls back to the DynamoDB `status` field (`Active` / `Inactive`).

## Mock data

Seeded at deploy time by the `MockDataPopulatorLambda` (custom resource):

- 8 profiles in `lambda/mock_data/profiles.json`
- 24 policies in `lambda/mock_data/policies.json`, mixing Unicorn-issued and third-party
- 28-product catalog (Unicorn portfolio + competitor refs) populated alongside, with markdown bodies in S3 and a thin DynamoDB index keyed by `product_id`
- 8 sample third-party policy documents (PDF + markdown) in `s3-data/mock-policies/`, accessible from the frontend's "sample policies" menu so the document-upload flow can be exercised end-to-end without external assets
- Every profile carries enough signal (age, marital status, dependents, occupation, income, home ownership, smoking, broad health) for the agent to coach both customer and prospect conversations

Custom resource is idempotent: a redeploy reconciles to the seeded state, so any prospect created via the agent during testing gets wiped on the next deploy unless added to the seed JSON.

## Project layout

```
insurance-advisor-agentcore/
├── app.py                         # CDK app entrypoint + cdk-nag suppressions
├── deploy.sh / destroy.sh         # Stack deploy / teardown
├── architecture.drawio            # Architecture diagram (drawio source)
├── cdk/
│   ├── auth_stack.py              # Cognito (user pool + gateway pool, app + runtime + gateway clients)
│   ├── tools_stack.py             # DynamoDB, Lambdas, API Gateway, WAF, S3, shared Bedrock Guardrail, KMS, Budget
│   ├── agentcore_stack.py         # Text runtime, gateway, identity, long-term memory
│   ├── voice_stack.py             # Voice runtime (Nova Sonic 2)
│   ├── frontend_stack.py          # S3 + CloudFront for the React build
│   └── agentcore_oauth_provider.py
├── agent/                         # Strands text agent (Claude Haiku 4.5)
├── voice-agent/                   # Strands BidiAgent voice agent (Nova Sonic 2)
├── lambda/
│   ├── profile/ policies/         # Customer data CRUD
│   ├── portfolio/ promotions/
│   ├── company/ competitive/ competitors/
│   ├── catalog/ comparator/ recommend/
│   ├── documents/ extract_policy/ # Document upload + extraction
│   ├── formschema/                # Application-form schema lookup
│   ├── data/                      # Data explorer: lists + serves s3-data docs
│   ├── signup/
│   ├── oauth_provider/
│   └── mock_data/
├── s3-data/
│   ├── company/ competitive/ competitors/ portfolio/ promotion/
│   ├── forms/                     # Application-form schemas, one JSON per life product
│   └── mock-policies/             # Sample third-party PDFs for the upload flow
└── react-frontend/                # React 18 + Vite 8 + Tailwind 4 SPA (9 locales)
    └── public/presentation/       # Sales pitch deck (reveal.js + drawio)
```

## Internationalization

Nine UI locales ship today, all at full key parity — no partial translations:

| | | |
|---|---|---|
| English (`en`) | 日本語 (`ja`) | 한국어 (`ko`) |
| Español (`es`) | Français (`fr`) | 简体中文 (`zh`) |
| Bahasa Melayu (`ms`) | ไทย (`th`) | Bahasa Indonesia (`id`) |

Language switcher in the top nav, persisted to `localStorage` and restored on the next visit. Region variants collapse to the base language (`en-US` → `en`), falling back to English.

Backend datastores and S3 markdown content are English-only by design. The LLM handles multilingual prompts natively, so a Japanese question produces a Japanese reply against an English knowledge base. The comparator and recommender go a step further and pass the active locale explicitly with the request, because their output is generated rather than conversational.

See [`react-frontend/README.md`](./react-frontend/README.md) for the i18n structure and the recipe for adding a new locale.

## Design decisions worth knowing

- **Currency: USD everywhere** in the catalog, competitive content, and competitor references.
- **Prospect = no Unicorn-issued policies on file.** Inferred from `policies.every(p => p.third_party)`.
- **Fictional competitors only:** BigRival (incumbent), StarInsure (budget), QuickSafe (insurtech). Each competitor file opens with a NOTICE blockquote making the illustrative nature clear.
- **Memory is customer-scoped** (`actor_id = customer_id`) so conversations persist across advisor sessions. Three strategies — summary, user preference, semantic — extract different signals from the same conversation history. Retrieval thresholds are deliberately strict (`top_k=3`, `relevance ≥ 0.7`) to prevent cross-context bleed across past sessions for the same customer.
- **No quote / no bind.** The agent analyses and recommends but never quotes a price or binds coverage — enforced in both system prompts AND the `UnderwritingDecisions` denied topic on the guardrail.
- **Same Cognito JWT for two channels.** Browser hits AgentCore Runtime directly *and* API Gateway with the same token. No proxy, no IAM credentials in the browser, no `advisorId` field in the request payload.
- **Comparator + recommender bypass the runtime.** They are one-shot Converse calls with `toolConfig`-forced JSON output, not MCP tools. Lower latency, deterministic output shape, and no memory write back to AgentCore for what is effectively a stateless analytical request.
- **Shared guardrail, SSM-published version.** Same content / PII / topic / grounding policy gates all five model surfaces. Single source of truth — bumping a threshold in `tools_stack.py` propagates to text, voice, comparator, recommender, and extract_policy on the next deploy without redeploying the runtimes.
- **Document content is data, not instructions.** The extract_policy Lambda's tool-forced JSON output makes prompt injection inside document bodies hard to weaponize, and the validators clamp/sanitize fields before the agent reads them.

## Teardown

```bash
./destroy.sh
```

Removes all five stacks and their Cognito pools, DynamoDB tables, S3 buckets, CloudFront distribution, and AgentCore resources. The ECR repository managed by CDK assets remains; delete manually for a complete reset.

## Related docs

- [`react-frontend/README.md`](./react-frontend/README.md) — React app run instructions + i18n recipe

## Limitations

This is a demonstration build, and advisor **sign-up is intentionally constrained** — worth knowing before sharing the app:

- **No open self-registration.** The Cognito user pool is configured with `AllowAdminCreateUserOnly`, so Cognito's self-service sign-up flow is disabled by design. Accounts are only ever created server-side by the `/signup` Lambda via `admin_create_user` + `admin_set_user_password`.
- **Hard-coded allowlist of two demo advisors.** The `/signup` Lambda accepts only `john.doe@example.com` and `jane.doe@example.com`; every other address is rejected. Adding an advisor means editing the allowlist in `lambda/signup/` and redeploying `insadv-02-tools` — there is no runtime UI or API path to onboard new advisors.
- **Edge rate-limiting.** WAF caps `/signup` at 100 requests / 5 min / source IP, so even the allowlisted path is not suited to bulk onboarding.
- **Net effect.** The app effectively supports only the two seeded advisors and their seeded customer sets. A real multi-tenant onboarding flow (self-registration, email verification, MFA, per-advisor provisioning) is out of scope for the demo and tracked under the deferred production-hardening items.


## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.

