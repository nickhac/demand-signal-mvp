# Paywall Copy — v1.1 Paid-Tier Differentiation
**Author:** Founder (standing in for CPO — DEM-127)
**Date:** 2026-06-25
**Approved for:** FE implementation (DEM-125/126 unblocked)

---

## Decision: $199/mo Growth Tier (single paid tier)

Aligns with pricing-strategy.md. The "$5/opportunity" model shown in the current
paywall template is deprecated — switch to the $199/mo subscription model.

---

## Paywall Headline + Subhead

**Headline:** Unlock unlimited searches — and every buyer found

**Subhead:**
> You've seen 5 free opportunities for **{{ domain }}**.
> Growth gives you unlimited runs, 2× the cards, and CSV export to your CRM.

---

## The 3 Paid-Tier Bullets (conversion copy, not feature list)

These go directly above the CTA button. Maximum 3. Order by conversion impact:

```
✓ Unlimited searches — run any domain, any time, no monthly cap
✓ 10 cards per search — 2× the prospects, ranked by signal strength
✓ CSV export — one click to get all results into your CRM
```

**Implementation note:** render as a `<ul class="unlock-list">` with green checkmarks.
Each bullet is one line. No sub-bullets. No extra explanation — the copy is already
conversion-optimised.

---

## Pricing Block

```
$199 /month
or $1,990/year (2 months free)
```

---

## CTA Button

**Primary:** `Start Growth — $199/mo →`
**Secondary (below button, small grey text):**
> Most customers close their first deal within a week and never look back.
> If you close one deal at any ACV, you're already ahead.

---

## Social Proof (already rendered, keep as-is)

- Domain counter: `X domains analyzed` — keep
- Testimonials: keep existing 2 quotes
- Outcome line: keep `Users report saving 3–4 hours of manual LinkedIn prospecting per search`

---

## What to Remove

- `$5 per opportunity` pricing — delete
- `Pay only for what you unlock` — delete
- `No monthly commitment` bullet — delete (replaced by the 3 bullets above)

---

## Full Paywall Section Order (top to bottom)

1. Email capture banner (existing — keep)
2. Headline + subhead (update domain context)
3. Social proof block (existing — keep)
4. Pricing card:
   a. Price: `$199/mo`
   b. Period: `or $1,990/year`
   c. 3-bullet unlock list (new)
   d. CTA button: `Start Growth — $199/mo →`
   e. Trust line below button (new)
5. Back to results link (existing — keep)

---

## Notes for FE Dev

- The `showWaitlistForm()` flow can stay — just update the copy inside it to match
  "Growth tier launching soon — enter email to be notified" instead of
  "pay-per-opportunity payments launching soon"
- The `/api/stats` fetch for domain counter is already wired — no changes needed
- No backend changes required for this task — pure HTML/copy update
