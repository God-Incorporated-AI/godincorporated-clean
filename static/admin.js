document.addEventListener("DOMContentLoaded", () => {
  const adminIdentity = document.getElementById("adminIdentity");
  const overviewDays = document.getElementById("overviewDays");
  const refreshOverviewBtn = document.getElementById("refreshOverviewBtn");
  const overviewOutput = document.getElementById("overviewOutput");

  const adminSearchForm = document.getElementById("adminSearchForm");
  const searchEmail = document.getElementById("searchEmail");
  const searchDisplayName = document.getElementById("searchDisplayName");
  const searchSeekerId = document.getElementById("searchSeekerId");
  const searchLimit = document.getElementById("searchLimit");
  const searchStatus = document.getElementById("searchStatus");
  const searchResults = document.getElementById("searchResults");

  const detailUserId = document.getElementById("detailUserId");
  const loadDetailBtn = document.getElementById("loadDetailBtn");
  const detailOutput = document.getElementById("detailOutput");

  const adminActionsLimit = document.getElementById("adminActionsLimit");
  const refreshAdminActionsBtn = document.getElementById("refreshAdminActionsBtn");
  const adminActionsOutput = document.getElementById("adminActionsOutput");

  function pretty(data) {
    return JSON.stringify(data, null, 2);
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

  async function loadOverview() {
    overviewOutput.textContent = "Loading overview...";

    const days = Number(overviewDays.value || 30);
    const response = await fetch(`/admin/reports/overview?days=${encodeURIComponent(days)}`, {
      credentials: "same-origin"
    });
    const data = await safeReadJson(response);

    if (!response.ok) {
      overviewOutput.textContent = pretty(data);
      return;
    }

    overviewOutput.textContent = pretty(data.report);
  }

  async function loadAdminActions() {
    adminActionsOutput.textContent = "Loading admin actions...";

    const limit = Number(adminActionsLimit.value || 100);
    const response = await fetch(`/admin/reports/admin-actions?limit=${encodeURIComponent(limit)}`, {
      credentials: "same-origin"
    });
    const data = await safeReadJson(response);

    if (!response.ok) {
      adminActionsOutput.textContent = pretty(data);
      return;
    }

    adminActionsOutput.textContent = pretty(data.results);
  }

  async function loadUserDetail(userId) {
    const trimmed = (userId || "").trim();

    if (!trimmed) {
      detailOutput.textContent = "Enter a user ID first.";
      return;
    }

    detailOutput.textContent = "Loading user detail...";

    const response = await fetch(`/admin/users/${encodeURIComponent(trimmed)}/detail`, {
      credentials: "same-origin"
    });
    const data = await safeReadJson(response);

    if (!response.ok) {
      detailOutput.textContent = pretty(data);
      return;
    }

    detailOutput.textContent = pretty(data.user);
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
    searchResults.textContent = "";

    const response = await fetch(`/admin/users/search?${params.toString()}`, {
      credentials: "same-origin"
    });
    const data = await safeReadJson(response);

    if (!response.ok) {
      searchStatus.textContent = data.error || "Search failed.";
      searchResults.textContent = pretty(data);
      return;
    }

    searchStatus.textContent = `${data.results.length} result(s) found.`;
    searchResults.textContent = pretty(data.results);

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
