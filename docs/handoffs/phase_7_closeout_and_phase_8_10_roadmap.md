# God Incorporated — Phase 7 Closeout and Forward Roadmap

## Current demarc state

This chat ended in a strong demarc state.

Confirmed at closeout:
- `main.py` compiles cleanly.
- Phase 7 entitlement closeout was committed and pushed.
- Tag created and pushed: `v7.0.1-pre-stripe-closeout`.
- Branch in use at closeout: `phase6_semantic_oracle`.

Terminal-confirmed closeout items:
- `python -m py_compile main.py` passed.
- Usage-window helpers are present in `main.py`.
- Admin closeout functions are present in `main.py`.
- Commit created: `1788729` with message: `Phase 7: finalize entitlement closeout before Stripe`.
- Tag created and pushed: `v7.0.1-pre-stripe-closeout`.

---

## What Phase 7 accomplished

Phase 7 was the billing-authority and entitlement-foundation phase.

### 1. Billing and entitlement authority model
Phase 7 established that entitlement is not determined by raw plan alone.

The effective access model now accounts for:
- current active support state
- grace state
- expired/cancelled support state
- donor floor
- scroll floor
- fallback floor
- renewal offer plan
- support mode

This means the app now distinguishes between:
- stored paid/support history
- current effective access
- fallback access after support ends

### 2. Fallback floor model
Phase 7 implemented the demotion / continuity model discussed in planning.

Key rules now encoded:
- active paid support grants current plan access
- when support ends, access falls to a computed fallback floor rather than simply collapsing to anon
- scroll contribution can raise fallback access
- donor history can preserve a higher fallback than pure free baseline
- renewal targeting can point back to the user’s previous paid/support level

### 3. Monthly recurring lifecycle
Phase 7 added and validated the monthly recurring lifecycle path.

This includes:
- renewal success helper
- renewal failure to grace helper
- grace expiry downgrade helper
- cancel-at-period-end downgrade helper
- donor-history updates during successful paid renewals
- fallback-state refresh after lifecycle transitions

### 4. Annual prepaid lifecycle
Phase 7 added and validated a second paid-support model:
- annual prepaid activation
- annual prepaid expiry
- fallback after annual expiry
- preservation of renewal offer target

This was important because the product model includes both:
- monthly recurring support
- annual prepaid support terms

### 5. `/me` completion
Phase 7 completed the seeker-facing `/me` payload so it can support billing-era UX.

`/me` now cleanly communicates:
- current effective access plan
- stored/raw plan
- support status
- support mode
- donor floor
- scroll floor
- fallback floor
- renewal offer plan
- support message
- renewal message
- usage window start

This makes seeker-facing state understandable before Stripe is added.

### 6. Admin operator parity
Phase 7 brought admin tooling closer to authority parity.

Completed in this area:
- annual prepaid actions added to admin controls
- support/fallback details exposed more fully in admin detail views
- operator workflow improved for exercising lifecycle logic

### 7. Usage-window authority closeout
A critical late closeout item was completed:
- usage windows are now explicitly derived from entitlement state rather than always inheriting raw current-period timestamps.

That was necessary before Stripe because usage limits must remain coherent across:
- active monthly users
- annual prepaid users
- expired users on fallback floors
- authenticated low/free tiers

### 8. Admin override closeout
Another critical late closeout item was completed:
- admin entitlement override now refreshes fallback state after changes.

That prevents operator actions from leaving derived floor/support state stale.

---

## Phase 7 architectural result

At the end of this chat, the project reached this state:

### Entitlement foundation is now structurally ready
The app can now support provider-driven billing without provider-driven business logic.

This means Stripe can be integrated on top of a stable internal model instead of defining the model itself.

That is the central achievement of Phase 7.

---

## Recommended immediate housekeeping before next coding session

These items were visible in the terminal at demarc and should not be forgotten:

### Working tree leftovers
At demarc, local leftovers still existed outside the committed core changes:
- `cookies.txt` modified
- multiple `*.bak` files untracked

These are not blockers for the roadmap, but they should be cleaned intentionally.

### Recommended housekeeping actions
Before or at the beginning of the next coding session:
1. decide whether `cookies.txt` should be ignored, removed, or restored
2. remove or archive backup files if no longer needed
3. consider adding local-only artifacts to `.gitignore` if appropriate

This is repo hygiene, not product architecture, but it will reduce noise in the next phase.

---

# Forward roadmap

## Phase 8 — Stripe integration

### Objective
Connect real billing-provider events to the Phase 7 entitlement foundation.

### Core principle
Stripe should **feed** the internal authority model, not replace it.

### Scope
1. Stripe product and price mapping
   - map `plan_catalog` plans to Stripe products/prices
   - support both monthly recurring and annual prepaid

2. Checkout/session creation
   - create Stripe checkout flow for monthly recurring support
   - create Stripe checkout flow for annual prepaid support
   - attach user identity cleanly to checkout metadata

3. Stripe customer authority
   - create or resolve Stripe customer per authenticated user
   - persist mapping in `billing_customers`

4. Webhook ingestion
   - add webhook endpoint with signature verification
   - write all incoming events to `payment_events`
   - make event handling idempotent

5. Subscription and transaction persistence
   - persist Stripe state into:
     - `billing_customers`
     - `subscriptions`
     - `billing_transactions`
   - do not let Stripe become the only readable source of truth

