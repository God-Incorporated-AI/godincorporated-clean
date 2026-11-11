"use strict";
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const source = fs.readFileSync(process.env.GODINC_A7C5G5_UI_BLOCK, "utf8");
const ids = ["stripeReupOfferPanel", "stripeReupOfferCopy", "stripeReupReviewBtn",
  "stripeReupConfirmPanel", "stripeReupConfirmCopy", "stripeReupAcknowledge",
  "stripeReupChargeBtn", "stripeReupCancelBtn"];
const originalQuote = {
  eligible: true, quote_only: true, plan_code: "seeker", support_mode: "monthly_recurring",
  currency: "usd", amount_cents: 99, new_period_question_limit: 33, questions_used: 33,
  existing_period_ends_at: new Date(Date.now() + 8 * 86400000).toISOString(),
  requires_explicit_charge_confirmation: true, payment_confirmed: false, questions_replenished: false
};
const identity = () => ({ authenticated: true, plan_code: "seeker",
  entitlement: { status: "active" }, usage: {
    is_unlimited_questions: false, question_limit: 33, questions_remaining: 0
  }
});
function makeCase(options = {}) {
  const elements = Object.fromEntries(ids.map(id => [id, {
    hidden: true, disabled: false, checked: false, style: { display: "none" },
    listeners: {}, textContent: "",
    addEventListener(event, cb) { this.listeners[event] = cb; },
    fire(event) { if (this.listeners[event]) return this.listeners[event](); }
  }]));
  const calls = []; const notices = [];
  const queues = [];
  const context = {
    document: { getElementById: id => elements[id] },
    window: { crypto: { randomUUID: () => "01234567-89ab-4def-8abc-0123456789ab" } },
    Intl, Date, Number, JSON, Boolean, String,
    isNativeIOSApp: () => !!options.native,
    currentIdentity: options.identity === undefined ? identity() : options.identity,
    identityFetch: async (url, req) => {
      calls.push({ url, req });
      if (!queues.length) throw new Error("No fake fetch response");
      const item = queues.shift();
      if (typeof item === "function") return item();
      if (item instanceof Error) throw item;
      return { ok: item.ok === undefined ? true : item.ok, value: item.data };
    },
    safeReadJson: async res => res.value,
    showFeedbackModal: (message, buttons, title) => notices.push({ message, title })
  };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { context, elements, calls, notices, queues,
    quote: (value = originalQuote) => queues.push({ data: value }),
    get: (expression) => vm.runInContext(expression, context)
  };
}
async function flushPromised(fn) { await fn(); await Promise.resolve(); }
let tests = 0;
async function testcase(name, fn) { await fn(); tests++; process.stdout.write("PASS " + name + "\n"); }
(async () => {
  await testcase("anonymous has no quote request", async () => {
    const c = makeCase({ identity: null }); await c.get("refreshStripeReupQuote()")
    assert.equal(c.calls.length, 0);
    assert.equal(c.elements.stripeReupOfferPanel.style.display, "none");
  });
  await testcase("native Apple app has no quote request", async () => {
    const c = makeCase({ native: true }); await c.get("refreshStripeReupQuote()")
    assert.equal(c.calls.length, 0);
  });
  await testcase("unexhausted seeker never requests quote", async () => {
    const c = makeCase({ identity: { ...identity(), usage: { ...identity().usage, questions_remaining: 1 } } });
    await c.get("refreshStripeReupQuote()"); assert.equal(c.calls.length, 0);
  });
  await testcase("ineligible quote stays hidden", async () => {
    const c = makeCase(); c.quote({eligible:false}); await c.get("refreshStripeReupQuote()");
    assert.equal(c.elements.stripeReupOfferPanel.hidden, true);
  });
  await testcase("disabled quote status 404 stays hidden", async () => {
    const c = makeCase(); c.queues.push({ ok: false, data: {} }); await c.get("refreshStripeReupQuote()");
    assert.equal(c.elements.stripeReupOfferPanel.hidden, true);
  });
  await testcase("malformed price never displays an offer", async () => {
    const c = makeCase(); c.quote({ ...originalQuote, amount_cents: "99" });
    await c.get("refreshStripeReupQuote()"); assert.equal(c.elements.stripeReupOfferPanel.hidden, true);
  });
  await testcase("stale quote after account change is ignored", async () => {
    const c = makeCase(); c.quote();
    c.queues[0] = () => { c.context.currentIdentity = null; return { ok: true, value: originalQuote } };
    await c.get("refreshStripeReupQuote()"); assert.equal(c.elements.stripeReupOfferPanel.hidden, true);
  });
  await testcase("quote shows additional charge after exact eligible response", async () => {
    const c = makeCase(); c.quote(); await c.get("refreshStripeReupQuote()");
    assert.equal(c.elements.stripeReupOfferPanel.hidden, false);
    assert.match(c.elements.stripeReupOfferCopy.textContent, /\$0\.99/);
    assert.equal(c.calls[0].req.method, "GET");
  });
  await testcase("review shows period reset, checkbox default unchecked, charge disabled", async () => {
    const c = makeCase(); c.quote(); await c.get("refreshStripeReupQuote()");
    c.elements.stripeReupReviewBtn.fire("click");
    assert.equal(c.elements.stripeReupConfirmPanel.hidden, false);
    assert.equal(c.elements.stripeReupAcknowledge.checked, false);
    assert.equal(c.elements.stripeReupChargeBtn.disabled, true);
    assert.match(c.elements.stripeReupConfirmCopy.textContent, /billing date will shift/);
    assert.match(c.elements.stripeReupConfirmCopy.textContent, /33 questions/);
  });
  await testcase("no charge without acknowledgment", async () => {
    const c = makeCase(); c.quote(); await c.get("refreshStripeReupQuote()");
    c.elements.stripeReupReviewBtn.fire("click");
    await c.get("submitStripeReup()"); assert.equal(c.calls.length, 1);
  });
  await testcase("single acknowledgment charge requires re-quote and returns pending only", async () => {
    const c = makeCase(); c.quote(); await c.get("refreshStripeReupQuote()");
    c.elements.stripeReupReviewBtn.fire("click");
    c.elements.stripeReupAcknowledge.checked = true;
    c.elements.stripeReupAcknowledge.fire("change");
    assert.equal(c.elements.stripeReupChargeBtn.disabled, false);
    c.quote(); c.queues.push({data: {ok:true,status:"awaiting_verified_payment",payment_confirmed:false,questions_replenished:false}});
    await c.get("submitStripeReup()");
    assert.equal(c.calls.filter(v => v.req.method === "POST").length, 1);
    const post = c.calls.find(v => v.req.method === "POST");
    assert.equal(post.url, "/billing/stripe/early-reup");
    assert.equal(JSON.parse(post.req.body).confirm_charge, true);
    assert.equal(JSON.parse(post.req.body).purchase_request_id, "01234567-89ab-4def-8abc-0123456789ab");
    const reviewedTerms = JSON.parse(post.req.body);
    assert.equal(reviewedTerms.confirmed_amount_cents, 99);
    assert.equal(reviewedTerms.confirmed_currency, "usd");
    assert.equal(reviewedTerms.confirmed_question_limit, 33);
    assert.equal(reviewedTerms.confirmed_existing_period_ends_at, originalQuote.existing_period_ends_at);
    assert.match(c.notices[0].message, /NOT yet been verified/);
    c.quote(); await c.get("submitStripeReup()");
    assert.equal(c.calls.filter(v => v.req.method === "POST").length, 1);
  });
  await testcase("changed quote prevents charge", async () => {
    const c = makeCase(); c.quote(); await c.get("refreshStripeReupQuote()");
    c.elements.stripeReupReviewBtn.fire("click");
    c.elements.stripeReupAcknowledge.checked = true; c.quote({ ...originalQuote, amount_cents: 299 });
    await c.get("submitStripeReup()");
    assert.equal(c.calls.filter(v => v.req.method === "POST").length, 0);
    assert.match(c.notices[0].message, /No charge request/);
  });
  await testcase("missing crypto prevents charge", async () => {
    const c = makeCase(); c.quote(); await c.get("refreshStripeReupQuote()");
    c.elements.stripeReupReviewBtn.fire("click"); c.elements.stripeReupAcknowledge.checked = true;
    c.context.window.crypto = undefined;
    c.quote(); await c.get("submitStripeReup()");
    assert.equal(c.calls.filter(v => v.req.method === "POST").length, 0);
  });
  await testcase("HTTP failure after POST holds and never retries", async () => {
    const c = makeCase(); c.quote(); await c.get("refreshStripeReupQuote()");
    c.elements.stripeReupReviewBtn.fire("click"); c.elements.stripeReupAcknowledge.checked = true;
    c.quote(); c.queues.push({ok:false, data:{detail:"server held"}});
    await c.get("submitStripeReup()"); await c.get("submitStripeReup()");
    assert.equal(c.calls.filter(v => v.req.method === "POST").length, 1);
    assert.match(c.notices[0].message, /Do not retry/);
  });
  await testcase("network error after POST holds and never retries", async () => {
    const c = makeCase(); c.quote(); await c.get("refreshStripeReupQuote()");
    c.elements.stripeReupReviewBtn.fire("click"); c.elements.stripeReupAcknowledge.checked = true;
    c.quote(); c.queues.push(new Error("network"));
    await c.get("submitStripeReup()"); await c.get("submitStripeReup()");
    assert.equal(c.calls.filter(v => v.req.method === "POST").length, 1);
    assert.match(c.notices[0].message, /uncertain/);
  });
  await testcase("ordinary checkout and StoreKit not referenced in review block", async () => {
    assert.ok(!source.includes("/billing/checkout-session"));
    assert.ok(!source.includes("templeStoreKit"));
  });
  process.stdout.write("RESULT: PASS " + tests + " isolated web UI runtime tests\n");
})().catch(error => { console.error(error); process.exitCode = 1; });
