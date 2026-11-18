"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(
  path.join(root, "static/temple.js"), "utf8"
);

function section(begin, end) {
  const start = source.indexOf(begin);
  assert(start >= 0, "Missing source anchor: " + begin);
  assert(
    source.indexOf(begin, start + begin.length) === -1,
    "Duplicate source anchor: " + begin
  );
  const stop = source.indexOf(end, start);
  assert(stop > start, "Missing ending anchor: " + end);
  return source.slice(start, stop);
}

const persistence = section(
  "const VALID_SUPPORT_PLAN_CODES",
  "let supportOpenedFromQuery = false"
);

const checkout = section(
  "async function launchStripeCheckout(",
  "// Toggle menu visibility"
);

function createStorage(initial = {}, restricted = false) {
  const values = new Map(Object.entries(initial));
  return {
    values,
    getItem(key) {
      if (restricted) throw Error("Storage disabled");
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      if (restricted) throw Error("Storage disabled");
      values.set(key, String(value));
    },
    removeItem(key) {
      if (restricted) throw Error("Storage disabled");
      values.delete(key);
    }
  };
}

function harness(options = {}) {
  const events = [];
  const intro = {hidden: true};
  const button = {
    disabled: false,
    textContent: "Select support"
  };
  const storage = createStorage(
    options.storage || {}, Boolean(options.restricted)
  );

  const context = {
    urlParams: new URLSearchParams(options.query || ""),
    localStorage: storage,
    currentIdentity: options.authenticated
      ? {authenticated: true}
      : null,
    supportModal: "support",
    registerModal: "register",
    loginModal: "login",
    loginError: {textContent: ""},
    window: {location: {href: "unchanged"}},
    document: {
      getElementById(id) {
        assert.equal(id, "registerSupportIntro");
        return intro;
      }
    },
    closeModal: name => events.push(["close", name]),
    openModal: name => events.push(["open", name]),
    clearAuthErrors: () => events.push(["clear_errors"]),
    showFeedbackModal: message =>
      events.push(["feedback", message]),
    setAuthenticatedMenuState: value =>
      events.push(["menu", value]),
    updateMenuToggleIdentity: value =>
      events.push(["menu_identity", value]),
    updateAdminNav: value =>
      events.push(["admin", value]),
    identityFetch: async (url, request) => {
      events.push(["request", url, request]);
      if (!options.response) {
        throw Error("Unexpected checkout request");
      }
      return options.response;
    },
    safeReadJson: async response => response.payload,
    updateIdentityDisplay: async () =>
      events.push(["identity_refresh"]),
    __button: button
  };

  vm.createContext(context, {
    codeGeneration: {strings: false, wasm: false}
  });
  vm.runInContext(
    persistence + "\n" + checkout,
    context,
    {timeout: 2500}
  );

  function evaluate(expression) {
    return vm.runInContext(expression, context, {timeout: 2500});
  }

  async function purchase(plan = "seeker", mode = "monthly_recurring") {
    context.__plan = plan;
    context.__mode = mode;
    return await evaluate(
      "launchStripeCheckout(__plan, __mode, __button)"
    );
  }

  return {
    context, storage, events, intro, button,
    evaluate, purchase,
    selection() {
      return JSON.parse(evaluate(
        "JSON.stringify(selectedSupportIntent)"
      ));
    },
    saved() {
      const value = storage.values.get(
        "godinc_pending_support_v1"
      );
      return value ? JSON.parse(value) : null;
    }
  };
}

const cases = [];
function test(name, fn) {
  cases.push([name, fn]);
}

const key = "godinc_pending_support_v1";
const valid = {
  planCode: "seeker",
  supportMode: "monthly_recurring"
};

test("Empty initial selection", () => {
  const h = harness();
  assert.deepEqual(h.selection(), {
    planCode: null, supportMode: null
  });
});

test("Restore fresh stored selection", () => {
  const h = harness({
    storage: {[key]: JSON.stringify({
      ...valid, savedAt: Date.now() - 3600000
    })}
  });
  assert.equal(h.selection().planCode, "seeker");
  assert.equal(
    h.selection().supportMode, "monthly_recurring"
  );
});

test("Expired selection is discarded", () => {
  const h = harness({
    storage: {[key]: JSON.stringify({
      ...valid, savedAt: Date.now() - 90000000
    })}
  });
  assert.equal(h.selection().planCode, null);
  assert.equal(h.storage.values.has(key), false);
});

test("Invalid stored plan is discarded", () => {
  const h = harness({
    storage: {[key]: JSON.stringify({
      planCode: "__invalid__",
      supportMode: "monthly_recurring",
      savedAt: Date.now()
    })}
  });
  assert.equal(h.selection().planCode, null);
  assert.equal(h.storage.values.has(key), false);
});

test("Valid URL selection takes precedence", () => {
  const h = harness({
    query: "?plan_code=seeker&support_mode=monthly_recurring",
    storage: {[key]: JSON.stringify({
      planCode: "__invalid__",
      supportMode: "__invalid__",
      savedAt: Date.now()
    })}
  });
  assert.deepEqual(h.selection(), valid);
});