6. Event-to-lifecycle mapping
   - monthly success -> `apply_subscription_renewal_success()`
   - renewal failure -> `apply_subscription_renewal_failure_to_grace()`
   - grace expiry -> `apply_grace_expiry_downgrade()`
   - annual prepaid purchase -> `apply_annual_prepaid_activation()`
   - annual expiry -> `apply_annual_prepaid_expiry()`
   - cancel-at-period-end -> `apply_cancel_at_period_end_downgrade()` when appropriate

7. Billing UX integration
   - add support-selection and checkout launch paths in seeker UI
   - keep language clear: support, renewal, expiry, access continuity

### Phase 8 success criteria
- user can successfully purchase monthly recurring support
- user can successfully purchase annual prepaid support
- Stripe webhooks are verified and idempotent
- provider events are persisted before mutation
- internal helpers, not raw webhook branches, determine lifecycle state
- `/me` reflects Stripe-driven changes correctly
- admin detail reflects Stripe-driven changes correctly

### Risks to manage in Phase 8
- webhook idempotency
- user identity binding across checkout/webhook flow
- avoiding duplicated lifecycle mutations
- ensuring support history and floor state stay consistent

---

## Phase 9 — LLaMA / router integration

### Objective
Resume the model-routing and local-intelligence architecture after billing authority is stable.

### Why this comes after Stripe
Billing must stabilize the product spine first.
Once billing authority is real, LLaMA/router work can safely build on known user state and product tiers.

### Scope
1. Revisit routing architecture
   - LLaMA as router / learner / retrieval intelligence layer
   - preserve Oracle authority model for Hathor and Moses

2. Revisit personal-memory and corpus influence design
   - use seeker state and entitlement tiers intentionally
   - avoid tying model behavior directly to billing in simplistic ways

3. Resume domain-specific intelligence work
   - personal scroll weighting
   - canonical/community/personal retrieval interplay
   - potential user-specific or tier-aware context shaping

4. Define where LLaMA lives operationally
   - local server path
   - cost-control path
   - relationship to OpenAI / xAI inference paths

### Phase 9 success criteria
- routing architecture is explicit and testable
- LLaMA role is clearly bounded
- no confusion between billing entitlement and oracle authority
- personalized seeker experience deepens without destabilizing the product

---

## Phase 10 — Production hardening and deployment path

### Objective
Take the stabilized product toward deployable production readiness.

This phase likely includes more than just Render.

### Likely Phase 10 scope
1. Deployment decision and environment strategy
   - Render path, or alternative host if architecture evolves
   - env var hardening
   - production secrets handling
   - static asset and database configuration cleanup

2. Production migration discipline
   - migration ordering
   - rollback approach
   - backup strategy
   - restore confidence for DB changes

3. Billing production readiness
   - Stripe test mode -> live mode transition plan
   - webhook reliability and replay handling
   - support reconciliation/admin tooling

4. Auth and session production hardening
   - session secret discipline
   - cookie security settings
   - email verification and password-reset production review

5. Operator safety and observability
   - logging review
   - admin audit visibility
   - error surfacing
   - health checks and deployment diagnostics

6. Product readiness review
   - seeker-facing support language
   - expiry/renewal UX polish
   - edge-case flows
   - limited production pilot readiness

7. Launch staging path
   - local/staging/prod environment separation
   - seeded admin/support accounts
   - smoke-test checklist

### Possible Render-to-prod framing
If Render remains the target, Phase 10 should likely be framed as:
- staging deployment first
- verified billing sandbox in deployed environment
- controlled production cutover after staged validation

### Phase 10 success criteria
- deployed environment is stable
- auth works in deployment
- billing works in deployed environment
- admin operator flows are production-usable
- rollback and observability are acceptable
- pilot users can be supported safely

---

## Possible Phase 11 and beyond

These are not necessarily immediate, but they are likely future phases once production exists.

### Phase 11 — Financial / banking expansion
Potential future work:
- deeper billing reporting
- donation/support ledger refinement
- provider abstraction beyond Stripe if ever needed
- bank-linking or account-layer work only if product direction truly demands it

### Phase 12 — Mobile / platform expansion
Potential future work:
- native mobile support
- refined voice experience
- platform-specific UX optimization

### Phase 13 — Scale and intelligence expansion
Potential future work:
- deeper personalization
- larger-scale retrieval / caching / corpus ops
- advanced LLaMA orchestration
- sustainability and cost controls at scale

---

# Suggested next-chat opening

Use the next chat to focus only on Stripe integration.

Recommended opening objective:

> Phase 7 closeout is complete and tagged at `v7.0.1-pre-stripe-closeout`. We now want to begin Phase 8: Stripe integration on top of the finished entitlement foundation. Please work architecture-first, then schema/use-case mapping, then implementation steps. The internal lifecycle helpers already exist for monthly recurring, annual prepaid, grace, expiry, cancel-at-period-end, fallback floors, and `/me` support status. Start by defining the exact Stripe product/price mapping, checkout flow, customer mapping, webhook ingestion, idempotency strategy, and event-to-helper mapping.

---

# Bottom line

This chat completed the **entitlement and billing-foundation closeout**.

That means:
- Phase 8 should be **Stripe integration**
- Phase 9 should be **LLaMA / router integration**
- Phase 10 should be **production hardening and deployment path**, likely including Render staging-to-prod or equivalent deployment discipline

This is now a clean demarc point.

