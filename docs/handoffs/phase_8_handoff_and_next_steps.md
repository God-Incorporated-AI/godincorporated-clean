# Phase 8 Handoff: Stripe Recurring Activation Working

## Current checkpoint
- **Branch:** `phase8_stripe_integration`
- **Latest commit:** `825f01a` — `Phase 8: lock recurring Stripe activation and entitlement updates`
- **Latest tag:** `v8.1.3-recurring-activation-working`

## What is now working
- Stripe checkout session creation works from the Temple UI.
- Support modal is wired into the hamburger/menu flow.
- Stripe webhook signature handling is working.
- `checkout.session.completed` can now:
  - create/update a local `subscriptions` row
  - activate the user entitlement in `users`
- A paid user now upgrades in-app correctly.
- JD test case confirmed:
  - `users.plan_code = seeker`
  - `users.entitlement_status = active`
  - monthly renewal dates are populated
  - hamburger menu reflects the paid-tier question count
- Recurring activation state is committed and pushed.

## What was fixed in this phase
- Stripe billing customer schema alignment
- webhook persistence schema alignment
- support modal UI wiring
- recurring activation path from Stripe webhook to local user entitlement
- subscription upsert path using Stripe subscription IDs
- Stripe object normalization issues in webhook processing
- missing DB constraint for `subscriptions.stripe_subscription_id`
- checkout completion now activates user entitlement immediately for recurring support

## Known remaining gap
- **`billing_transactions` is still empty**
- `invoice.paid` is being received and marked `processed`, but invoice-to-ledger persistence is not yet fully landing.
- This is a **secondary ledger/history issue**, not a blocker for paid user activation.

## Recommended next task (Phase 8.2)
Finish Stripe billing ledger persistence:
1. Trace `invoice.paid` path in `process_stripe_event()`
2. Confirm `resolve_user_and_subscription_context()` returns all values needed on invoice events
3. Ensure `upsert_billing_transaction_from_invoice()` is actually writing rows
4. Populate meaningful `helper_name` on successful invoice processing
5. Verify `billing_transactions` fills for both:
   - initial subscription invoice
   - future renewals

## Suggested test sequence next chat
1. Log in as JD test user
2. Purchase a monthly recurring plan
3. Verify in SQL:
   - `payment_events`
   - `subscriptions`
   - `billing_transactions`
   - `users`
4. Hit `/me`
5. Confirm menu question limit and support messaging match the DB state

## Important current reality
- The core recurring Stripe path is **working**.
- The remaining Stripe work is **ledger/history completion**, not fundamental activation.
- Do **not** destabilize the working entitlement flow while fixing ledger persistence.

## Files most relevant next chat
- `main.py`
- `services/stripe_billing.py`
- `db/migrations/2026_04_02_phase8_annual_recurring.sql`
- `db/migrations/2026_04_03_phase8_billing_customers_alignment.sql`
- `db/migrations/2026_04_06_phase8_webhook_schema_alignment.sql`

## Prompt for the next chat
We are continuing on branch `phase8_stripe_integration` from tag `v8.1.3-recurring-activation-working`.

Current status:
- Stripe recurring activation is working end to end.
- `checkout.session.completed` successfully activates user entitlement.
- Paid users now upgrade correctly in `/me` and in the UI.
- The remaining issue is that `billing_transactions` is still empty even though `invoice.paid` is being received and marked processed.

Your task:
1. Review the current `main.py` and `services/stripe_billing.py`
2. Preserve the working entitlement flow
3. Finish the invoice-to-ledger path so `billing_transactions` is populated correctly
4. Make `invoice.paid` produce a meaningful `helper_name`
5. Give terminal-safe, minimal-risk patches and verification SQL
6. After the ledger path works, help prepare the next step for account/tier/about pages and broader Stripe account UX

Constraints:
- Do not regress the current recurring activation path.
- Prefer surgical changes over rewrites.
- Keep the database as the source of truth for verification.
- Maintain branch hygiene and clean demarc after success.

## After Stripe ledger completion
Next likely workstreams:
- account page / billing visibility
- tier explanation page
- about page / story page
- navigation cleanup through hamburger menu
- Stripe look-and-feel / branding alignment
- later: Render broadcast prep and LLaMA phase handoff

