# Unicorn Insurance Digital Platform

This document describes the customer-facing and advisor-facing digital tools we provide, and the security and privacy standards they operate to. It is aimed at advisors who need to explain the platform to prospects or help existing clients use it.

## Overview

Our digital platform has three main surfaces that share one back-end:

- The **Unicorn Insurance mobile app**, for customers.
- The **customer portal** on the web, also for customers.
- The **advisor workbench**, for licensed advisors who distribute our products.

All three use the same policy, claims, billing, and document systems, so a change made in one place is visible in the others in near real time. The back-end is hosted in cloud data centers in the EEA, the UK, Singapore, and North America, with customer data residency aligned to the customer's market.

## Mobile App

The mobile app is available for iOS and Android and is the primary channel for day-to-day customer interactions.

### Core features

- **Policy overview** — all of the customer's policies in one list, with cover summary, premium, renewal date, and documents.
- **Digital documents** — policy schedules, certificates of insurance, and endorsements available as PDFs; a motor insurance certificate that works offline for roadside checks where the local regulator accepts digital certificates.
- **Premium and billing** — view upcoming payments, change payment method, download receipts, and update bank or card details.
- **Mid-term changes** — common self-service changes such as address, named driver, or beneficiary updates, subject to underwriting rules. More complex changes are routed to an agent.
- **Start a claim** — a guided flow that captures the loss details, lets the customer upload photos and documents, and hands off to the claims team with a reference number.
- **Claim tracking** — live status, next-step prompts, and a full message history for each open claim.
- **Secure messaging** — authenticated chat with our service team and, where relevant, the assigned claims handler.
- **Emergency assistance** — one-tap calling to the claims hotline and the travel assistance line.
- **Renewals and quotes** — renewal review, coverage adjustments, and indicative new-business quotes for other product lines.

### Convenience features

- **Biometric sign-in** using Face ID, Touch ID, or Android biometric equivalents.
- **Offline access** to previously downloaded documents.
- **Push notifications** for premium reminders, claim updates, and renewal deadlines. Notifications are configurable.
- **Language switcher** covering the supported service languages (see customer service documentation).
- **Accessibility** — supports system font scaling, high-contrast mode, screen readers (VoiceOver and TalkBack), and keyboard navigation on external keyboards. Targeted at WCAG 2.1 AA.

## Customer Portal

The customer portal provides the same core features as the mobile app, with a layout tuned to larger screens.

- Full document library with search and download.
- Longer-form quote and application journeys that benefit from a larger screen (life underwriting questionnaires, home valuation wizards).
- Payment history export to CSV and PDF.
- Household view for customers who manage policies for partners, children, or parents, where the relevant consents are in place.

Sign-in uses the same credentials as the mobile app. Customers can link multiple devices to one account and see active sessions in the security settings.

## Advisor Tools

Advisors have a separate workbench that mirrors the customer view and adds professional tooling. Access is granted only to advisors with a verified licence and an active distribution agreement.

### Advisor workbench

- **Client list** — all clients the advisor has distribution rights for, with policy status, renewal calendar, and pending tasks.
- **Quote and bind** — full new-business quoting for all active product lines, with side-by-side option comparisons and compliant needs-and-means capture.
- **Mid-term changes** — submit endorsements on behalf of the client with the client's consent.
- **Claims support** — open a claim on behalf of a client, track its status, and escalate through the advisor desk.
- **Documents and disclosures** — generate and send product disclosures, key information documents, and illustrations in the client's language.
- **Commissions** — statements, breakdowns, and year-to-date summaries, aligned to the advisor's contract.
- **Training and compliance** — mandatory product training, CPD tracking where required by the local regulator, and records of completion.

### Integrations

- **Open API** — a REST API for tied agent and broker systems that want to integrate quoting and policy servicing directly. API access requires a signed integration agreement and uses OAuth 2.0 with mTLS for server-to-server calls.
- **Single sign-on** — SAML 2.0 and OIDC-based SSO for broker houses that maintain their own identity systems.
- **Document exchange** — SFTP and secure file exchange for bulk documents where API integration is not yet in place.

