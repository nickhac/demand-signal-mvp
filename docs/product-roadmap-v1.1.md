# Product Roadmap v1.1 — Demand Signal Lead Generator
**Author:** Chief Product Officer agent (DEM-35)
**Date:** 2026-06-21
**Status:** Approved for v1.1 scope

---

## Current MVP State

- Live: https://demand-signal-mvp.onrender.com
- Flow: domain → ICP extraction → ICP confirm/edit → Apify signal search → 5 ranked opportunity cards → paywall → email capture
- Output quality: HIGH (validated by DEM-21 quality report — specific, evidence-based, ready-to-use)
- Paying customers: 0
- Beta users through full flow: 0
- Distribution: ALL channels blocked on operator action (LinkedIn, Reddit, HN require human accounts)

---

## Gap Analysis: Top 3 Conversion Blockers

### Gap 1 — No trust signal at the paywall (CRITICAL)
The paywall appears after 5 cards with no social proof. A first-time visitor who got decent results has no reason to believe $199/mo is worth it. There is no testimonial, no counter ("X companies searched"), no "what you get next month" framing. The value proposition at the paywall is purely transactional — it asks for payment without building belief.

**Conversion impact:** HIGH. This is the primary reason a warm free user doesn't convert. Even if they loved the cards, the paywall asks for commitment before establishing credibility.

### Gap 2 — No account or history (HIGH)
Every session is anonymous and ephemeral. A user who got good results yesterday cannot:
- Come back and see their previous run
- Run a second domain without re-entering ICP
- Share results with a colleague or manager who approves budget

Without account persistence, there is no "reason to return" and no "reason to pay" — paid tiers require memory of value delivered. A user who cannot recall their session cannot advocate internally for budget approval.

**Conversion impact:** HIGH. B2B SaaS purchases often require a second visit and/or internal sharing. Anonymous sessions kill both.

### Gap 3 — No clear paid-tier differentiation (MEDIUM)
The paywall shows a $199/mo gate but doesn't clearly explain what paid unlocks beyond "more searches." The free tier already delivers 5 high-quality cards — the best output the product can produce. Paying doesn't obviously unlock something qualitatively better; it just unlocks more of the same. There is no:
- Saved history / account
- CRM export (CSV download)
- Higher card count per search
- Alerting / monitoring mode

Without a clear paid hook the conversion argument is volume-only, which is weak for a tool this new.

**Conversion impact:** MEDIUM. Compounds Gap 1 and 2 — the paywall is both uncredible AND unclear about what it delivers.

---

## v1.1 Scope (3–5 items, minimum to convert a free user to $199/mo)

### Item 1 — Social proof block at paywall (S effort)
Add a static proof block above the paywall CTA:
- Counter: "N domains analyzed" (increment a persistent counter on each completed search)
- 2–3 short quote testimonials (seed with real beta quotes once available; placeholder acceptable for launch)
- One concrete outcome statement: "Users report saving 3–4 hours of manual LinkedIn prospecting per search"

This is copy + a single backend counter endpoint. No auth required.

**Effort:** S (1–2 days)
**Conversion lever:** Trust at the decision moment

---

### Item 2 — Email-gated session save + history (M effort)
Replace anonymous sessions with optional email-gated sessions:
- After results page, before paywall: "Save these results / get them by email" → collect email → link session to email
- On return visit: "Enter your email to see past searches" → retrieve last N sessions
- No password, no OAuth. Email = identity for v1.1.

This directly solves Gap 2 (no return path) and turns the email capture from a dead-end waitlist into a functional product feature. It also gives the operator a real usage signal: emails with multiple sessions = high-intent users to reach out to.

**Effort:** M (3–5 days — needs backend email→session link table, lookup endpoint, email send via SendGrid/Resend)
**Conversion lever:** Return visits + internal sharing + paid-tier upgrade path

---

### Item 3 — CSV export behind paywall (S effort)
Add a "Download results as CSV" button that is locked behind the paid tier. CSV includes: name, company, role, pain signal, source URL, why-now, outreach draft.

