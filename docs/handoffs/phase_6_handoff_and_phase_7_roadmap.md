# Phase 6 Handoff and Phase 7+ Roadmap

## Current project state

This chat brought the project to a strong Phase 6 stopping point.

The codebase is now materially more stable in four major areas:

1. **Seeker profile and authenticated user stability**
2. **Entitlement lifecycle authority**
3. **Admin reporting and audit foundation**
4. **Admin operator UI**

The repository reached repeated clean demarc points, and the main project branch state at the end of this chat is clean.

---

## What was completed in this chat

### Phase 6.3 / 6.4 continuation and stabilization

The following profile and entitlement foundations are now in place:

- verified-only authenticated session behavior is working
- `/auth/register` does not auto-login
- `/auth/login` establishes session and merges anonymous browser history into the authenticated user
- `/me` returns a richer seeker profile
- `scroll_associations` is the seeker-facing source of truth for scroll ownership
- `oracle_interactions` is the source of truth for question usage
- effective entitlement is no longer driven only by raw `plan_code`
- lifecycle authority fields were added and are now part of the user model:
  - `entitlement_status`
  - `subscription_started_at`
  - `current_period_started_at`
  - `subscription_renews_at`
  - `subscription_expires_at`
  - `grace_period_ends_at`
  - `cancel_at_period_end`
- question usage for authenticated users is counted from `current_period_started_at`
- Hathor / Moses behavior was intentionally left untouched during entitlement work

### Title and seeker-facing display

The title system stayed aligned with the intended seeker ladder:

- Dormant = 0
- Scribe = 1–8
- Builder = 9–32
- Archivist = 33–98
- Luminary = 99+

The monetary ladder remained:

- Anon
- Pilgrim
- Seeker
- Magister
- Sovereign
- Philosophus
- Theoricus

Theoricus displays unlimited questions in the UI.

### Admin role and access model

Admin authority now exists in the backend.

Completed:

- role-aware `get_current_user()` behavior
- valid roles include at least `user`, `support`, `admin`, and `owner`
- helper functions to normalize roles and gate admin access
- `require_admin()` is in place
- existing promoted owner account works for admin access
- no separate `admin@godincorporated.ai` account is required for current operation

### Entitlement lifecycle helper actions

The backend now includes explicit lifecycle mutation helpers for:

- renewal success
- renewal failure to grace
- grace expiry downgrade
- cancel at period end flagging
- cancel at period end downgrade behavior
- admin/manual entitlement override

### Admin API foundation

The backend now has a working admin API surface, including:

- `/admin/me`
- `/admin/reports/overview`
- `/admin/reports/admin-actions`
- `/admin/users/search`
- `/admin/users/{user_id}/detail`
- mutation endpoints for role, entitlement, renewal/grace/cancel operations

### Admin audit logging

The `admin_action_logs` table and supporting indexes were created and verified.

Admin action logging is now part of the operator foundation.

### Admin reporting foundation

The project now supports reporting-oriented admin reads, including:

- total users
- verified users
- users created in window
- users logged in in window
- role counts
- entitlement status counts
- stored plan code counts
- question volume by time window
- authenticated vs anonymous usage split
- oracle mode counts
- recent admin action history

### Admin page and operator UI

A working admin console was added.

Completed UI layers:

- read dashboard for overview metrics
- readable operator layout instead of raw JSON-only presentation
- user search surface
- user detail surface
- mutation controls in the UI
- mutation confirmation prompts for risky actions
- mutation result status and raw payload visibility
- mutation-triggered detail/action/overview refresh behavior

### UI cleanup and modal/menu normalization

The Temple interface received a meaningful polish pass:

- modal behavior was normalized
- repeated menu/modal visibility logic was centralized
- header/menu HTML was cleaned up
- inline admin layout styles were moved into `static/style.css`
- admin page layout was moved off inline CSS and into stylesheet classes

### Cleanup work and editor diagnostics

Completed cleanup:

- stale HTML inline-style problems in `admin.html` were removed
- the invalid `github/issue_read` tool reference was removed from agent metadata
- the repo no longer contains that bad tool reference
- CSS/editor lint issues related to actual file contents were resolved

One issue remains unresolved at the **editor extension / VS Code problem-state level**:

- a sticky `Explore.agent.md` problem remained visible in VS Code even after:
  - repo cleanup
  - file content verification
  - extension host restart
  - reloads
  - temp rename through Git

Important interpretation:

