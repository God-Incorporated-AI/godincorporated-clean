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

  function looksLikeIsoDate(value) {
    return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(value);
  }

  function formatDateTime(value) {
    if (!looksLikeIsoDate(value)) return formatValue(value);

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return formatValue(value);

    return date.toLocaleString([], {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit"
    });
  }

  function formatDateOnly(value) {
    if (!looksLikeIsoDate(value)) return formatValue(value);

    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return formatValue(value);

    return date.toLocaleDateString([], {
      year: "numeric",
      month: "short",
      day: "numeric"
    });
  }

  function formatUsageWindow(data) {
    const currentAccess = (data.current_access_plan_code || "").toLowerCase();
    const startedAt = data.usage?.usage_window_started_at;

    if (currentAccess === "pilgrim") return "Daily";
    if (["seeker", "magister", "sovereign", "philosophus", "theoricus"].includes(currentAccess)) {
      return "Monthly";
    }

    return formatDateOnly(startedAt);
  }

  function cleanSupportMessage(value) {
    if (!value) return "-";
    return String(value).replace(/T(\d{2}:\d{2}:\d{2}(?:\.\d+)?)?(?:[+-]\d{2}:\d{2}|Z)?/g, " ");
  }

  function formatVoiceAccess(voiceAccess) {
    if (!voiceAccess) return "-";

    const parts = [];

    if (voiceAccess.has_recurring_web_realtime) {
      if (voiceAccess.web_realtime_fair_use) {
        parts.push("Live realtime voice under fair-use monitoring");
      } else if (voiceAccess.web_realtime_monthly_turns) {
        parts.push(`${voiceAccess.web_realtime_monthly_turns} live realtime turns/month`);
      } else {
        parts.push("Live realtime voice");
      }
    } else {
      parts.push("Regular Speak voice with browser voice-out");
      if (voiceAccess.one_time_realtime_preview_turns) {
        parts.push(`${voiceAccess.one_time_realtime_preview_turns}-turn live preview`);
      }
    }

    if (voiceAccess.library_full_research) {
      parts.push("full library/research");
    } else if (voiceAccess.library_access) {
      parts.push("library access begins");
    }

    return parts.join(" · ");
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
    setText("accountLastLogin", formatDateTime(data.last_login));

    setText("accountCurrentAccess", formatValue(data.current_access_label || data.current_access_plan_code));
    setText("accountStoredLevel", formatValue(data.stored_plan_label || data.stored_plan_code));
    setText("accountSupportMode", formatValue(data.support?.mode_label || data.support?.mode));
    setText("accountMemoryDepth", formatValue(data.memory_depth));
    setText("accountVoiceAccess", formatVoiceAccess(data.voice_access));
    setText("accountSupportStatus", formatValue(data.support?.status));

    setText("accountQuestionsUsed", formatValue(data.usage?.questions_used));
    setText("accountQuestionLimit", data.usage?.is_unlimited_questions ? "Unlimited" : formatValue(data.usage?.question_limit_display ?? data.usage?.question_limit));
    setText("accountQuestionsRemaining", formatValue(data.usage?.questions_remaining_display ?? data.usage?.questions_remaining));
    setText("accountUsageWindow", formatUsageWindow(data));

    setText("accountScrolls", formatValue(data.scrolls_donated));
    setText("accountMoney", formatValue(data.money_donated));
    setText("accountDonationCount", formatValue(data.donation_count));

    setText("accountSupportMessage", cleanSupportMessage(data.support_message));
    setText("accountRenewalMessage", cleanSupportMessage(data.renewal_message));
  } catch (err) {
    loadingEl.hidden = true;
    authPromptEl.hidden = false;
    setText("accountSupportMessage", "Could not load account details.");
  }
});
