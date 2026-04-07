document.addEventListener("DOMContentLoaded", async function () {
  const loadingEl = document.getElementById("accountLoading");
  const authPromptEl = document.getElementById("accountAuthPrompt");
  const contentEl = document.getElementById("accountContent");

  function setText(id, value) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = value ?? "-";
  }

  function formatValue(value) {
    if (value === null || value === undefined || value === "") return "-";
    return String(value);
  }

  try {
    const response = await fetch("/me", { credentials: "same-origin" });
    const data = await response.json();

    loadingEl.hidden = true;

    if (!response.ok || !data.authenticated) {
      authPromptEl.hidden = false;
      return;
    }

    contentEl.hidden = false;

    setText("accountDisplayName", formatValue(data.display_name));
    setText("accountCombinedTitle", formatValue(data.combined_title));
    setText("accountEmail", formatValue(data.email));
    setText("accountLastLogin", formatValue(data.last_login));

    setText("accountCurrentAccess", formatValue(data.current_access_label || data.current_access_plan_code));
    setText("accountStoredLevel", formatValue(data.stored_plan_label || data.stored_plan_code));
    setText("accountSupportMode", formatValue(data.support?.mode_label || data.support?.mode));
    setText("accountMemoryDepth", formatValue(data.memory_depth));
    setText("accountSupportStatus", formatValue(data.support?.status));

    setText("accountQuestionsUsed", formatValue(data.usage?.questions_used));
    setText("accountQuestionLimit", data.usage?.is_unlimited_questions ? "Unlimited" : formatValue(data.usage?.question_limit_display ?? data.usage?.question_limit));
    setText("accountQuestionsRemaining", formatValue(data.usage?.questions_remaining_display ?? data.usage?.questions_remaining));
    setText("accountUsageWindow", formatValue(data.usage?.usage_window_started_at));

    setText("accountScrolls", formatValue(data.scrolls_donated));
    setText("accountMoney", formatValue(data.money_donated));
    setText("accountDonationCount", formatValue(data.donation_count));

    setText("accountSupportMessage", formatValue(data.support_message));
    setText("accountRenewalMessage", formatValue(data.renewal_message));
  } catch (err) {
    loadingEl.hidden = true;
    authPromptEl.hidden = false;
    setText("accountSupportMessage", "Could not load account details.");
  }
});