test("Malformed stored JSON does not break startup", () => {
  const h = harness({storage: {[key]: "{invalid"}});
  assert.equal(h.selection().planCode, null);
});

test("Restricted storage does not break startup", () => {
  const h = harness({restricted: true});
  assert.equal(h.selection().planCode, null);
});

test("Remembered selection contains no account data", () => {
  const h = harness();
  assert.equal(
    h.evaluate(
      'rememberPendingSupportIntent("seeker","monthly_recurring")'
    ),
    true
  );
  assert.deepEqual(
    Object.keys(h.saved()).sort(),
    ["planCode", "savedAt", "supportMode"]
  );
  assert.equal(h.saved().planCode, "seeker");
});

test("Invalid selected mode is rejected", () => {
  const h = harness();
  assert.equal(
    h.evaluate(
      'rememberPendingSupportIntent("seeker","__invalid__")'
    ),
    false
  );
  assert.equal(h.saved(), null);
});

test("Unauthenticated checkout opens registration", async () => {
  const h = harness();
  await h.purchase();
  assert.equal(h.saved().planCode, "seeker");
  assert.equal(h.intro.hidden, false);
  assert(h.events.some(
    e => e[0] === "open" && e[1] === "register"
  ));
  assert(!h.events.some(e => e[0] === "request"));
});

test("Invalid checkout selection fails closed", async () => {
  const h = harness();
  await h.purchase("__invalid__", "monthly_recurring");
  assert.equal(h.saved(), null);
  assert(h.events.some(e => e[0] === "feedback"));
  assert(!h.events.some(e => e[0] === "open"));
});

test("Expired session opens login and preserves plan", async () => {
  const h = harness({
    authenticated: true,
    response: {ok: false, status: 401, payload: {}}
  });
  await h.purchase();
  assert.equal(h.context.currentIdentity, null);
  assert.equal(h.saved().planCode, "seeker");
  assert(h.events.some(
    e => e[0] === "open" && e[1] === "login"
  ));
  assert(h.context.loginError.textContent.includes("expired"));
  assert.equal(h.button.disabled, false);
});

test("Authenticated checkout uses selected plan", async () => {
  const url = "https://example.invalid/synthetic-checkout";
  const h = harness({
    authenticated: true,
    response: {
      ok: true, status: 200,
      payload: {checkout_url: url}
    }
  });
  await h.purchase();
  const requests = h.events.filter(e => e[0] === "request");
  assert.equal(requests.length, 1);
  assert.equal(requests[0][1], "/billing/checkout-session");
  assert.deepEqual(JSON.parse(requests[0][2].body), {
    plan_code: "seeker",
    support_mode: "monthly_recurring"
  });
  assert.equal(h.context.window.location.href, url);
});

test("Checkout failure does not redirect", async () => {
  const h = harness({
    authenticated: true,
    response: {
      ok: false, status: 400,
      payload: {error: "Synthetic checkout rejection"}
    }
  });
  await h.purchase();
  assert.equal(
    h.context.window.location.href, "unchanged"
  );
  assert(h.events.some(
    e => e[0] === "feedback"
      && e[1] === "Synthetic checkout rejection"
  ));
  assert.equal(h.button.disabled, false);
});

test("Subscription change refreshes identity", async () => {
  const h = harness({
    authenticated: true,
    response: {
      ok: true, status: 200,
      payload: {changed_subscription: true}
    }
  });
  await h.purchase();
  assert.equal(
    h.context.window.location.href, "unchanged"
  );
  assert(h.events.some(e => e[0] === "identity_refresh"));
});

test("Login continuation restores support modal", () => {
  const marker = "if (!isNativeIOSApp() &&";
  const index = source.lastIndexOf(marker);
  assert(index >= 0);
  const block = source.slice(index, index + 380);
  assert(block.includes("selectedSupportIntent.planCode"));
  assert(block.includes("selectedSupportIntent.supportMode"));
  assert(block.includes("renderSupportModal();"));
  assert(block.includes("openModal(supportModal);"));
  assert(block.includes("applySupportIntentSelection(true);"));
});

test("Registration preserves verification requirement", () => {
  assert(source.includes(
    "Account created. Verify your email, then log in"
  ));
  assert(source.includes("registerToLoginLink"));
  assert(source.includes("loginToRegisterLink"));
});

(async () => {
  let passed = 0;
  let failed = 0;

  for (const [name, run] of cases) {
    try {
      await run();
      passed++;
      console.log("PASS:", name);
    } catch (error) {
      failed++;
      console.error("FAIL:", name);
      console.error(
        error && error.stack
          ? error.stack.slice(0, 1800)
          : String(error)
      );
    }
  }

  if (failed) {
    console.error(
      "RESULT: FAIL " + failed +
      " of " + cases.length + " checks"
    );
    process.exitCode = 1;
  } else {
    console.log(
      "RESULT: PASS " + passed +
      " isolated web support flow checks"
    );
  }
})().catch(error => {
  console.error("STOP:", error.message);
  process.exitCode = 1;
});
