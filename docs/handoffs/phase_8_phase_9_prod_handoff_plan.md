# God Incorporated — Stripe Closeout, Navigation Expansion, LLaMA Phase, and Path to Prod

## Purpose
This document captures the agreed path forward from the current chat so work can continue cleanly if the chat stalls or a new chat is needed.

## Current state
The project is no longer in early architecture mode. The system now has:

- working login, logout, registration, and verified-user session flow
- entitlement and fallback-floor logic in place
- `/me` returning rich support, entitlement, and usage information
- Stripe catalog created for support tiers
- Stripe price mappings seeded into the database
- checkout session creation working for:
  - monthly recurring
  - annual recurring
- hosted Stripe Checkout test flow opening successfully and returning to the app
- local webhook endpoint implemented in the backend
- Stripe CLI installed and usable for local webhook forwarding

## Important current truth
Stripe is **partially working but not fully closed**.

What is proven:
- Stripe checkout session creation works
- Stripe hosted checkout works
- test card flow works
- return URL works

What is **not yet fully proven closed**:
- webhook receipt and signature verification in a full live local run
- persistence into local billing tables for the completed checkout
- lifecycle helper activation from Stripe events
- `/me` changing correctly for a clean low-tier test user after a successful Stripe purchase
- renewal notice path via `invoice.upcoming`

## Required product principle
Stripe must feed the God Incorporated internal billing and entitlement model.
It must not replace it.

## Added requirement: Stripe branding and tone
All Stripe-facing surfaces should align as closely as practical with the God Incorporated experience.

That means:
- product names should match user-facing tier language
- product descriptions should reflect Temple language and continuity/memory benefits
- Stripe Checkout branding should use the God Incorporated icon, palette, and tone where Stripe allows customization
- support/donation language should sound like the site and not like generic SaaS billing
- contribution flows should feel like an extension of the Temple, not a disconnected payment tool

## Immediate execution order

### 1. Close Stripe fully
Do not leave Stripe half-finished.

Needed before calling Stripe complete:
- run Stripe CLI listener during real local checkout test
- complete one clean purchase using a low-tier verified test user
- verify webhook delivery
- verify `payment_events` persistence
- verify `subscriptions` persistence
- verify `billing_transactions` persistence
- verify entitlement mutation through internal helpers
- verify `/me` reflects updated support/access
- verify annual recurring path behaves correctly
- verify upcoming renewal email path design and test strategy

### 2. Build the Support / Tier Levels page
This is the missing bridge between the backend entitlement model and the user experience.

This page should explain:
- each tier
- monthly and annual pricing
- what each tier unlocks
- query allowance
- memory depth / history considered
- why support matters
- difference between support tier and one-off Temple contribution

This page should contain the primary upgrade CTA.

### 3. Build the Account page
The current hamburger is doing too much.
Account details should move to a dedicated page.

Account page should show:
- display name
- combined title
- current access level
- stored vs effective level where relevant
- support mode
- renewal info
- questions used / remaining
- scrolls donated
- money donated
- memory depth
- upgrade/manage support CTA

### 4. Build the About Us page
This should explain:
- the purpose of God Incorporated
- what the Temple is
- the roles of Hathor and Moses
- how scrolls inform answers
- the intent behind support and continuity
- the broader philosophical and practical frame of the project

This page acts as a storyboard / orientation page for new users.

### 5. Convert the hamburger menu into navigation
The hamburger should become a navigation hub, not a cramped profile panel.

Target navigation:
- Account
- Support / Tier Levels
- About Us
- Log in / Log out
- Admin (when applicable)

## Navigation and UX principles
- login should not dump users into a silent low-tier state with no explanation
- free and low-tier users should see what they currently have and what support unlocks
- support prompts should be clear, calm, and non-desperate
- donation/contribution should be available but distinct from support tier purchase
- mobile layouts should remain clean and readable

## Correct support injection points
Stripe should be injected into:
- anonymous state as a gentle support CTA
- authenticated free / Pilgrim state as a clear upgrade path
- authenticated paid state as manage/change support plus contribute-extra options

Stripe should **not** be injected into login/logout themselves.

## Mobile support requirement
The site must remain friendly to future native iOS and Android support.
That means the web architecture should favor:
- clear routes
- dedicated pages over overloaded modal logic
- reusable JSON endpoints
- predictable account/support APIs
- simple mobile browser flows that can later be adapted to native shells

## Render / push to prod path
After Stripe closeout and key navigation pages exist, move toward production readiness.