### Advisor mobile view

The workbench is responsive and usable on tablet and phone, which covers most on-the-road advisor needs. A dedicated advisor mobile app is on the roadmap but is not yet generally available.

## Identity and Authentication

- **Customer accounts** use email or phone plus password, with mandatory multi-factor authentication (MFA) at first sign-in and on any new device. MFA options include authenticator apps, SMS one-time codes, and push notifications to a trusted device.
- **Advisor accounts** require MFA on every session and are tied to the advisor's verified identity and licence.
- **Session management** — active sessions are listed in the security settings; customers and advisors can sign out remote sessions at any time.
- **Passwordless sign-in** is available through platform passkeys (WebAuthn) in supported browsers and operating systems.

## Data Protection and Privacy

- All personal data is processed in line with GDPR, UK GDPR, and equivalent local laws (for example, PDPA in Singapore, Privacy Act in Australia, PIPEDA in Canada, HIPAA-aligned handling for U.S. health data).
- Data residency is aligned to the customer's market. EEA customer data stays within the EEA. UK customer data stays in the UK. APAC and North American data stay within their respective regions.
- Customers can exercise data rights (access, correction, erasure where applicable, portability) through the portal's privacy settings or by contacting `privacy@unicorninsurance.example`. See the customer service documentation for the full list of privacy contacts.

## Security Certifications

Our digital platform operates to a set of independently audited standards. Certifications are maintained through regular surveillance audits; certificates and current scopes are available on request.

### Information security management

- **ISO/IEC 27001** — certified information security management system covering the policy, claims, billing, customer portal, mobile app, and advisor workbench.
- **ISO/IEC 27017** — cloud-specific security controls applied to our cloud-hosted services.
- **ISO/IEC 27018** — privacy controls for personally identifiable information processed in cloud services.

### Service controls

- **SOC 2 Type II** — annual report covering the Security, Availability, and Confidentiality trust service criteria for the customer platform and the advisor workbench. SOC 2 reports are shared with enterprise partners under NDA.
- **SOC 1 Type II** — annual report covering financial reporting controls for the policy administration and billing systems.

### Payments

- **PCI DSS** — compliant for the cardholder-data environment used by the portal and app. We use tokenization so that raw card numbers do not touch our application servers.

### Privacy

- **ISO/IEC 27701** — privacy information management system certification, extending the 27001 scope to personal data.
- Registered with data protection authorities in each market.

### Business continuity

- **ISO 22301** — business continuity management certification covering the digital platform.
- Target availability for customer sign-in, policy view, and claim submission is 99.9% monthly. Planned maintenance is scheduled in low-traffic windows and announced in advance.

## Security Practices

In addition to the certifications above, the platform applies these practices:

- **Encryption** — TLS 1.2+ in transit for all client connections; AES-256 at rest for customer data.
- **Least-privilege access** — role-based access control for internal staff, with just-in-time elevation for sensitive actions. All access is logged.
- **Secure development** — secure-by-default coding standards, mandatory code review, automated security testing (SAST, DAST, dependency scanning), and annual external penetration tests.
- **Vulnerability disclosure** — a responsible-disclosure program at `security@unicorninsurance.example`. We acknowledge reports within two business days.
- **Incident response** — a 24/7 security operations function with defined runbooks and regulatory notification paths.

## Roadmap Highlights

Items currently on the public roadmap (subject to change):

- A dedicated advisor mobile app.
- Passkey sign-in as the default for new customer accounts.
- Expanded self-service for mid-term changes on home and health policies.
- Real-time voice-to-claim intake for motor first notice of loss.

If an advisor has a specific capability request, feedback can be sent through the advisor desk. We review feedback quarterly as part of the product planning cycle.