- this appears to be an editor/extension cache or stale diagnostic identity issue
- Git itself is clean
- the repo tracks lowercase `explore.agent.md`
- the live file contents are clean

This should be treated as a tooling-state issue unless it later proves to affect actual agent execution.

---

## What is complete in the larger phase plan

### Phase 6 status

Phase 6 is now effectively complete enough to hand off.

That includes:

- seeker profile stabilization
- entitlement lifecycle authority model
- admin role/guard model
- admin audit and reporting foundation
- admin operator UI for read and mutation flows
- front-end cleanup sufficient for transition

What is **not** fully complete in a perfect sense, but is acceptable for handoff:

- sticky VS Code diagnostic around `Explore.agent.md`
- deeper admin UI polish could continue later, but it is no longer blocking
- more destructive mutation controls should continue to be handled carefully in testing

---

## Recommended next phase

## Phase 7: billing / banking / subscription domain

This should begin in a **new chat**.

The next phase should not start with UI changes. It should start with architecture and data model authority.

### Recommended order for Phase 7

1. **Decide payment architecture**
   - donations only?
   - subscriptions only?
   - both donations and subscriptions?
   - ACH / bank linking now or later?

2. **Choose provider strategy**
   - Stripe-first is the most practical default for this stage
   - Plaid-first only if bank-linking/data is the immediate priority
   - direct bank integration should come later, not first

3. **Define the Phase 7 billing schema before coding**
   Recommended tables:
   - `subscriptions`
   - `payment_events`
   - `plan_catalog`
   - possibly `billing_customers`
   - possibly `donation_events` if donations remain distinct from subscriptions

4. **Define the authority model clearly**
   The recommended model is:
   - provider event stream is the billing source of truth
   - entitlement state is projected from billing lifecycle state
   - admin override remains available but explicit and audited
   - `plan_code` remains the purchased / assigned plan marker
   - effective access remains computed from lifecycle authority, not only raw plan

5. **Map payment events to entitlement events**
   Examples:
   - invoice paid → renewal success
   - invoice failed → grace
   - grace expired → expired/downgraded access
   - cancel at period end reached → cancelled
   - manual override → explicit admin action log

6. **Only after schema and event model are agreed, begin implementation**

---

## Banking requirements to gather before Phase 7 work

Before implementation, gather:

- desired payment model
  - recurring subscription
  - one-time donation
  - both
- legal/business entity information
- bank account you want the platform to settle into
- support email and business identity details
- whether you want users to link bank accounts directly, or only pay by card/ACH
- whether payouts to others are in scope now or much later

### Practical recommendation

For this project stage, start with:

- **Stripe-first** for subscriptions and donations
- consider bank-linking only if there is a clear product need now
- defer direct bank API complexity until later

---

## LLaMA integration path forward

LLaMA integration remains important, but it should continue to follow the operational foundation rather than jump ahead of payments and entitlement.

### Recommended sequence

### Phase 7
- finish billing/subscription authority first

### Phase 8
- corpus governance and oracle quality improvements
- retrieval quality, memory weighting, citation/debug surfaces, corpus provenance

### Phase 9
- LLaMA/router integration and model arbitration

### LLaMA-specific forward path

When LLaMA work resumes, it should focus on:

- message routing and arbitration logic
- local inference cost control
- Moses vs Hathor routing clarity
- personal memory vs canonical corpus weighting
- shadow mode vs active-answer mode
- operational observability for local model decisions

The current recommendation remains:

- keep LLaMA integration behind stable identity, entitlement, and admin operations
- do not let LLaMA/router work outpace payment/billing authority unless intentionally switching into research mode

---

## Remaining caution items

- test admin mutation actions conservatively on the owner account
- avoid accidental role downgrade of the owner account
- avoid unnecessary entitlement overrides without logging expected before/after state
- treat the lingering `Explore.agent.md` problem as a tooling issue unless it causes actual runtime/tool failures

---

## Recommended immediate next steps

1. Start a **fresh chat** for Phase 7
2. Begin with architecture, not code edits
3. Decide provider direction:
   - Stripe-first recommended
4. Define the full billing schema and event model
5. Then implement subscription and payment authority
6. Revisit LLaMA/router work only after billing authority is structurally clear

---

## Suggested framing for the next chat

The next chat should begin as a **Phase 7 architecture handoff**, not as an ad hoc coding thread.

It should assume:

- Phase 6 is complete enough to move on
- admin/reporting/operator control exists
- entitlement projection exists
- the next focus is subscription + payment + banking architecture
- LLaMA integration remains in the roadmap, but after payment authority is designed