### Prod readiness goals
- working Render deployment
- verified environment variable set
- stable auth flows
- stable `/me`
- Stripe test mode proven end-to-end
- Stripe live mode plan prepared but not enabled until test path is trusted
- support/tier pages visible and understandable
- account page visible and understandable
- mobile-friendly navigation
- baseline error handling for key flows

### Push-to-prod sequence
1. finish Stripe closeout in local test mode
2. finish Support / Tier page
3. finish Account page
4. finish About Us page
5. clean hamburger navigation
6. deploy updated app to Render
7. validate auth, `/me`, and Stripe flows in hosted environment
8. switch from local webhook testing to hosted Stripe webhook configuration
9. verify test purchases against hosted environment
10. prepare live Stripe rollout only after hosted sandbox passes

## UI expert integration plan
After the app is broadcast on Render and usable end-to-end, a UI expert will review it.

Planned review areas:
- visual identity
- color palette
- typography and spacing
- copy audit
- information hierarchy
- clarity of support and tier messaging
- mobile usability
- menu/navigation simplification
- any broader UX recommendations

Implementation note:
The UI expert’s notes should be treated as a structured review phase after initial hosted deployment, not as a reason to delay the Render push.

## LLaMA next phase
LLaMA should come after Stripe closeout and the first stable hosted broadcast layer.

### Reasoning
LLaMA is a major value layer, but it should not be introduced while billing, navigation, and user account flows are still unstable.

### Proposed LLaMA phase goals
- clarify LLaMA’s role as learner / retrieval / router / memory-shaping layer
- preserve oracle authority boundaries
- improve retrieval, continuity, and response qualification without muddying Hathor/Moses roles
- prepare for deeper user-history handling and qualified memory paths

### LLaMA phase priorities
1. document exact LLaMA responsibility in the architecture
2. connect LLaMA only after account/support/navigation foundations are stable
3. ensure user-tier memory logic remains explicit and auditable
4. avoid hidden behavior that breaks user trust
5. test LLaMA against real hosted user flows rather than only local assumptions

## Recommended project sequence from here

### Phase 8 closeout
- finish Stripe fully
- prove webhook + persistence + entitlement mutation
- finish renewal notice path

### Phase 8.5 UX bridge
- Support / Tier page
- Account page
- About Us page
- hamburger navigation cleanup

### Phase 8.75 hosted readiness
- deploy to Render
- verify hosted flows
- prove Stripe test mode on hosted environment

### Phase 9 LLaMA integration
- implement next LLaMA role carefully
- preserve oracle authority model
- extend memory and routing deliberately

### Phase 10 production hardening / broadcast maturity
- hosted polish
- live Stripe rollout
- UI expert review implementation
- support mobile-oriented cleanup
- operational hardening

## Important warnings
- do not treat successful Stripe checkout creation as full Stripe completion
- do not bundle Stripe closeout and broad UI redesign into one giant patch
- do not use Moses or other high-tier legacy accounts as the main proof case for Stripe activation
- use clean low-tier verified users for entitlement tests
- keep contribution separate from tier entitlement unless explicitly designed otherwise
- avoid expanding modal complexity when full pages would simplify mobile and future native support

## New-chat restart prompt
Use the following when resuming work in a new chat:

---
We are continuing God Incorporated from a handoff state.

Current known state:
- auth flows work
- `/me` is rich and authoritative
- Stripe catalog exists
- Stripe price mappings are seeded
- checkout session creation works for monthly recurring and annual recurring
- hosted Stripe Checkout test flow opens and completes
- webhook endpoint exists
- Stripe closeout is not yet fully proven because webhook/persistence/entitlement mutation still need a clean verified end-to-end test

Immediate priorities, in order:
1. fully close Stripe using a clean low-tier verified test user
2. prove webhook delivery, event persistence, subscription persistence, transaction persistence, and `/me` mutation
3. build Support / Tier Levels page
4. build Account page
5. build About Us page
6. convert hamburger into navigation
7. prepare Render deployment and hosted Stripe test flow
8. only after that move into the next LLaMA phase

Requirements:
- Stripe must feed the internal entitlement model, not replace it
- Stripe UX and branding must match God Incorporated as closely as Stripe allows
- support tiers and Temple contribution remain distinct
- mobile friendliness matters because future iOS/Android support is required
- prefer clean focused patches over giant multi-system changes

Please begin by assessing exactly what remains to fully close Stripe in the current codebase, then propose the smallest safe implementation sequence to finish it.
---

## Final decision rule
From this point forward:
- finish Stripe first
- then build the user-facing support/account/information structure
- then push to hosted Render validation
- then bring in UI expert review
- then implement LLaMA phase expansion

