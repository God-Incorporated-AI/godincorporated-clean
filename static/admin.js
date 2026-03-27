document.addEventListener("DOMContentLoaded", () => {
  const adminIdentity = document.getElementById("adminIdentity");

  const overviewDays = document.getElementById("overviewDays");
  const refreshOverviewBtn = document.getElementById("refreshOverviewBtn");
  const overviewStatus = document.getElementById("overviewStatus");
  const overviewMetrics = document.getElementById("overviewMetrics");
  const overviewBreakdown = document.getElementById("overviewBreakdown");

  const adminSearchForm = document.getElementById("adminSearchForm");
  const searchEmail = document.getElementById("searchEmail");
  const searchDisplayName = document.getElementById("searchDisplayName");
  const searchSeekerId = document.getElementById("searchSeekerId");
  const searchLimit = document.getElementById("searchLimit");
  const searchStatus = document.getElementById("searchStatus");
  const searchResults = document.getElementById("searchResults");
  const searchRaw = document.getElementById("searchRaw");

  const detailUserId = document.getElementById("detailUserId");
  const loadDetailBtn = document.getElementById("loadDetailBtn");
  const detailStatus = document.getElementById("detailStatus");
  const detailCards = document.getElementById("detailCards");
  const detailRaw = document.getElementById("detailRaw");

  const adminActionsLimit = document.getElementById("adminActionsLimit");
  const refreshAdminActionsBtn = document.getElementById("refreshAdminActionsBtn");
  const adminActionsStatus = document.getElementById("adminActionsStatus");
  const adminActionsList = document.getElementById("adminActionsList");
  const adminActionsRaw = document.getElementById("adminActionsRaw");

  function pretty(data) {
    return JSON.stringify(data, null, 2);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function renderMetricCard(label, value) {
    return `
      <div class="metric-card">
        <div class="metric-label">${escapeHtml(label)}</div>
        <div class="metric-value">${escapeHtml(value)}</div>
      </div>
    `;
  }

  function renderKV(label, value, mono = false) {
    return `
      <div class="kv">
        <div class="label">${escapeHtml(label)}</div>
        <div class="${mono ? "mono" : ""}">${escapeHtml(value ?? "—")}</div>
      </div>
    `;
  }

  function renderSummaryCard(title, items) {
    return `
      <div class="summary-card">
        <h3>${escapeHtml(title)}</h3>
        ${items.length ? items.join("") : '<div class="muted">No data.</div>'}
      </div>
    `;
  }

  async function safeReadJson(response) {
    try {
      return await response.json();
    } catch (err) {
      return { error: "Invalid JSON response" };
    }
  }

  async function fetchAdminMe() {
    const response = await fetch("/admin/me", { credentials: "same-origin" });
    const data = await safeReadJson(response);

    if (!response.ok) {
      adminIdentity.textContent = data.error || "Failed to load admin identity.";
      return;
    }

    adminIdentity.textContent =
      `Signed in as ${data.admin.display_name} (${data.admin.email}) — role: ${data.admin.role}`;
  }

  function renderOverview(report) {
    const userSummary = report.users || {};
    const oracleSummary = report.oracle || {};
    const adminSummary = report.admin || {};

    overviewMetrics.innerHTML = [
      renderMetricCard("Total users", userSummary.total_users ?? 0),
      renderMetricCard("Verified users", userSummary.verified_users ?? 0),
      renderMetricCard("Users created in window", userSummary.users_created_in_window ?? 0),
      renderMetricCard("Users logged in in window", userSummary.users_logged_in_in_window ?? 0),
      renderMetricCard("Total questions", oracleSummary.total_questions ?? 0),
      renderMetricCard("Authenticated questions", oracleSummary.authenticated_questions ?? 0),
      renderMetricCard("Anonymous questions", oracleSummary.anonymous_questions ?? 0),
      renderMetricCard("Admin actions", adminSummary.total_admin_actions ?? 0)
    ].join("");

    const roles = (userSummary.roles || []).map(
      row => renderKV(row.role, row.total)
    );

    const entitlements = (userSummary.entitlement_statuses || []).map(
      row => renderKV(row.entitlement_status, row.total)
    );

    const plans = (userSummary.stored_plan_codes || []).map(
      row => renderKV(row.plan_code, row.total)
    );

    const modes = (oracleSummary.mode_counts || []).map(
      row => renderKV(row.mode, row.total)
    );

    overviewBreakdown.innerHTML = [
      renderSummaryCard("Roles", roles),
      renderSummaryCard("Entitlement Statuses", entitlements),
      renderSummaryCard("Stored Plan Codes", plans),
      renderSummaryCard("Oracle Modes", modes)
    ].join("");
  }

  async function loadOverview() {
    overviewStatus.textContent = "Loading overview...";

    const days = Number(overviewDays.value || 30);
    const response = await fetch(`/admin/reports/overview?days=${encodeURIComponent(days)}`, {
      credentials: "same-origin"
    });
    const data = await safeReadJson(response);

    if (!response.ok) {
      overviewStatus.textContent = data.error || "Failed to load overview.";
      overviewMetrics.innerHTML = "";
      overviewBreakdown.innerHTML = "";
      return;
    }

    overviewStatus.textContent = `Overview loaded for the last ${data.report.window_days} day(s).`;
    renderOverview(data.report);
  }

  function renderSearchResults(results) {
    if (!results.length) {
      searchResults.innerHTML = '<div class="muted">No matching users found.</div>';
      return;
    }

    searchResults.innerHTML = results.map(user => `
      <div class="result-card">
        <div><strong>${escapeHtml(user.display_name || "Unnamed User")}</strong></div>
        <div>${escapeHtml(user.email || "—")}</div>
        <div class="mono">${escapeHtml(user.id)}</div>
        <div class="muted">
          role=${escapeHtml(user.role)} · plan=${escapeHtml(user.plan_code)} · entitlement=${escapeHtml(user.entitlement_status)}
        </div>
        <div style="margin-top: 10px;">
          <button type="button" class="load-user-detail-btn" data-user-id="${escapeHtml(user.id)}">Load Detail</button>
        </div>
      </div>
    `).join("");

    document.querySelectorAll(".load-user-detail-btn").forEach(btn => {
      btn.addEventListener("click", async () => {
        const userId = btn.getAttribute("data-user-id");
        detailUserId.value = userId;
        await loadUserDetail(userId);
      });
    });
  }

  function renderUserDetail(user) {
    const scrolls = user.scrolls || {};
    const donations = user.donations || {};
    const entitlement = user.entitlement || {};
    const usage = user.usage || {};

    detailCards.innerHTML = `
      <div class="detail-card">
        <h3>Identity</h3>
        ${renderKV("Display name", user.display_name)}
        ${renderKV("Email", user.email)}
        ${renderKV("User ID", user.id, true)}
        ${renderKV("Seeker ID", user.seeker_id, true)}
        ${renderKV("Role", user.role)}
        ${renderKV("Email verified", user.email_verified)}
        ${renderKV("Created at", user.created_at)}
        ${renderKV("Last login", user.last_login)}
      </div>

      <div class="detail-card">
        <h3>Titles and Scrolls</h3>
        ${renderKV("Title", user.title)}
        ${renderKV("Combined title", user.combined_title)}
        ${renderKV("Authoritative scroll count", scrolls.authoritative_scroll_count)}
        ${renderKV("Legacy scroll count", scrolls.legacy_scroll_count)}
      </div>

      <div class="detail-card">
        <h3>Entitlement</h3>
        ${renderKV("Stored plan", entitlement.raw_plan_code)}
        ${renderKV("Effective plan", entitlement.effective_plan_code)}
        ${renderKV("Status", entitlement.entitlement_status)}
        ${renderKV("Subscription started", entitlement.subscription_started_at)}
        ${renderKV("Current period started", entitlement.current_period_started_at)}
        ${renderKV("Renews at", entitlement.subscription_renews_at)}
        ${renderKV("Expires at", entitlement.subscription_expires_at)}
        ${renderKV("Grace ends at", entitlement.grace_period_ends_at)}
        ${renderKV("Cancel at period end", entitlement.cancel_at_period_end)}
        ${renderKV("Is entitled", entitlement.is_entitled)}
        ${renderKV("Is in grace", entitlement.is_in_grace)}
        ${renderKV("Downgraded for access", entitlement.downgraded_for_access)}
      </div>

      <div class="detail-card">
        <h3>Usage and Donations</h3>
        ${renderKV("Current period questions", usage.current_period_questions_used)}
        ${renderKV("Lifetime questions", usage.lifetime_questions_used)}
        ${renderKV("Question limit", usage.question_limit_display ?? usage.question_limit)}
        ${renderKV("Questions remaining", usage.questions_remaining_display)}
        ${renderKV("Unlimited questions", usage.is_unlimited_questions)}
        ${renderKV("Donation count", donations.donation_count)}
        ${renderKV("Money donated", donations.money_donated)}
        ${renderKV("Donation source", donations.donation_source)}
      </div>
    `;
  }

  async function loadUserDetail(userId) {
    const trimmed = (userId || "").trim();

    if (!trimmed) {
      detailStatus.textContent = "Enter a user ID first.";
      detailCards.innerHTML = "";
      detailRaw.textContent = "No user loaded yet.";
      return;
    }

    detailStatus.textContent = "Loading user detail...";
    detailCards.innerHTML = "";

    const response = await fetch(`/admin/users/${encodeURIComponent(trimmed)}/detail`, {
      credentials: "same-origin"
    });
    const data = await safeReadJson(response);

    if (!response.ok) {
      detailStatus.textContent = data.error || "Failed to load user detail.";
      detailRaw.textContent = pretty(data);
      detailCards.innerHTML = "";
      return;
    }

    detailStatus.textContent = `Loaded user detail for ${data.user.display_name || data.user.email}.`;
    detailRaw.textContent = pretty(data.user);
    renderUserDetail(data.user);
  }

  async function loadAdminActions() {
    adminActionsStatus.textContent = "Loading admin actions...";
    adminActionsList.innerHTML = "";

    const limit = Number(adminActionsLimit.value || 100);
    const response = await fetch(`/admin/reports/admin-actions?limit=${encodeURIComponent(limit)}`, {
      credentials: "same-origin"
    });
    const data = await safeReadJson(response);

    if (!response.ok) {
      adminActionsStatus.textContent = data.error || "Failed to load admin actions.";
      adminActionsRaw.textContent = pretty(data);
      return;
    }

    const rows = data.results || [];
    adminActionsStatus.textContent = `${rows.length} admin action(s) loaded.`;
    adminActionsRaw.textContent = pretty(rows);

    if (!rows.length) {
      adminActionsList.innerHTML = '<div class="muted">No admin actions recorded yet.</div>';
      return;
    }

    adminActionsList.innerHTML = rows.map(row => `
      <div class="action-card">
        <div><strong>${escapeHtml(row.action_type)}</strong></div>
        <div class="muted">${escapeHtml(row.created_at)}</div>
        <div class="mono">admin=${escapeHtml(row.admin_user_id)}</div>
        <div class="mono">target=${escapeHtml(row.target_user_id || "—")}</div>
        <details>
          <summary>Payload</summary>
          <pre>${escapeHtml(pretty(row.action_payload || {}))}</pre>
        </details>
      </div>
    `).join("");
  }

  adminSearchForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    const params = new URLSearchParams();

    if (searchEmail.value.trim()) {
      params.set("email", searchEmail.value.trim());
    }

    if (searchDisplayName.value.trim()) {
      params.set("display_name", searchDisplayName.value.trim());
    }

    if (searchSeekerId.value.trim()) {
      params.set("seeker_id", searchSeekerId.value.trim());
    }

    params.set("limit", String(Number(searchLimit.value || 25)));

    searchStatus.textContent = "Searching...";
    searchResults.innerHTML = "";
    searchRaw.textContent = "";

    const response = await fetch(`/admin/users/search?${params.toString()}`, {
      credentials: "same-origin"
    });
    const data = await safeReadJson(response);

    if (!response.ok) {
      searchStatus.textContent = data.error || "Search failed.";
      searchRaw.textContent = pretty(data);
      return;
    }

    searchStatus.textContent = `${data.results.length} result(s) found.`;
    searchRaw.textContent = pretty(data.results);
    renderSearchResults(data.results);

    if (data.results.length === 1 && data.results[0].id) {
      detailUserId.value = data.results[0].id;
      await loadUserDetail(data.results[0].id);
    }
  });

  loadDetailBtn.addEventListener("click", async () => {
    await loadUserDetail(detailUserId.value);
  });

  refreshOverviewBtn.addEventListener("click", async () => {
    await loadOverview();
  });

  refreshAdminActionsBtn.addEventListener("click", async () => {
    await loadAdminActions();
  });

  fetchAdminMe();
  loadOverview();
  loadAdminActions();
});