This is the single clearest paid-tier differentiator. Sales reps and founders immediately understand "I need to get this into my CRM." It gives the paywall a concrete unlock beyond "more searches."

**Effort:** S (1 day — client-side CSV generation from results JSON)
**Conversion lever:** Clear paid-tier value; CRM workflow integration

---

### Item 4 — Persistent run counter + usage analytics (S effort)
Add a lightweight backend counter:
- Increment on each completed opportunity search
- Display "X domains analyzed" on homepage and paywall
- Log domain + timestamp to a persistent store (not /tmp — use a small SQLite or append-only log in a mounted volume)

This feeds Item 1 (social proof), catches aggregate usage data the operator currently has no visibility into, and survives Render restarts (current /tmp sessions are lost on every deploy).

**Effort:** S (1 day — SQLite or append log on persistent volume)
**Conversion lever:** Enables social proof; gives operator usage visibility

---

### Item 5 — ICP re-use on second search (S effort)
When a user (identified by email from Item 2) runs a second domain, pre-fill the ICP confirm step with their previous ICP hypothesis. This reduces friction for the most obvious repeat use case: an agency or founder running multiple client domains.

**Effort:** S (conditional on Item 2 being done — 0.5 days additional)
**Conversion lever:** Repeat usage stickiness; makes paid volume argument real

---

## Not-Yet List (explicitly deferred)

- **Stripe / payment integration** — do not build until 3 users express unprompted intent to pay. Take manual payments (bank transfer, PayPal) for first 2–3 customers to validate price point before engineering investment.
- **Slack / CRM integrations** — post-validation; adds complexity without proving the core loop works.
- **LinkedIn OAuth or LinkedIn API** — too much risk (account bans, API access cost). Keep Apify scraping for MVP stage.
- **Team accounts / multi-seat** — first prove single-user willingness to pay.
- **Custom ICP training** — users can already edit the ICP on every run. Fine-tuning is premature.
- **Real-time alerting / monitoring** — valuable but irrelevant until paying customers exist.
- **Mobile-optimised UI** — B2B sales workflows happen on desktop; mobile is a distraction.

---

## Prioritised v1.1 Feature List

| # | Feature | Effort | Conversion Lever | Priority |
|---|---------|--------|-----------------|----------|
| 1 | Social proof block at paywall | S | Trust | P1 |
| 2 | Persistent run counter + analytics | S | Social proof + visibility | P1 |
| 3 | CSV export (paywall-locked) | S | Clear paid-tier value | P2 |
| 4 | Email-gated session save + history | M | Return visits + sharing | P2 |
| 5 | ICP re-use on second search | S (dependent on 4) | Repeat stickiness | P3 |

**Recommended sequence:** Ship items 1+2 together as a 2-day sprint (pure frontend/counter work, zero auth). Then ship item 3 (1 day). Then item 4 (3–5 days — the one M effort item). Item 5 comes free with 4.

**Total v1.1 effort:** 7–10 days engineering.

---

## Success Criteria for v1.1

- 10 free users complete the full flow (session + results)
- 3+ express intent to pay (ask the paywall question: "If this was $199/mo, would you consider it?")
- 1 pays voluntarily before being asked

These are the existing CompanyBuilder tripwires. v1.1 is considered validated when all three are cleared.

---

## Distribution Note

v1.1 is meaningless without distribution. All current channels are operator-blocked. The operator should treat the following as a parallel track to v1.1 engineering:

1. **HN Show HN post** — package is ready at docs/hn-show-submission.md. Post Mon–Wed 9am ET.
2. **LinkedIn personal post** — 5-minute task. Announce the MVP with a short personal note.
3. **10 warm LinkedIn DMs** — playbook at docs/beta-outreach-tracker.md Section 1. ~45 min total.

Engineering v1.1 without distribution gets 0 conversion data. Distribution without v1.1 gets anecdotal feedback. Both should run in parallel.
