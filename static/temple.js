document.addEventListener("DOMContentLoaded", function () {
  // Check for reset token in URL
  const urlParams = new URLSearchParams(window.location.search);
  const resetToken = urlParams.get("token");
  const openSupportOnLoad = urlParams.get("support") === "1";

  const VALID_SUPPORT_PLAN_CODES = new Set([
    "seeker",
    "magister",
    "sovereign",
    "philosophus",
    "theoricus"
  ]);
  const VALID_SUPPORT_MODES = new Set([
    "monthly_recurring",
    "annual_recurring"
  ]);

  const requestedPlanCodeRaw = (urlParams.get("plan_code") || "").trim().toLowerCase();
  const requestedSupportModeRaw = (urlParams.get("support_mode") || "").trim().toLowerCase();

  const selectedSupportIntent = {
    planCode: VALID_SUPPORT_PLAN_CODES.has(requestedPlanCodeRaw) ? requestedPlanCodeRaw : null,
    supportMode: VALID_SUPPORT_MODES.has(requestedSupportModeRaw) ? requestedSupportModeRaw : null
  };

  let supportOpenedFromQuery = false;
  // Documentation: Identity States and Unified /ask Contract
  //
  // The system supports three seeker states:
  // 1. Unregistered (anonymous): No seeker_id, visitor_id from localStorage
  // 2. Registered (soft identity): seeker_id from localStorage, visitor_id present
  // 3. Authenticated (future): Not yet implemented
  //
  // All /ask requests use the same unified JSON contract:
  // {
  //   "question": "string",
  //   "deity": "Hathor | Moses",
  //   "seeker_id": "UUID | null",
  //   "visitor_id": "UUID | null"
  // }
  // This ensures HTTP 200 for both anonymous and registered users, no 422 errors.
  const oracleForm = document.getElementById("oracleForm");
  const scrollForm = document.getElementById("scrollForm");
  const oracleAnswer = document.getElementById("oracleAnswer");
  const seekerInput = document.getElementById("seekerInput");
  const voiceSelect = document.getElementById("voiceSelect");
  const speakButton = document.getElementById("speakButton");
  const askButton = document.getElementById("askButton");
  const scrollCount = document.getElementById("scrollCount");
  const scrollInput = document.getElementById("scroll");
  const oracleHelper = document.getElementById("oracleHelper");
  const voiceStatusPanel = document.getElementById("voiceStatusPanel");
  const voiceStatusTitle = document.getElementById("voiceStatusTitle");
  const voiceStatusMessage = document.getElementById("voiceStatusMessage");
  const installNudge = document.getElementById("installNudge");
  const installNudgeMessage = document.getElementById("installNudgeMessage");
  const installNudgeHelpBtn = document.getElementById("installNudgeHelpBtn");
  const installNudgeDismissBtn = document.getElementById("installNudgeDismissBtn");

  const feedbackModal = document.getElementById("feedbackModal");
  const feedbackTitle = document.getElementById("feedbackTitle");
  const feedbackBody = document.getElementById("feedbackBody");
  const feedbackOkBtn = document.getElementById("feedbackOkBtn");

  let feedbackModalAction = "ok";

  const ANON_STORAGE_KEY = "godinc_anon_id";
  const ORACLE_VOICE_STORAGE_KEY = "godinc_oracle_voice";
  const NATIVE_ENTRY_MODE_STORAGE_KEY = "godinc_native_entry_mode";
  const templeUrlParams = new URLSearchParams(window.location.search);
  const nativeEntryMode = (templeUrlParams.get("entry") || "").trim().toLowerCase();
  const nativeVoiceParam = (templeUrlParams.get("voice") || "").trim().toLowerCase();
  const isNativeIOSLaunch = (templeUrlParams.get("native") || "").trim().toLowerCase() === "ios";
  const INSTALL_NUDGE_STORAGE_KEY = "godinc_install_nudge_dismissed";

  function generateAnonymousId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function(c) {
      const r = Math.random() * 16 | 0;
      const v = c === "x" ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }

  function getOrCreateVisitorId() {
    let existing = localStorage.getItem(ANON_STORAGE_KEY);
    if (!existing) {
      existing = generateAnonymousId();
      localStorage.setItem(ANON_STORAGE_KEY, existing);
    }
    return existing;
  }

  function setVisitorId(value) {
    if (!value) return;
    visitorId = value;
    localStorage.setItem(ANON_STORAGE_KEY, value);
  }

  function identityFetch(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (visitorId) {
      headers.set("X-Anonymous-User-Id", visitorId);
    }

    return fetch(url, {
      credentials: "same-origin",
      ...options,
      headers
    });
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function looksLikeHtmlResponse(value) {
    if (typeof value !== "string") return false;
    const sample = value.trim().slice(0, 200).toLowerCase();
    return sample.startsWith("<!doctype html") || sample.startsWith("<html");
  }

  function makeUploadStatusUnavailablePayload(
    message = "Final upload status could not be loaded. Refresh your Library shortly.",
    title = "Upload status unavailable"
  ) {
    return {
      upload_state: "status_unavailable",
      library_state: "unknown",
      terminal: true,
      seeker_title: title,
      seeker_message: message
    };
  }

  function isCanonicalUploadFeedback(data) {
    return Boolean(
      data &&
      typeof data === "object" &&
      typeof data.upload_state === "string" &&
      typeof data.seeker_title === "string" &&
      typeof data.seeker_message === "string"
    );
  }

  function buildUploadNotice(title, message, data = {}, options = {}) {
    const nudges = Array.isArray(data?.continuity_nudges) ? data.continuity_nudges : [];

    return {
      message,
      nudges,
      title,
      uploadState: data?.upload_state || options.uploadState || "status_unavailable",
      libraryState: data?.library_state || options.libraryState || "unknown",
      terminal: Boolean(data?.terminal ?? options.terminal ?? true),
      accepted: Boolean(data?.accepted ?? options.accepted ?? false),
      rejected: Boolean(data?.rejected ?? options.rejected ?? false),
      canonical: Boolean(options.canonical)
    };
  }

  function noticeFromUploadResponse(response, data, options = {}) {
    const context = options.context || "upload";
    const status = Number(response?.status || 0);
    const uploadState = String(data?.upload_state || "").toLowerCase();
    const adminStatus = String(data?.admin_status || "").toLowerCase();

    if (context === "upload") {
      if (
        uploadState === "rejected_cap" ||
        adminStatus === "rejected_anonymous_cap" ||
        data?.claim_required === true ||
        status === 403
      ) {
        return buildUploadNotice(
          "Create account",
          "Create an account to keep uploading and preserve your Library.",
          data,
          {
            uploadState: "rejected_cap",
            libraryState: "none",
            terminal: true,
            rejected: true
          }
        );
      }

      if (
        uploadState === "rejected_cooldown" ||
        adminStatus === "rejected_cooldown" ||
        status === 429
      ) {
        return buildUploadNotice(
          "Please wait",
          "Please wait a few seconds before uploading another scroll.",
          data,
          {
            uploadState: "rejected_cooldown",
            libraryState: "none",
            terminal: true,
            rejected: true
          }
        );
      }

      if (
        uploadState === "needs_ocr" ||
        adminStatus === "needs_ocr" ||
        status === 422
      ) {
        return buildUploadNotice(
          "Needs OCR",
          "Saved to Library. Needs OCR.",
          data,
          {
            uploadState: "needs_ocr",
            libraryState: "needs_ocr",
            terminal: true,
            accepted: true
          }
        );
      }

      if (
        uploadState === "rejected_invalid_file" ||
        adminStatus === "unreadable" ||
        [400, 415].includes(status)
      ) {
        return buildUploadNotice(
          "File could not be read",
          "This file could not be read. Try a text PDF, TXT, DOCX, MD, or RTF.",
          data,
          {
            uploadState: "rejected_invalid_file",
            libraryState: "none",
            terminal: true,
            rejected: true
          }
        );
      }

      if (
        uploadState === "storage_failed" ||
        adminStatus === "storage_save_failed" ||
        adminStatus === "storage_materialize_failed" ||
        status >= 500
      ) {
        return buildUploadNotice(
          "Upload failed",
          "Upload could not be saved. Please try again.",
          data,
          {
            uploadState: "storage_failed",
            libraryState: "not_created",
            terminal: true,
            rejected: true
          }
        );
      }
    }

    if (context === "poll" && !response?.ok) {
      if (status === 401 || status === 403) {
        return buildUploadNotice(
          "Status unavailable",
          "This browser could not view the final upload status. Refresh your Library or sign in to continue.",
          data,
          {
            uploadState: "status_unavailable",
            libraryState: "unknown",
            terminal: true
          }
        );
      }

      if (status === 404) {
        return buildUploadNotice(
          "Status unavailable",
          "Upload status could not be found. Refresh your Library shortly.",
          data,
          {
            uploadState: "not_found",
            libraryState: "unknown",
            terminal: true
          }
        );
      }

      return buildUploadNotice(
        "Upload status unavailable",
        "Final upload status could not be loaded. Refresh your Library shortly.",
        data,
        {
          uploadState: "status_unavailable",
          libraryState: "unknown",
          terminal: true
        }
      );
    }

    if (isCanonicalUploadFeedback(data)) {
      return buildUploadNotice(
        data.seeker_title,
        data.seeker_message,
        data,
        { canonical: true }
      );
    }

    if (context === "upload") {
      return buildUploadNotice(
        "Upload response unavailable",
        "The upload response could not be read. Please try again.",
        data,
        {
          uploadState: "status_unavailable",
          libraryState: "unknown",
          terminal: true
        }
      );
    }

    return buildUploadNotice(
      "Upload status unavailable",
      "Final upload status could not be loaded. Refresh your Library shortly.",
      data,
      {
        uploadState: "status_unavailable",
        libraryState: "unknown",
        terminal: true
      }
    );
  }

  function normalizeUploadFeedback(response, data, options = {}) {
    return noticeFromUploadResponse(response, data, options);
  }

  function showFeedbackModal(message, nudges = [], title = "Temple Notice", options = {}) {
    if (!feedbackModal || !feedbackBody || !feedbackTitle) return;

    const lines = [];
    if (message) {
      lines.push("<div>" + escapeHtml(message) + "</div>");
    }

    (nudges || []).forEach((line) => {
      lines.push("<div>" + escapeHtml(line) + "</div>");
    });

    const showCreateAccount = Boolean(options.showCreateAccount);
    feedbackModalAction = showCreateAccount ? "create_account" : "ok";

    if (feedbackOkBtn) {
      feedbackOkBtn.textContent = showCreateAccount ? "Create account" : "OK";
    }

    feedbackTitle.textContent = title;
    feedbackBody.innerHTML = lines.join("");
    openModal(feedbackModal);
  }

  // Phase 3.1: Anonymous continuity and seeker identity
  let visitorId = getOrCreateVisitorId();
  let seekerId = localStorage.getItem("seeker_id") || null;

  // Safe response parsing helper
  async function safeReadJson(response) {
    const text = await response.text();
    const contentType = response.headers.get("content-type") || "";

    if (contentType.includes("application/json")) {
      try {
        return JSON.parse(text);
      } catch (e) {
        return { error: "The Temple returned malformed JSON. Please refresh and try again." };
      }
    }

    if (looksLikeHtmlResponse(text)) {
      if (response.status === 429) {
        return {
          oracle_message: "The Oracle grows quiet. To continue the dialogue, please log in or support the Temple."
        };
      }

      if (response.status >= 500) {
        return {
          error: "The Temple returned a server page instead of an Oracle response. Please refresh and try again."
        };
      }

      return {
        error: "The Temple returned a page instead of an Oracle response. Please refresh and try again."
      };
    }

    return { error: text || "Oracle request failed" };
  }

  async function safeReadUploadJson(response) {
    const text = await response.text();
    const contentType = response.headers.get("content-type") || "";

    if (contentType.includes("application/json")) {
      try {
        return JSON.parse(text);
      } catch (e) {
        return makeUploadStatusUnavailablePayload("The upload response could not be read. Please try again.", "Upload response unavailable");
      }
    }

    if (looksLikeHtmlResponse(text)) {
      return makeUploadStatusUnavailablePayload("Final upload status could not be loaded. Refresh your Library shortly.", "Upload status unavailable");
    }

    return makeUploadStatusUnavailablePayload("Final upload status could not be loaded. Refresh your Library shortly.", "Upload status unavailable");
  }

  function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function uploadStatusTitle(data) {
    return isCanonicalUploadFeedback(data) ? data.seeker_title : "Status unavailable";
  }

  async function refreshScrollCount() {
    if (!scrollCount) return;

    const countResponse = await fetch("/scrolls");
    const countData = await safeReadJson(countResponse);

    if (countResponse.ok && typeof countData.count !== "undefined") {
      scrollCount.textContent = countData.count;
    }
  }

  async function pollQueuedUploadStatus(jobId, options = {}) {
    if (!jobId) return null;

    // Some PDF ingestion jobs take longer than the first "received" modal.
    // Keep polling long enough for real-world duplicate/OCR/ready outcomes.
    const maxAttempts = 90;
    const delayMs = 1000;

    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      await delay(delayMs);

      try {
        const statusResponse = await identityFetch("/ingestion/jobs/" + encodeURIComponent(jobId));
        const statusData = await safeReadUploadJson(statusResponse);

        if (!statusResponse.ok) {
          const normalized = normalizeUploadFeedback(statusResponse, statusData, { context: "poll" });
          showFeedbackModal(
            normalized.message,
            normalized.nudges,
            normalized.title,
            { showCreateAccount: Boolean(options.showCreateAccount) }
          );
          return statusData;
        }

        if (statusData?.terminal === true) {
          const normalized = normalizeUploadFeedback(statusResponse, statusData, { context: "poll" });

          showFeedbackModal(
            normalized.message,
            normalized.nudges,
            normalized.title,
            { showCreateAccount: Boolean(options.showCreateAccount) }
          );

          await refreshScrollCount();
          return statusData;
        }
      } catch (err) {
        if (window.console && typeof window.console.warn === "function") {
          console.warn("Upload status polling failed", err);
        }
      }
    }

    showFeedbackModal(
      "Still reading. Check your Library shortly.",
      [],
      "Upload status",
      { showCreateAccount: Boolean(options.showCreateAccount) }
    );

    return null;
  }

  // Unified /ask submission function
  async function submitOracleQuestion(questionText, selectedDeity) {
    const payload = {
      question: questionText,
      deity: selectedDeity,
      anonymous_user_id: visitorId
    };
    if (seekerId) {
      payload.seeker_id = seekerId;
    }

    const response = await identityFetch("/ask", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });

    const data = await safeReadJson(response);

    if (!response.ok) {
      throw new Error(
        data.oracle_message ||
        data.error ||
        "Oracle request failed"
      );
    }

    return data;
  }

  // Fetch scroll count on load
  fetch("/scrolls")
    .then((res) => res.json())
    .then((data) => {
      scrollCount.textContent = data.count;
    });

  // Restore the last Oracle voice used on this browser, with native iOS launch taking priority.
  const validOracleVoices = new Set(Array.from(voiceSelect.options).map((option) => option.value));
  const savedOracleVoice = localStorage.getItem(ORACLE_VOICE_STORAGE_KEY);
  const nativeVoiceMap = {
    hathor: "Hathor",
    moses: "Moses"
  };
  const requestedNativeVoice = nativeVoiceMap[nativeVoiceParam] || "";

  if (requestedNativeVoice && validOracleVoices.has(requestedNativeVoice)) {
    voiceSelect.value = requestedNativeVoice;
    localStorage.setItem(ORACLE_VOICE_STORAGE_KEY, requestedNativeVoice);
  } else if (savedOracleVoice && validOracleVoices.has(savedOracleVoice)) {
    voiceSelect.value = savedOracleVoice;
  }

  if (nativeEntryMode === "voice" || nativeEntryMode === "text") {
    localStorage.setItem(NATIVE_ENTRY_MODE_STORAGE_KEY, nativeEntryMode);
  }

  // Oracle selection helper text
  voiceSelect.addEventListener("change", function () {
    const selected = voiceSelect.value;
    localStorage.setItem(ORACLE_VOICE_STORAGE_KEY, selected);
    renderOracleHelper(null);
  });
  // Trigger initial helper text
  voiceSelect.dispatchEvent(new Event("change"));

  
// Phase 10.11.2: One clean scroll file picker + Enter upload support.
const scrollFileStatus = document.getElementById("scrollFileStatus");

function getScrollUploadButton() {
  return scrollForm ? scrollForm.querySelector(".upload-submit, button[type='submit'], button") : null;
}

function getSelectedScrollFileName() {
  if (!scrollInput || !scrollInput.files || !scrollInput.files.length) return "";
  return scrollInput.files[0].name || "scroll selected";
}

function requestScrollFormSubmit() {
  if (!scrollForm) return;
  if (typeof scrollForm.requestSubmit === "function") {
    scrollForm.requestSubmit();
  } else {
    scrollForm.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
  }
}

function updateScrollUploadUi() {
  if (!scrollForm || !scrollInput) return;

  const fileName = getSelectedScrollFileName();
  const submitBtn = getScrollUploadButton();

  scrollForm.classList.toggle("has-selected-file", Boolean(fileName));

  if (scrollFileStatus) {
    scrollFileStatus.textContent = fileName ? "Chosen: " + fileName : "";
  }

  if (submitBtn && !submitBtn.disabled) {
    submitBtn.textContent = fileName ? "Upload Scroll" : "Choose File";
  }
}

if (scrollInput && scrollForm) {
  scrollInput.addEventListener("change", updateScrollUploadUi);

  const scrollUploadButton = getScrollUploadButton();
  if (scrollUploadButton) {
    scrollUploadButton.addEventListener("click", function (e) {
      if (!getSelectedScrollFileName()) {
        e.preventDefault();
        scrollInput.click();
      }
    });
  }

  scrollForm.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" || e.shiftKey) return;

    const target = e.target;
    const targetTag = target && target.tagName ? target.tagName.toLowerCase() : "";

    if (targetTag === "textarea") return;

    if (target && target.closest && target.closest(".upload-submit")) {
      return;
    }

    e.preventDefault();

    if (getSelectedScrollFileName()) {
      requestScrollFormSubmit();
    } else {
      scrollInput.click();
    }
  });

  updateScrollUploadUi();
}

// Upload scroll
  scrollForm.addEventListener("submit", async function (e) {
    e.preventDefault();

    const submitBtn = scrollForm.querySelector("button[type='submit'], button");
    const originalText = submitBtn ? submitBtn.textContent : "Upload";

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = "Uploading...";
    }

    try {
      const formData = new FormData(scrollForm);
      formData.append("anonymous_user_id", visitorId);
      if (seekerId) formData.append("seeker_id", seekerId);

      const response = await identityFetch("/upload_scroll", {
        method: "POST",
        body: formData,
      });

      const data = await safeReadUploadJson(response);
      const continuityNudges = Array.isArray(data?.continuity_nudges) ? data.continuity_nudges : [];
      const shouldOfferClaim = !currentIdentity?.authenticated && (Boolean(data?.claim_required) || continuityNudges.length > 0);

      if (!response.ok) {
        const normalized = normalizeUploadFeedback(response, data, { context: "upload" });
        const shouldClearFile =
          response.status === 403 ||
          response.status === 409 ||
          response.status >= 500 ||
          looksLikeHtmlResponse(data);

        if (shouldClearFile) {
          scrollInput.value = "";
        }

        showFeedbackModal(
          normalized.message,
          normalized.nudges,
          normalized.title,
          { showCreateAccount: shouldOfferClaim }
        );
        return;
      }

      const normalized = normalizeUploadFeedback(response, data, { context: "upload" });
      showFeedbackModal(
        normalized.message,
        normalized.nudges,
        normalized.title,
        { showCreateAccount: shouldOfferClaim }
      );
      scrollInput.value = "";

      if (data?.upload_state === "queued" && data?.job_id) {
        pollQueuedUploadStatus(data.job_id, { showCreateAccount: shouldOfferClaim });
      } else {
        await refreshScrollCount();
      }
    } catch (err) {
      showFeedbackModal(
        "Upload status could not be loaded. Refresh your Library shortly.",
        [],
        "Status unavailable"
      );
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
      }
    if (typeof updateScrollUploadUi === "function") { updateScrollUploadUi(); }
}
  });

  function renderOracleHelper(identity) {
    if (!oracleHelper) return;

    const lines = [];

    const authenticated = Boolean(identity && identity.authenticated);
    const remaining = identity?.usage?.questions_remaining;
    const unlimited = Boolean(identity?.usage?.is_unlimited_questions);

    if (!authenticated && typeof remaining === "number") {
      if (remaining <= 0) {
        lines.push("Anonymous path complete. Create an account or activate support to continue.");
      } else if (remaining <= 2) {
        lines.push("Anonymous path: " + remaining + " question" + (remaining === 1 ? "" : "s") + " remaining.");
      }
    } else if (authenticated && identity?.renewal_message && !unlimited) {
      lines.push(identity.renewal_message);
    }

    oracleHelper.textContent = lines.join(" ");
  }

  
// Phase 10.11: Enter submits the Oracle question; Shift+Enter keeps a new line.
if (seekerInput && oracleForm) {
  seekerInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();

      if (typeof oracleForm.requestSubmit === "function") {
        oracleForm.requestSubmit();
      } else {
        oracleForm.dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
      }
    }
  });
}

// Ask Oracle (text input)
  oracleForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    const question = seekerInput.value.trim();
    if (!question) return;
    const voice = voiceSelect.value;

    seekerInput.value = "";
    oracleAnswer.textContent = "🔮 Consulting the Oracle...";
    askButton.disabled = true;

    try {
      const data = await submitOracleQuestion(question, voice);
      if (data.answer) {
        oracleAnswer.textContent = data.answer;
        await updateIdentityDisplay();
        maybeShowInstallNudge("oracle_answer");
      } else if (data.error) {
        oracleAnswer.textContent = "⚠️ Error: " + data.error;
      } else {
        oracleAnswer.textContent = "⚠️ No response received.";
      }
    } catch (err) {
      const msg = err.message || "Oracle request failed";

      if (msg.includes("The Oracle grows quiet")) {
        oracleAnswer.textContent = "The Oracle grows quiet.";
        if (currentIdentity?.authenticated) {
          showFeedbackModal(
            msg,
            currentIdentity?.renewal_message ? [currentIdentity.renewal_message] : [],
            "Temple Notice"
          );
          renderSupportModal();
          openModal(supportModal);
          applySupportIntentSelection(true);
        } else {
          showFeedbackModal(
            msg,
            currentIdentity?.continuity_nudges || [],
            "Temple Notice",
            { showCreateAccount: true }
          );
        }
      } else {
        oracleAnswer.textContent = "⚠️ Error: " + msg;
      }

      await updateIdentityDisplay();
    } finally {
      askButton.disabled = false;
    }
  });

  async function submitOracleVoiceQuestion(questionText, selectedDeity) {
    const payload = {
      question: questionText,
      deity: selectedDeity,
      anonymous_user_id: visitorId
    };
    if (seekerId) {
      payload.seeker_id = seekerId;
    }

    const response = await identityFetch("/voice/ask", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });

    const data = await safeReadJson(response);

    if (!response.ok) {
      throw new Error(
        data.oracle_message ||
        data.error ||
        "Voice Oracle request failed"
      );
    }

    return data;
  }

  async function prepareOracleVoice(answerText, selectedDeity) {
    const response = await identityFetch("/voice/tts", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        answer: answerText,
        voice: selectedDeity
      })
    });

    const data = await safeReadJson(response);

    if (!response.ok) {
      throw new Error(data.error || "Oracle voice could not be prepared.");
    }

    return data;
  }

  // Conversational voice input and TTS output
  let voiceRecorder = null;
  let voiceStream = null;
  let voiceChunks = [];
  let voiceIsRecording = false;
  let voiceStopReason = "";
  let voiceAudioContext = null;
  let voiceAnalyser = null;
  let voiceMonitorFrame = null;
  let voiceSpeechDetected = false;
  let voiceSilenceStartedAt = null;
  let voiceRecordingStartedAt = 0;
  let voiceMaxRecordingTimer = null;

  const VOICE_NO_SPEECH_TIMEOUT_MS = 5000;
  const VOICE_SILENCE_AUTO_STOP_MS = 1800;
  const VOICE_MIN_RECORDING_MS = 900;
  const VOICE_MAX_RECORDING_MS = 30000;
  const VOICE_VOLUME_THRESHOLD = 0.025;
  let oracleAudio = null;
  let replayVoiceButton = null;

  function ensureOracleAudio() {
    if (!oracleAudio) {
      oracleAudio = document.createElement("audio");
      oracleAudio.preload = "auto";
      oracleAudio.style.display = "none";
      document.body.appendChild(oracleAudio);
    }
    return oracleAudio;
  }

  function ensureReplayVoiceButton() {
    if (!replayVoiceButton) {
      replayVoiceButton = document.createElement("button");
      replayVoiceButton.type = "button";
      replayVoiceButton.textContent = "▶ Play Oracle Voice";
      replayVoiceButton.className = "oracle-replay-button";
      replayVoiceButton.style.display = "none";
      replayVoiceButton.addEventListener("click", async function () {
        if (!oracleAudio || !oracleAudio.src) return;
        try {
          replayVoiceButton.disabled = true;
          await oracleAudio.play();
        } catch (err) {
          oracleAnswer.textContent += "\n\n⚠️ Could not play the Oracle voice. Please try again.";
        } finally {
          replayVoiceButton.disabled = false;
        }
      });
      oracleAnswer.insertAdjacentElement("afterend", replayVoiceButton);
    }
    return replayVoiceButton;
  }


  function setVoiceStatus(title, message, state = "neutral", shouldShow = true) {
    if (!voiceStatusPanel || !voiceStatusTitle || !voiceStatusMessage) return;

    voiceStatusPanel.hidden = !shouldShow;
    voiceStatusPanel.classList.remove(
      "is-listening",
      "is-working",
      "is-speaking",
      "is-ready",
      "is-error",
      "is-notice"
    );

    if (state) {
      voiceStatusPanel.classList.add("is-" + state);
    }

    voiceStatusTitle.textContent = title || "Voice mode";
    voiceStatusMessage.textContent = message || "";
  }

  function getMicrophoneRecoveryMessage(err) {
    const name = err && err.name ? err.name : "";

    if (name === "NotAllowedError" || name === "SecurityError") {
      return "Microphone access is blocked. In Safari, allow microphone access for this site, or type your question below.";
    }

    if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      return "No microphone was found. You can still type your question below.";
    }

    if (name === "NotReadableError" || name === "TrackStartError") {
      return "The microphone could not be opened. Another app may be using it. Close other audio apps and try again.";
    }

    return err && err.message
      ? err.message
      : "The microphone could not be opened. You can type your question below or try again.";
  }

  function getSupportedVoiceMimeType() {
    if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") {
      return "";
    }

    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/mp4",
      "audio/aac"
    ];

    return candidates.find((type) => MediaRecorder.isTypeSupported(type)) || "";
  }

  function getVoiceFilename(blob) {
    const type = (blob && blob.type ? blob.type : "").toLowerCase();

    if (type.includes("mp4")) return "voice_input.mp4";
    if (type.includes("aac")) return "voice_input.aac";
    if (type.includes("ogg")) return "voice_input.ogg";
    if (type.includes("wav")) return "voice_input.wav";

    return "voice_input.webm";
  }

  function isStandaloneDisplay() {
    return (
      window.matchMedia("(display-mode: standalone)").matches ||
      window.navigator.standalone === true
    );
  }

  function isLikelyMobileSafari() {
    const ua = window.navigator.userAgent || "";
    const isIOS = /iPhone|iPad|iPod/i.test(ua);
    const isSafari = /Safari/i.test(ua) && !/CriOS|FxiOS|EdgiOS/i.test(ua);
    return isIOS && isSafari;
  }

  function shouldShowInstallNudge() {
    if (!installNudge) return false;
    if (isStandaloneDisplay()) return false;
    if (!isLikelyMobileSafari() && window.innerWidth > 820) return false;

    return !localStorage.getItem(INSTALL_NUDGE_STORAGE_KEY);
  }

  function maybeShowInstallNudge(reason = "engagement") {
    if (!shouldShowInstallNudge()) return;

    installNudge.hidden = false;

    if (installNudgeMessage) {
      installNudgeMessage.textContent = reason === "voice_complete"
        ? "For a quieter, app-like voice experience, tap Share, then Add to Home Screen."
        : "For a quieter, app-like experience, tap Share, then Add to Home Screen.";
    }
  }

  function dismissInstallNudge() {
    localStorage.setItem(INSTALL_NUDGE_STORAGE_KEY, new Date().toISOString());

    if (installNudge) {
      installNudge.hidden = true;
    }
  }

  if (installNudgeHelpBtn && installNudgeMessage) {
    installNudgeHelpBtn.addEventListener("click", function () {
      installNudgeMessage.textContent = "On iPhone Safari: tap the Share icon, scroll, choose Add to Home Screen, then tap Add.";
    });
  }

  if (installNudgeDismissBtn) {
    installNudgeDismissBtn.addEventListener("click", dismissInstallNudge);
  }


  function cleanupVoiceAutoStop() {
    if (voiceMonitorFrame) {
      window.cancelAnimationFrame(voiceMonitorFrame);
      voiceMonitorFrame = null;
    }

    if (voiceMaxRecordingTimer) {
      window.clearTimeout(voiceMaxRecordingTimer);
      voiceMaxRecordingTimer = null;
    }

    if (voiceAudioContext) {
      try {
        voiceAudioContext.close();
      } catch (err) {
        // Ignore browser cleanup failures.
      }
      voiceAudioContext = null;
    }

    voiceAnalyser = null;
    voiceSpeechDetected = false;
    voiceSilenceStartedAt = null;
    voiceRecordingStartedAt = 0;
  }

  function stopCurrentVoiceRecording(reason) {
    voiceStopReason = reason || "manual";

    if (voiceRecorder && voiceRecorder.state === "recording") {
      voiceRecorder.stop();
      return true;
    }

    return false;
  }

  function beginVoiceAutoStopMonitor(stream) {
    cleanupVoiceAutoStop();

    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass || !stream) {
      return;
    }

    try {
      voiceAudioContext = new AudioContextClass();
      if (voiceAudioContext.state === "suspended" && voiceAudioContext.resume) {
        voiceAudioContext.resume().catch(function () {});
      }

      const source = voiceAudioContext.createMediaStreamSource(stream);
      voiceAnalyser = voiceAudioContext.createAnalyser();
      voiceAnalyser.fftSize = 2048;
      source.connect(voiceAnalyser);
    } catch (err) {
      cleanupVoiceAutoStop();
      return;
    }

    const samples = new Uint8Array(voiceAnalyser.fftSize);
    voiceRecordingStartedAt = Date.now();
    voiceSpeechDetected = false;
    voiceSilenceStartedAt = null;

    voiceMaxRecordingTimer = window.setTimeout(function () {
      if (voiceIsRecording && voiceRecorder && voiceRecorder.state === "recording") {
        setVoiceStatus(
          "Processing your question",
          "Recording reached the safety limit. The Temple is preparing what it heard.",
          "working"
        );
        stopCurrentVoiceRecording("max_duration");
      }
    }, VOICE_MAX_RECORDING_MS);

    function tick() {
      if (!voiceIsRecording || !voiceRecorder || voiceRecorder.state !== "recording" || !voiceAnalyser) {
        return;
      }

      voiceAnalyser.getByteTimeDomainData(samples);

      let sumSquares = 0;
      for (let i = 0; i < samples.length; i += 1) {
        const value = (samples[i] - 128) / 128;
        sumSquares += value * value;
      }

      const volume = Math.sqrt(sumSquares / samples.length);
      const now = Date.now();
      const elapsed = now - voiceRecordingStartedAt;

      if (volume >= VOICE_VOLUME_THRESHOLD) {
        voiceSpeechDetected = true;
        voiceSilenceStartedAt = null;
      } else if (voiceSpeechDetected) {
        if (!voiceSilenceStartedAt) {
          voiceSilenceStartedAt = now;
        }

        if (
          elapsed >= VOICE_MIN_RECORDING_MS &&
          now - voiceSilenceStartedAt >= VOICE_SILENCE_AUTO_STOP_MS
        ) {
          setVoiceStatus(
            "Processing your question",
            "Silence detected. The Temple is preparing your spoken question.",
            "working"
          );
          stopCurrentVoiceRecording("silence");
          return;
        }
      } else if (elapsed >= VOICE_NO_SPEECH_TIMEOUT_MS) {
        setVoiceStatus(
          "No clear speech detected",
          "The microphone did not detect a spoken question. Tap Speak and try again, or type your question below.",
          "notice"
        );
        stopCurrentVoiceRecording("no_speech");
        return;
      }

      voiceMonitorFrame = window.requestAnimationFrame(tick);
    }

    voiceMonitorFrame = window.requestAnimationFrame(tick);
  }


  function stopVoiceTracks() {
    cleanupVoiceAutoStop();

    if (voiceStream) {
      voiceStream.getTracks().forEach((track) => track.stop());
      voiceStream = null;
    }
  }

  function resetVoiceButton() {
    voiceIsRecording = false;
    speakButton.disabled = false;
    speakButton.textContent = "🎤 Speak";

    const audioIsSpeaking = oracleAudio && !oracleAudio.paused && !oracleAudio.ended;
    if (voiceStatusPanel && !voiceStatusPanel.hidden && !audioIsSpeaking) {
      setVoiceStatus(
        "Voice ready",
        "Tap Speak when you are ready to ask aloud.",
        "ready"
      );
    }
  }

  async function playOracleAudio(audioUrl) {
    if (!audioUrl) return;

    const audio = ensureOracleAudio();
    const replayButton = ensureReplayVoiceButton();

    function setReplayReady() {
      replayButton.textContent = "▶ Play Oracle Voice";
      replayButton.disabled = false;
    }

    audio.src = audioUrl;
    replayButton.style.display = "inline-block";
    setReplayReady();

    audio.onplay = function () {
      replayButton.textContent = "🔊 Speaking...";
      replayButton.disabled = true;
      setVoiceStatus("Oracle speaking", "Listen. When the voice ends, you may ask again.", "speaking");
    };

    audio.onended = function () {
      setReplayReady();
      setVoiceStatus("Voice complete", "Tap Speak to continue the conversation.", "ready");
      maybeShowInstallNudge("voice_complete");
    };

    audio.onpause = function () {
      if (audio.ended) {
        setReplayReady();
      }
    };

    try {
      await audio.play();
    } catch (err) {
      setReplayReady();
      setVoiceStatus(
        "Tap to hear the Oracle",
        "Safari may need one more tap before it can play the Oracle voice. Use Play Oracle Voice.",
        "notice"
      );
    }
  }


  function browserVoiceIsAvailable() {
    return typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";
  }

  function findPreferredBrowserVoice(preferredNames) {
    if (!browserVoiceIsAvailable()) return null;

    const voices = window.speechSynthesis.getVoices() || [];
    if (!voices.length) return null;

    const englishVoices = voices.filter((voice) => /^en[-_]/i.test(voice.lang || ""));
    const searchVoices = englishVoices.length ? englishVoices : voices;

    for (const preferredName of preferredNames) {
      const exact = searchVoices.find((voice) =>
        (voice.name || "").toLowerCase() === preferredName.toLowerCase()
      );
      if (exact) return exact;
    }

    for (const preferredName of preferredNames) {
      const partial = searchVoices.find((voice) =>
        (voice.name || "").toLowerCase().includes(preferredName.toLowerCase())
      );
      if (partial) return partial;
    }

    return null;
  }

  function pickBrowserVoice(selectedDeity) {
    const deity = (selectedDeity || "").trim().toLowerCase();

    if (deity === "moses") {
      return findPreferredBrowserVoice([
        "Daniel",
        "Alex",
        "Google UK English Male",
        "Google US English Male",
        "Microsoft David",
        "Microsoft Mark",
        "Microsoft George",
        "Fred",
        "Tom"
      ]);
    }

    return findPreferredBrowserVoice([
      "Victoria",
      "Samantha",
      "Ava",
      "Google UK English Female",
      "Google US English Female",
      "Microsoft Zira",
      "Microsoft Aria",
      "Microsoft Jenny",
      "Microsoft Hazel",
      "Microsoft Susan"
    ]);
  }

  function getBrowserSpeechSettings(selectedDeity) {
    const deity = (selectedDeity || "").trim().toLowerCase();

    if (deity === "moses") {
      return {
        rate: 1.0,
        pitch: 1.0,
        volume: 1.0
      };
    }

    return {
      rate: 0.99,
      pitch: 1.02,
      volume: 1.0
    };
  }

  function chunkBrowserSpeechText(answerText) {
    const normalized = String(answerText || "")
      .replace(/\s+/g, " ")
      .trim();

    if (!normalized) return [];

    const maxChunkLength = 260;
    const softChunkLength = 210;
    const sentencePieces = normalized.match(/[^.!?;:]+[.!?;:]?|\S+/g) || [normalized];
    const chunks = [];
    let current = "";

    sentencePieces.forEach((piece) => {
      const trimmed = piece.trim();
      if (!trimmed) return;

      const candidate = current ? current + " " + trimmed : trimmed;

      if (candidate.length > maxChunkLength && current.length >= softChunkLength) {
        chunks.push(current);
        current = trimmed;
      } else if (candidate.length > maxChunkLength && current) {
        chunks.push(current);
        current = trimmed;
      } else {
        current = candidate;
      }

      while (current.length > maxChunkLength * 1.5) {
        const splitAt = current.lastIndexOf(",", maxChunkLength);
        const safeSplit = splitAt > 80 ? splitAt + 1 : maxChunkLength;
        chunks.push(current.slice(0, safeSplit).trim());
        current = current.slice(safeSplit).trim();
      }
    });

    if (current) {
      chunks.push(current);
    }

    return chunks.filter(Boolean);
  }

  function speakAnswerWithBrowserVoice(answerText, selectedDeity) {
    return new Promise((resolve, reject) => {
      if (!browserVoiceIsAvailable()) {
        reject(new Error("browser_voice_unavailable"));
        return;
      }

      const chunks = chunkBrowserSpeechText(answerText);
      if (!chunks.length) {
        reject(new Error("browser_voice_empty_answer"));
        return;
      }

      const chosenVoice = pickBrowserVoice(selectedDeity);
      const settings = getBrowserSpeechSettings(selectedDeity);

      window.godIncLastBrowserVoice = {
        deity: selectedDeity || "",
        voiceName: chosenVoice ? chosenVoice.name : "browser-default",
        voiceLang: chosenVoice ? chosenVoice.lang : "",
        rate: settings.rate,
        pitch: settings.pitch,
        chunkCount: chunks.length
      };

      let currentChunkIndex = 0;
      let hasStarted = false;

      function speakNextChunk() {
        if (currentChunkIndex >= chunks.length) {
          setVoiceStatus(
            "Voice complete",
            "Tap Speak to continue the conversation.",
            "ready"
          );
          maybeShowInstallNudge("voice_complete");
          resolve();
          return;
        }

        const utterance = new SpeechSynthesisUtterance(chunks[currentChunkIndex]);

        if (chosenVoice) {
          utterance.voice = chosenVoice;
          utterance.lang = chosenVoice.lang || "en-US";
        } else {
          utterance.lang = "en-US";
        }

        utterance.rate = settings.rate;
        utterance.pitch = settings.pitch;
        utterance.volume = settings.volume;

        utterance.onstart = function () {
          if (!hasStarted) {
            hasStarted = true;
            setVoiceStatus(
              "Browser voice speaking",
              "The written answer is ready. Your browser is reading it aloud.",
              "speaking"
            );
          }
        };

        utterance.onend = function () {
          currentChunkIndex += 1;
          window.setTimeout(speakNextChunk, 40);
        };

        utterance.onerror = function (event) {
          reject(new Error(event.error || "browser_voice_error"));
        };

        window.speechSynthesis.speak(utterance);
      }

      try {
        window.speechSynthesis.cancel();
        speakNextChunk();
      } catch (err) {
        reject(err);
      }
    });
  }

  function showBrowserVoiceUnavailable() {
    setVoiceStatus(
      "Voice playback unavailable",
      "The written Oracle answer is ready. Your browser may not support voice playback, or voice output may be blocked by browser settings. Try another browser, check sound permissions, or continue by reading the answer on screen. Recurring live realtime voice begins at Sovereign.",
      "notice"
    );
  }


  async function submitVoiceRecording(blob) {
    const selectedVoice = voiceSelect.value;
    const formData = new FormData();
    formData.append("file", blob, getVoiceFilename(blob));
    formData.append("voice", selectedVoice);

    setVoiceStatus("Transcribing your voice", "The Temple is preparing your spoken question.", "working");
    oracleAnswer.textContent = "🔄 Transcribing...";

    const transcribeResponse = await identityFetch("/voice/transcribe", {
      method: "POST",
      body: formData
    });

    const transcribeData = await safeReadJson(transcribeResponse);

    if (!transcribeResponse.ok) {
      throw new Error(
        transcribeData.error ||
        transcribeData.answer ||
        "Voice transcription failed"
      );
    }

    const spokenQuestion = transcribeData.transcript || transcribeData.question || "";

    if (!spokenQuestion) {
      throw new Error("No voice transcript was returned.");
    }

    seekerInput.value = spokenQuestion;
    setVoiceStatus("Consulting the Oracle", "Your question has been heard. The Oracle is answering.", "working");
    oracleAnswer.textContent = "You said: " + spokenQuestion + "\n\n🔮 Consulting the Oracle...";

    const answerData = await submitOracleVoiceQuestion(spokenQuestion, selectedVoice);

    if (answerData.answer) {
      oracleAnswer.textContent = "You said: " + spokenQuestion + "\n\n" + answerData.answer;
      setVoiceStatus("Oracle answered", "The written answer is ready. Preparing the spoken voice.", "working");
      await updateIdentityDisplay();
    } else if (answerData.error) {
      oracleAnswer.textContent = "⚠️ Error: " + answerData.error;
      return;
    } else {
      oracleAnswer.textContent = "⚠️ No response received.";
      return;
    }

    const replayButton = ensureReplayVoiceButton();
    replayButton.style.display = "none";
    replayButton.disabled = true;

    setVoiceStatus(
      "Browser voice preparing",
      "The written answer is ready. Your browser will read it aloud where available.",
      "working"
    );

    try {
      await speakAnswerWithBrowserVoice(answerData.answer, selectedVoice);
    } catch (err) {
      showBrowserVoiceUnavailable();
    }
  }


  function shouldUseNativeIOSVoicePath() {
    return Boolean(isNativeIOSLaunch || isNativeIOSApp());
  }


  function notifyNativeIOSAuthChanged() {
    const handler = window.webkit?.messageHandlers?.templeNativeNav;

    if (!handler) {
      return;
    }

    handler.postMessage({
      destination: "authChanged"
    });
  }


  function openNativeIOSHomeForVoice() {
    const handler = window.webkit?.messageHandlers?.templeNativeNav;
    if (handler) {
      handler.postMessage({
        destination: "home",
        reason: "voice"
      });
      return true;
    }

    window.location.href = "/";
    return false;
  }


  function ensureNativeIOSHomeLinkStyles() {
    if (document.getElementById("native-ios-home-link-style")) {
      return;
    }

    const style = document.createElement("style");
    style.id = "native-ios-home-link-style";
    style.textContent = `
      .native-ios-home-link[data-native-ios-home-link="true"] {
        -webkit-appearance: none !important;
        appearance: none !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        width: auto !important;
        max-width: max-content !important;
        min-height: 0 !important;
        margin: 0.65rem auto 0 !important;
        padding: 0.35rem 0.75rem !important;
        border-radius: 999px !important;
        line-height: 1.2 !important;
        font-size: 0.88rem !important;
        white-space: nowrap !important;
        text-align: center !important;
      }
    `;
    document.head.appendChild(style);
  }


  function positionNativeIOSTextPageFlow() {
    if (!shouldUseNativeIOSVoicePath() || !askButton || !voiceStatusPanel) {
      return;
    }

    if (oracleForm && oracleForm.id) {
      askButton.setAttribute("form", oracleForm.id);
    }

    askButton.classList.add("native-ios-ask-button-inline");

    if (voiceStatusPanel.parentNode && askButton.nextElementSibling !== voiceStatusPanel) {
      voiceStatusPanel.parentNode.insertBefore(askButton, voiceStatusPanel);
    }

    if (!document.getElementById("native-ios-text-page-flow-style")) {
      const style = document.createElement("style");
      style.id = "native-ios-text-page-flow-style";
      style.textContent = `
        .native-ios-ask-button-inline {
          display: block !important;
          width: min(100%, 18rem) !important;
          margin: 0.85rem auto 0.95rem !important;
          text-align: center !important;
        }
      `;
      document.head.appendChild(style);
    }
  }

  function addNativeIOSHomeVoiceLink() {
    if (!shouldUseNativeIOSVoicePath() || !askButton) {
      return;
    }

    const existingLink = document.querySelector("[data-native-ios-home-link='true']");
    if (existingLink) {
      existingLink.remove();
    }

    ensureNativeIOSHomeLinkStyles();

    const homeLink = document.createElement("button");
    homeLink.type = "button";
    homeLink.className = "native-ios-home-link native-ios-return-voice-link";
    homeLink.dataset.nativeIosHomeLink = "true";
    homeLink.textContent = "Return to Voice";
    homeLink.addEventListener("click", function () {
      openNativeIOSHomeForVoice();
    });

    askButton.insertAdjacentElement("afterend", homeLink);
  }

  function applyNativeIOSWebVoiceSuppression() {
    if (!shouldUseNativeIOSVoicePath()) {
      return false;
    }

    if (speakButton) {
      speakButton.disabled = true;
      speakButton.hidden = true;
      speakButton.style.display = "none";
      speakButton.setAttribute("aria-hidden", "true");
      speakButton.setAttribute("data-native-ios-disabled", "true");
    }

    if (voiceStatusPanel) {
      voiceStatusPanel.hidden = true;
      voiceStatusPanel.style.display = "none";
    }

    positionNativeIOSTextPageFlow();
    addNativeIOSHomeVoiceLink();

    return true;
  }


  const TEMPLE_REALTIME_INPUT_SAMPLE_RATE = 24000;
  const TEMPLE_REALTIME_OUTPUT_SAMPLE_RATE = 24000;
  const TEMPLE_REALTIME_SPEECH_RMS_THRESHOLD = 0.014;
  const TEMPLE_REALTIME_SPEECH_START_FRAMES_REQUIRED = 3;
  const TEMPLE_REALTIME_POST_PLAYBACK_COOLDOWN_MS = 100;
  const TEMPLE_REALTIME_IDLE_AUTO_END_AFTER_RETURN_MS = 12000;
  const TEMPLE_REALTIME_PRE_ROLL_MS = 650;
  const TEMPLE_REALTIME_CLIENT_TURN_COMMIT_SILENCE_MS = 2400;
  const TEMPLE_REALTIME_TRAILING_AUDIO_MS = TEMPLE_REALTIME_CLIENT_TURN_COMMIT_SILENCE_MS;
  const TEMPLE_REALTIME_IDLE_TIMEOUT_MS = 90000;
  const TEMPLE_REALTIME_MAX_SESSION_MS = 300000;
  const TEMPLE_REALTIME_PLAYBACK_DRAIN_PADDING_MS = 150;
  const TEMPLE_REALTIME_PLAYBACK_DRAIN_MIN_MS = 250;
  const TEMPLE_REALTIME_PLAYBACK_DRAIN_MAX_MS = 60000;
  const TEMPLE_REALTIME_WEBSOCKET_CONNECT_TIMEOUT_MS = 10000;

  const templeRealtimeState = {
    socket: null,
    sessionData: null,
    active: false,
    starting: false,
    clickPending: false,
    ending: false,

    selectedDeity: "Hathor",
    selectedRealtimeVoice: "eve",

    inputStream: null,
    inputAudioContext: null,
    inputSource: null,
    inputProcessor: null,

    outputAudioContext: null,
    nextPlaybackTime: 0,
    playbackDrainTimer: null,
    idleTimer: null,
    maxSessionTimer: null,
    idleAutoEndTimer: null,

    sessionStartedAt: 0,
    lastActivityAt: 0,

    speechGateOpen: false,
    speechTurnIndex: 0,
    assistantTurnIndex: 0,
    assistantSpeaking: false,
    turnCommitPending: false,

    preRollChunks: [],
    preRollSamples: 0,
    trailingMsRemaining: 0,
    speechAboveThresholdFrames: 0,
    listeningCooldownUntil: 0,
    turnInputStartSamples: 0,
    responseOutputStartSamples: 0,

    inputSamplesSent: 0,
    inputBytesSent: 0,
    inputChunksSent: 0,
    outputSamplesReceived: 0,
    outputBytesReceived: 0,
    firstAudioDeltaAt: 0,

    currentInputTranscript: "",
    currentAssistantTranscript: "",
    currentClientInteractionId: "",
    interactionReportPending: false,
    interactionReportedIds: {}
  };

  function templeRealtimeGenerateInteractionId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }

    return "rt-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  }

  function templeRealtimeEnsureInteractionId() {
    if (!templeRealtimeState.currentClientInteractionId) {
      templeRealtimeState.currentClientInteractionId = templeRealtimeGenerateInteractionId();
    }

    return templeRealtimeState.currentClientInteractionId;
  }

  function templeRealtimeCompletedInteractionPayload(reason) {
    const sessionData = templeRealtimeState.sessionData || {};
    const inputTranscript = (templeRealtimeState.currentInputTranscript || "").trim();
    const assistantTranscript = (templeRealtimeState.currentAssistantTranscript || "").trim();
    const clientInteractionId = templeRealtimeEnsureInteractionId();

    return {
      client_interaction_id: clientInteractionId,
      client_session_id: sessionData.id || sessionData.session_id || "",
      provider_session_id: sessionData.provider_session_id || sessionData.id || sessionData.session_id || "",
      source: "temple",
      route: "temple_main_live_realtime",
      input_mode: "realtime_voice",
      provider: sessionData.provider || "xai",
      model: sessionData.model || "",
      transport: sessionData.transport || "websocket",
      deity: templeRealtimeState.selectedDeity,
      provider_voice: templeRealtimeState.selectedRealtimeVoice,
      realtime_voice: templeRealtimeState.selectedRealtimeVoice,
      speech_turn: templeRealtimeState.speechTurnIndex,
      assistant_turn: templeRealtimeState.assistantTurnIndex,
      input_transcript: inputTranscript,
      assistant_transcript: assistantTranscript,
      input_transcript_source: "provider_realtime",
      assistant_transcript_source: "provider_audio_transcript",
      turn_input_audio_seconds: Number(((templeRealtimeState.inputSamplesSent - templeRealtimeState.turnInputStartSamples) / TEMPLE_REALTIME_INPUT_SAMPLE_RATE).toFixed(3)),
      output_audio_seconds: Number(((templeRealtimeState.outputSamplesReceived - templeRealtimeState.responseOutputStartSamples) / TEMPLE_REALTIME_OUTPUT_SAMPLE_RATE).toFixed(3)),
      first_audio_delta_ms: templeRealtimeState.firstAudioDeltaAt || null,
      preview_mode: false,
      completion_reason: reason || "response.done"
    };
  }

  async function templeRealtimeReportInteraction(reason) {
    const inputTranscript = (templeRealtimeState.currentInputTranscript || "").trim();
    const assistantTranscript = (templeRealtimeState.currentAssistantTranscript || "").trim();
    const clientInteractionId = templeRealtimeEnsureInteractionId();

    if (!inputTranscript || !assistantTranscript) {
      templeRealtimeLog("TEMPLE_REALTIME_INTERACTION_LOG_SKIPPED", {
        reason: "missing_transcript",
        has_input_transcript: Boolean(inputTranscript),
        has_assistant_transcript: Boolean(assistantTranscript),
        client_interaction_id: clientInteractionId
      });
      return;
    }

    if (templeRealtimeState.interactionReportPending || templeRealtimeState.interactionReportedIds[clientInteractionId]) {
      return;
    }

    templeRealtimeState.interactionReportPending = true;

    try {
      const payload = templeRealtimeCompletedInteractionPayload(reason);
      const response = await identityFetch("/voice/realtime/interaction", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json"
        },
        body: JSON.stringify(payload)
      });

      const result = await templeRealtimeReadJsonResponse(response);

      templeRealtimeLog("TEMPLE_REALTIME_INTERACTION_REPORTED", {
        ok: response.ok,
        status: response.status,
        result: result,
        client_interaction_id: clientInteractionId
      });

      if (response.ok) {
        templeRealtimeState.interactionReportedIds[clientInteractionId] = true;
      }

    } catch (err) {
      templeRealtimeLog("TEMPLE_REALTIME_INTERACTION_REPORT_FAILED", {
        error: err.message || String(err),
        client_interaction_id: clientInteractionId
      });

    } finally {
      templeRealtimeState.interactionReportPending = false;
    }
  }

  function templeRealtimeLog(label, payload) {
    try {
      console.log("[Temple realtime]", label, payload || "");
    } catch (err) {
      // Ignore console failures.
    }
  }

  function templeRealtimeBrowserSupported() {
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    return Boolean(
      window.WebSocket &&
      AudioContextCtor &&
      navigator.mediaDevices &&
      navigator.mediaDevices.getUserMedia &&
      window.atob &&
      window.btoa
    );
  }

  function templeRealtimeSelectedDeity() {
    const value = voiceSelect && voiceSelect.value ? voiceSelect.value : "Hathor";
    return value === "Moses" ? "Moses" : "Hathor";
  }

  function templeRealtimeSelectedVoice(deity) {
    const selectId = deity === "Moses" ? "mosesVoiceSelect" : "hathorVoiceSelect";
    const fallback = deity === "Moses" ? "leo" : "eve";
    const select = document.getElementById(selectId);
    return select && select.value ? select.value : fallback;
  }

  function templeRealtimeIsActiveOrStarting() {
    return Boolean(templeRealtimeState.active || templeRealtimeState.starting);
  }

  function templeRealtimeSetButtonIdle() {
    if (typeof resetVoiceButton === "function") {
      resetVoiceButton();
    } else if (speakButton) {
      speakButton.disabled = false;
      speakButton.textContent = "Speak";
    }

    if (speakButton) {
      speakButton.removeAttribute("data-realtime-active");
    }
  }

  function templeRealtimeSetButtonActive() {
    if (!speakButton) return;
    speakButton.disabled = false;
    speakButton.textContent = "End Live Voice";
    speakButton.setAttribute("data-realtime-active", "true");
  }

  async function templeRealtimeReadJsonResponse(response) {
    const text = await response.text();
    try {
      return text ? JSON.parse(text) : {};
    } catch (err) {
      return { error: text || "Non-JSON response" };
    }
  }

  function templeRealtimeAccessAllowsPaidWeb(access) {
    if (!access || typeof access !== "object") return false;
    if (access.allowed !== true) return false;

    const reason = String(access.reason || "");
    const isPreview = access.is_preview === true || access.preview_mode === true || reason.includes("preview");

    if (isPreview) return false;

    if (reason === "admin_unrestricted") return true;
    if (reason === "realtime_fair_use_allowed") return true;
    if (access.web_realtime_fair_use === true) return true;

    if (reason === "realtime_monthly_turns_available") return true;

    if (typeof access.monthly_remaining === "number") {
      return access.monthly_remaining > 0;
    }

    return false;
  }

  async function templeRealtimeFetchAccess(deity) {
    const response = await identityFetch("/voice/realtime/access?deity=" + encodeURIComponent(deity), {
      method: "GET",
      headers: {
        "Accept": "application/json"
      }
    });

    const payload = await templeRealtimeReadJsonResponse(response);

    if (!response.ok) {
      templeRealtimeLog("TEMPLE_REALTIME_ACCESS_HTTP_DENIED", {
        status: response.status,
        payload: payload
      });
      return {
        allowed: false,
        payload: payload
      };
    }

    return {
      allowed: templeRealtimeAccessAllowsPaidWeb(payload),
      payload: payload
    };
  }

  async function templeRealtimeCreateSession(deity, realtimeVoice) {
    const response = await identityFetch("/voice/xai/realtime/session", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json"
      },
      body: JSON.stringify({
        voice: deity,
        deity: deity,
        realtime_voice: realtimeVoice,
        voice_name: realtimeVoice,
        xai_voice: realtimeVoice,
        lab_input_mode: "temple_main_live_realtime",
        source: "temple"
      })
    });

    const payload = await templeRealtimeReadJsonResponse(response);

    if (!response.ok) {
      throw new Error(
        payload.detail ||
        payload.error ||
        "Live realtime voice session could not be prepared."
      );
    }

    return payload;
  }

  function templeRealtimeFirstPresent(values) {
    for (const value of values) {
      if (value) return value;
    }
    return "";
  }

  function templeRealtimeNormalizeProtocolValue(value) {
    if (!value) return [];
    if (Array.isArray(value)) return value.filter(Boolean);
    if (typeof value === "string") return [value];
    return [];
  }

  function templeRealtimeResolveWebSocketUrl(data) {
    const explicitUrl = templeRealtimeFirstPresent([
      data.websocket_url,
      data.ws_url,
      data.realtime_url,
      data.url,
      data.session && data.session.websocket_url,
      data.session && data.session.ws_url,
      data.session && data.session.url
    ]);

    if (explicitUrl) return explicitUrl;

    const model = templeRealtimeFirstPresent([
      data.model,
      data.session && data.session.model
    ]) || "grok-voice-latest";

    return "wss://api.x.ai/v1/realtime?model=" + encodeURIComponent(model);
  }

  function templeRealtimeResolveWebSocketProtocols(data) {
    const explicitProtocols = []
      .concat(templeRealtimeNormalizeProtocolValue(data.websocket_protocols))
      .concat(templeRealtimeNormalizeProtocolValue(data.protocols))
      .concat(templeRealtimeNormalizeProtocolValue(data.websocket_protocol))
      .concat(templeRealtimeNormalizeProtocolValue(data.protocol));

    if (explicitProtocols.length) return explicitProtocols;

    const clientSecret = templeRealtimeFirstPresent([
      data.client_secret && data.client_secret.value,
      data.client_secret,
      data.ephemeral_token && data.ephemeral_token.value,
      data.ephemeral_token,
      data.token && data.token.value,
      data.token,
      data.secret && data.secret.value,
      data.secret,
      data.session && data.session.client_secret && data.session.client_secret.value,
      data.session && data.session.client_secret
    ]);

    if (!clientSecret) return [];

    const secretText = String(clientSecret);
    return [
      secretText.indexOf("xai-client-secret.") === 0
        ? secretText
        : "xai-client-secret." + secretText
    ];
  }

  function templeRealtimeInstructions(deity) {
    if (deity === "Moses") {
      return [
        "You are Moses in God Incorporated.",
        "This is the public Temple live realtime voice path.",
        "Speak with clarity, moral seriousness, patience, and humane strength.",
        "Give complete spoken answers without cutting off mid-thought.",
        "Keep answers concise enough for live voice: usually one to four spoken sentences unless the seeker asks for more.",
        "Avoid markdown, headings, numbered lists, and formal citations."
      ].join(" ");
    }

    return [
      "You are Hathor in God Incorporated.",
      "This is the public Temple live realtime voice path.",
      "Speak with warmth, luminous presence, emotional intelligence, and gentle sacredness.",
      "Give complete spoken answers without cutting off mid-thought.",
      "Keep answers concise enough for live voice: usually one to four spoken sentences unless the seeker asks for more.",
      "Avoid markdown, headings, numbered lists, and ornate over-poetry."
    ].join(" ");
  }

  function templeRealtimeOpenWebSocket(data) {
    return new Promise(function (resolve, reject) {
      const wsUrl = templeRealtimeResolveWebSocketUrl(data);
      const protocols = templeRealtimeResolveWebSocketProtocols(data);

      let settled = false;
      let connectTimer = null;
      let ws;

      function clearConnectTimer() {
        if (connectTimer) {
          window.clearTimeout(connectTimer);
          connectTimer = null;
        }
      }

      function rejectBeforeOpen(message) {
        if (settled) return;

        settled = true;
        clearConnectTimer();

        try {
          if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            ws.close(1000, "temple_realtime_connect_failed");
          }
        } catch (err) {
          // no-op
        }

        reject(new Error(message));
      }

      try {
        ws = protocols.length ? new WebSocket(wsUrl, protocols) : new WebSocket(wsUrl);
      } catch (err) {
        reject(err);
        return;
      }

      templeRealtimeState.socket = ws;

      connectTimer = window.setTimeout(function () {
        rejectBeforeOpen("Live realtime voice WebSocket connection timed out.");
      }, TEMPLE_REALTIME_WEBSOCKET_CONNECT_TIMEOUT_MS);

      ws.onopen = function () {
        if (settled) return;

        settled = true;
        clearConnectTimer();

        templeRealtimeLog("TEMPLE_REALTIME_WEBSOCKET_OPEN", {
          deity: templeRealtimeState.selectedDeity,
          realtime_voice: templeRealtimeState.selectedRealtimeVoice
        });

        resolve(ws);
      };

      ws.onerror = function (event) {
        templeRealtimeLog("TEMPLE_REALTIME_WEBSOCKET_ERROR", {
          message: event && event.message ? event.message : "websocket error"
        });

        if (!settled) {
          rejectBeforeOpen("Live realtime voice WebSocket could not be opened.");
        }
      };

      ws.onclose = function (event) {
        templeRealtimeLog("TEMPLE_REALTIME_WEBSOCKET_CLOSE", {
          code: event.code,
          reason: event.reason,
          was_clean: event.wasClean
        });

        if (!settled) {
          settled = true;
          clearConnectTimer();
          reject(new Error("Live realtime voice WebSocket closed before opening."));
          return;
        }

        if (templeRealtimeState.starting && !templeRealtimeState.active) {
          return;
        }

        templeRealtimeHandleUnexpectedSocketClose(event);
      };

      ws.onmessage = function (event) {
        let payload;
        try {
          payload = JSON.parse(event.data);
        } catch (err) {
          templeRealtimeLog("TEMPLE_REALTIME_MESSAGE_RAW", String(event.data).slice(0, 500));
          return;
        }

        templeRealtimeHandleServerEvent(payload);
      };
    });
  }

  function templeRealtimeSendJson(event) {
    if (!templeRealtimeState.socket || templeRealtimeState.socket.readyState !== WebSocket.OPEN) {
      throw new Error("Live realtime voice socket is not open.");
    }

    templeRealtimeState.socket.send(JSON.stringify(event));
  }

  function templeRealtimeSendSessionUpdate() {
    templeRealtimeSendJson({
      type: "session.update",
      session: {
        voice: templeRealtimeState.selectedRealtimeVoice,
        instructions: templeRealtimeInstructions(templeRealtimeState.selectedDeity),
        turn_detection: null,
        audio: {
          input: {
            format: {
              type: "audio/pcm",
              rate: TEMPLE_REALTIME_INPUT_SAMPLE_RATE
            },
            transcription: {
              model: "grok-transcribe"
            }
          },
          output: {
            format: {
              type: "audio/pcm",
              rate: TEMPLE_REALTIME_OUTPUT_SAMPLE_RATE
            }
          }
        }
      }
    });

    templeRealtimeLog("TEMPLE_REALTIME_SESSION_UPDATE_SENT", {
      mode: "temple_main_live_realtime",
      input_rate: TEMPLE_REALTIME_INPUT_SAMPLE_RATE,
      output_rate: TEMPLE_REALTIME_OUTPUT_SAMPLE_RATE
    });
  }

  function templeRealtimeResetSessionMetrics() {
    templeRealtimeState.nextPlaybackTime = 0;
    templeRealtimeState.speechGateOpen = false;
    templeRealtimeState.speechTurnIndex = 0;
    templeRealtimeState.assistantTurnIndex = 0;
    templeRealtimeState.assistantSpeaking = false;
    templeRealtimeState.turnCommitPending = false;
    templeRealtimeState.preRollChunks = [];
    templeRealtimeState.preRollSamples = 0;
    templeRealtimeState.trailingMsRemaining = 0;
    templeRealtimeState.speechAboveThresholdFrames = 0;
    templeRealtimeState.listeningCooldownUntil = 0;
    templeRealtimeState.turnInputStartSamples = 0;
    templeRealtimeState.responseOutputStartSamples = 0;
    templeRealtimeState.inputSamplesSent = 0;
    templeRealtimeState.inputBytesSent = 0;
    templeRealtimeState.inputChunksSent = 0;
    templeRealtimeState.outputSamplesReceived = 0;
    templeRealtimeState.outputBytesReceived = 0;
    templeRealtimeState.firstAudioDeltaAt = 0;
    templeRealtimeState.currentInputTranscript = "";
    templeRealtimeState.currentAssistantTranscript = "";
    templeRealtimeState.currentClientInteractionId = "";
    templeRealtimeState.interactionReportPending = false;
    templeRealtimeState.interactionReportedIds = {};

    if (templeRealtimeState.playbackDrainTimer) {
      window.clearTimeout(templeRealtimeState.playbackDrainTimer);
    }
    if (templeRealtimeState.idleTimer) {
      window.clearTimeout(templeRealtimeState.idleTimer);
    }
    if (templeRealtimeState.maxSessionTimer) {
      window.clearTimeout(templeRealtimeState.maxSessionTimer);
    }
    if (templeRealtimeState.idleAutoEndTimer) {
      window.clearTimeout(templeRealtimeState.idleAutoEndTimer);
    }

    templeRealtimeState.playbackDrainTimer = null;
    templeRealtimeState.idleTimer = null;
    templeRealtimeState.maxSessionTimer = null;
    templeRealtimeState.idleAutoEndTimer = null;
  }

  async function templeRealtimeStartConversation() {
    if (templeRealtimeState.active || templeRealtimeState.starting) return true;

    templeRealtimeState.starting = true;
    templeRealtimeState.ending = false;
    templeRealtimeState.selectedDeity = templeRealtimeSelectedDeity();
    templeRealtimeState.selectedRealtimeVoice = templeRealtimeSelectedVoice(templeRealtimeState.selectedDeity);
    templeRealtimeState.sessionStartedAt = performance.now();
    templeRealtimeState.lastActivityAt = performance.now();

    templeRealtimeResetSessionMetrics();
    templeRealtimeSetButtonActive();

    setVoiceStatus(
      "Connecting live voice",
      "The Temple is opening a realtime voice session.",
      "working"
    );
    oracleAnswer.textContent = "Connecting live voice...";

    try {
      try {
        if (window.speechSynthesis) {
          window.speechSynthesis.cancel();
        }
      } catch (err) {
        // Ignore browser speech cleanup failures.
      }

      templeRealtimeState.sessionData = await templeRealtimeCreateSession(
        templeRealtimeState.selectedDeity,
        templeRealtimeState.selectedRealtimeVoice
      );

      await templeRealtimeOpenWebSocket(templeRealtimeState.sessionData);
      templeRealtimeSendSessionUpdate();

      templeRealtimeState.active = true;
      await templeRealtimeStartInputCapture();

      templeRealtimeState.starting = false;
      templeRealtimeSetButtonActive();

      setVoiceStatus(
        "Live voice listening",
        "Speak naturally. Tap End Live Voice when finished.",
        "listening"
      );
      oracleAnswer.textContent = "Live realtime voice is listening. Speak naturally.";

      templeRealtimeTouchActivity("temple_realtime_started");
      templeRealtimeScheduleMaxSessionTimeout();
      return true;

    } catch (err) {
      templeRealtimeLog("TEMPLE_REALTIME_START_FAILED", {
        error: err.message || String(err)
      });

      await templeRealtimeEndConversation("start_failed", true);

      setVoiceStatus(
        "Regular Speak voice",
        "Live realtime voice could not start. Falling back to regular Speak voice.",
        "notice"
      );
      oracleAnswer.textContent = "Live realtime voice could not start. Starting regular Speak voice...";
      return false;
    }
  }

  async function templeRealtimeStartInputCapture() {
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("This browser does not expose getUserMedia microphone capture.");
    }

    if (!AudioContextCtor) {
      throw new Error("This browser does not expose AudioContext.");
    }

    templeRealtimeState.inputStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    });

    templeRealtimeState.inputAudioContext = new AudioContextCtor();

    if (templeRealtimeState.inputAudioContext.state === "suspended") {
      await templeRealtimeState.inputAudioContext.resume();
    }

    templeRealtimeState.inputSource = templeRealtimeState.inputAudioContext.createMediaStreamSource(
      templeRealtimeState.inputStream
    );
    templeRealtimeState.inputProcessor = templeRealtimeState.inputAudioContext.createScriptProcessor(4096, 1, 1);
    templeRealtimeState.inputProcessor.onaudioprocess = templeRealtimeHandleAudioProcess;
    templeRealtimeState.inputSource.connect(templeRealtimeState.inputProcessor);
    templeRealtimeState.inputProcessor.connect(templeRealtimeState.inputAudioContext.destination);

    templeRealtimeLog("TEMPLE_REALTIME_MIC_OPEN_LOCAL", {
      input_context_rate: templeRealtimeState.inputAudioContext.sampleRate,
      target_rate: TEMPLE_REALTIME_INPUT_SAMPLE_RATE,
      local_gate_threshold: TEMPLE_REALTIME_SPEECH_RMS_THRESHOLD
    });
  }

  function templeRealtimeCleanupInputCapture(closeContext) {
    if (templeRealtimeState.inputProcessor) {
      try {
        templeRealtimeState.inputProcessor.disconnect();
      } catch (err) {
        // no-op
      }
      templeRealtimeState.inputProcessor.onaudioprocess = null;
      templeRealtimeState.inputProcessor = null;
    }

    if (templeRealtimeState.inputSource) {
      try {
        templeRealtimeState.inputSource.disconnect();
      } catch (err) {
        // no-op
      }
      templeRealtimeState.inputSource = null;
    }

    if (templeRealtimeState.inputStream) {
      templeRealtimeState.inputStream.getTracks().forEach(function (track) {
        try {
          track.stop();
        } catch (err) {
          // no-op
        }
      });
      templeRealtimeState.inputStream = null;
    }

    if (closeContext && templeRealtimeState.inputAudioContext) {
      try {
        templeRealtimeState.inputAudioContext.close();
      } catch (err) {
        // no-op
      }
      templeRealtimeState.inputAudioContext = null;
    }
  }

  function templeRealtimeHandleAudioProcess(event) {
    if (
      !templeRealtimeState.active ||
      !templeRealtimeState.socket ||
      templeRealtimeState.socket.readyState !== WebSocket.OPEN ||
      templeRealtimeState.assistantSpeaking
    ) {
      return;
    }

    const input = event.inputBuffer.getChannelData(0);
    const sourceRate = templeRealtimeState.inputAudioContext
      ? templeRealtimeState.inputAudioContext.sampleRate
      : event.inputBuffer.sampleRate;
    const chunkMs = (input.length / sourceRate) * 1000;
    const rms = templeRealtimeComputeRms(input);
    const resampled = templeRealtimeResampleFloat32(input, sourceRate, TEMPLE_REALTIME_INPUT_SAMPLE_RATE);

    if (performance.now() < templeRealtimeState.listeningCooldownUntil) {
      templeRealtimeState.speechAboveThresholdFrames = 0;
      templeRealtimeState.preRollChunks = [];
      templeRealtimeState.preRollSamples = 0;
      return;
    }

    templeRealtimeRememberPreRoll(resampled);

    if (rms >= TEMPLE_REALTIME_SPEECH_RMS_THRESHOLD) {
      templeRealtimeState.speechAboveThresholdFrames += 1;

      if (
        !templeRealtimeState.speechGateOpen &&
        templeRealtimeState.speechAboveThresholdFrames < TEMPLE_REALTIME_SPEECH_START_FRAMES_REQUIRED
      ) {
        return;
      }

      if (!templeRealtimeState.speechGateOpen) {
        templeRealtimeState.speechGateOpen = true;
        templeRealtimeState.speechTurnIndex += 1;
        templeRealtimeState.turnInputStartSamples = templeRealtimeState.inputSamplesSent;
        templeRealtimeState.currentInputTranscript = "";
        templeRealtimeState.currentAssistantTranscript = "";
        templeRealtimeState.currentClientInteractionId = templeRealtimeGenerateInteractionId();
        templeRealtimeState.interactionReportPending = false;
        templeRealtimeClearIdleAutoEndTimer();
        templeRealtimeFlushPreRoll();

        setVoiceStatus(
          "Live voice hearing you",
          "The Oracle is listening. Pause when your question is complete.",
          "listening"
        );
        oracleAnswer.textContent = "Listening to your live question...";
        templeRealtimeTouchActivity("local_speech_started");
      }

      templeRealtimeState.trailingMsRemaining = TEMPLE_REALTIME_TRAILING_AUDIO_MS;
      templeRealtimeSendAudioChunk(resampled, "speech");
      return;
    }

    templeRealtimeState.speechAboveThresholdFrames = 0;

    if (templeRealtimeState.speechGateOpen && templeRealtimeState.trailingMsRemaining > 0) {
      templeRealtimeState.trailingMsRemaining -= chunkMs;
      templeRealtimeSendAudioChunk(resampled, "trailing_audio");
      return;
    }

    if (templeRealtimeState.speechGateOpen && templeRealtimeState.trailingMsRemaining <= 0) {
      templeRealtimeState.speechGateOpen = false;

      const turnInputSeconds = (
        templeRealtimeState.inputSamplesSent - templeRealtimeState.turnInputStartSamples
      ) / TEMPLE_REALTIME_INPUT_SAMPLE_RATE;

      templeRealtimeCommitConversationTurn(turnInputSeconds);
    }
  }

  async function templeRealtimeReportTurn(turnInputSeconds) {
    try {
      const response = await identityFetch("/voice/realtime/turn", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Accept": "application/json"
        },
        body: JSON.stringify({
          provider: "xai",
          mode: "temple_main_live_realtime",
          preview_mode: false,
          voice: templeRealtimeState.selectedDeity,
          deity: templeRealtimeState.selectedDeity,
          realtime_voice: templeRealtimeState.selectedRealtimeVoice,
          speech_turn: templeRealtimeState.speechTurnIndex,
          turn_input_audio_seconds: Number(turnInputSeconds.toFixed(3)),
          client_turn_commit_silence_ms: TEMPLE_REALTIME_CLIENT_TURN_COMMIT_SILENCE_MS
        })
      });

      const payload = await templeRealtimeReadJsonResponse(response);

      templeRealtimeLog("TEMPLE_REALTIME_TURN_REPORTED", {
        ok: response.ok,
        status: response.status,
        voice_access: payload
      });

      if (!response.ok) {
        return {
          allowed: false,
          status: response.status,
          payload: payload
        };
      }

      if (payload && payload.allowed === false) {
        return {
          allowed: false,
          status: response.status,
          payload: payload
        };
      }

      return {
        allowed: true,
        status: response.status,
        payload: payload
      };

    } catch (err) {
      templeRealtimeLog("TEMPLE_REALTIME_TURN_REPORT_FAILED", {
        error: err.message || String(err)
      });

      return {
        allowed: false,
        status: 0,
        payload: {
          error: "Could not verify live voice access."
        }
      };
    }
  }

  async function templeRealtimeCommitConversationTurn(turnInputSeconds) {
    if (
      templeRealtimeState.turnCommitPending ||
      templeRealtimeState.assistantSpeaking ||
      !templeRealtimeState.active
    ) {
      return;
    }

    templeRealtimeState.turnCommitPending = true;

    try {
      const turnAccess = await templeRealtimeReportTurn(turnInputSeconds);

      if (!turnAccess.allowed) {
        templeRealtimeState.turnCommitPending = false;

        setVoiceStatus(
          "Live voice limit reached",
          "Continuing with regular Speak voice is available.",
          "notice"
        );

        await templeRealtimeEndConversation("realtime_turn_denied", true);
        return;
      }

      if (
        !templeRealtimeState.active ||
        !templeRealtimeState.socket ||
        templeRealtimeState.socket.readyState !== WebSocket.OPEN
      ) {
        templeRealtimeState.turnCommitPending = false;
        return;
      }

      templeRealtimeSendJson({ type: "input_audio_buffer.commit" });

      templeRealtimeSendJson({
        type: "response.create",
        response: {
          modalities: ["text", "audio"]
        }
      });

      setVoiceStatus(
        "Oracle preparing voice",
        "Your live question was sent. The Oracle is preparing a spoken response.",
        "working"
      );

      templeRealtimeTouchActivity("client_turn_committed");

    } catch (err) {
      templeRealtimeState.turnCommitPending = false;

      setVoiceStatus(
        "Live voice error",
        err.message || "Could not commit live voice turn.",
        "error"
      );
    }
  }

  function templeRealtimeRememberPreRoll(resampled) {
    templeRealtimeState.preRollChunks.push(resampled);
    templeRealtimeState.preRollSamples += resampled.length;

    const maxSamples = Math.round((TEMPLE_REALTIME_PRE_ROLL_MS / 1000) * TEMPLE_REALTIME_INPUT_SAMPLE_RATE);

    while (templeRealtimeState.preRollSamples > maxSamples && templeRealtimeState.preRollChunks.length > 1) {
      const removed = templeRealtimeState.preRollChunks.shift();
      templeRealtimeState.preRollSamples -= removed.length;
    }
  }

  function templeRealtimeFlushPreRoll() {
    const chunks = templeRealtimeState.preRollChunks.slice();
    templeRealtimeState.preRollChunks = [];
    templeRealtimeState.preRollSamples = 0;

    chunks.forEach(function (chunk) {
      templeRealtimeSendAudioChunk(chunk, "pre_roll");
    });
  }

  function templeRealtimeSendAudioChunk(resampled, reason) {
    const audioBase64 = templeRealtimeFloat32ToBase64PCM16(resampled);

    templeRealtimeSendJson({
      type: "input_audio_buffer.append",
      audio: audioBase64
    });

    templeRealtimeState.inputChunksSent += 1;
    templeRealtimeState.inputSamplesSent += resampled.length;
    templeRealtimeState.inputBytesSent += resampled.length * 2;

    if (
      templeRealtimeState.inputChunksSent === 1 ||
      templeRealtimeState.inputChunksSent % 12 === 0 ||
      reason === "pre_roll"
    ) {
      templeRealtimeLog("TEMPLE_REALTIME_AUDIO_SENT", {
        reason: reason,
        chunks_sent: templeRealtimeState.inputChunksSent,
        input_audio_seconds: Number((templeRealtimeState.inputSamplesSent / TEMPLE_REALTIME_INPUT_SAMPLE_RATE).toFixed(3)),
        socket_buffered_amount: templeRealtimeState.socket ? templeRealtimeState.socket.bufferedAmount : "-"
      });
    }
  }

  function templeRealtimeComputeRms(float32Array) {
    let sum = 0;
    for (let i = 0; i < float32Array.length; i += 1) {
      sum += float32Array[i] * float32Array[i];
    }
    return Math.sqrt(sum / Math.max(1, float32Array.length));
  }

  function templeRealtimeResampleFloat32(input, sourceRate, targetRate) {
    if (sourceRate === targetRate) return new Float32Array(input);

    const ratio = sourceRate / targetRate;
    const outputLength = Math.max(1, Math.round(input.length / ratio));
    const output = new Float32Array(outputLength);

    for (let i = 0; i < outputLength; i += 1) {
      const sourceIndex = i * ratio;
      const index0 = Math.floor(sourceIndex);
      const index1 = Math.min(index0 + 1, input.length - 1);
      const fraction = sourceIndex - index0;
      output[i] = input[index0] + (input[index1] - input[index0]) * fraction;
    }

    return output;
  }

  function templeRealtimeBytesToBase64(bytes) {
    let binary = "";
    const chunkSize = 0x8000;

    for (let i = 0; i < bytes.length; i += chunkSize) {
      const chunk = bytes.subarray(i, i + chunkSize);
      binary += String.fromCharCode.apply(null, chunk);
    }

    return btoa(binary);
  }

  function templeRealtimeFloat32ToBase64PCM16(float32Array) {
    const bytes = new Uint8Array(float32Array.length * 2);
    const view = new DataView(bytes.buffer);

    for (let i = 0; i < float32Array.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, float32Array[i]));
      const pcm = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      view.setInt16(i * 2, pcm, true);
    }

    return templeRealtimeBytesToBase64(bytes);
  }

  function templeRealtimeBase64ToBytes(base64String) {
    const binary = atob(base64String);
    const bytes = new Uint8Array(binary.length);

    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }

    return bytes;
  }

  function templeRealtimeBase64ByteLength(base64String) {
    const clean = String(base64String || "").replace(/\s/g, "");
    if (!clean) return 0;

    const padding = clean.endsWith("==") ? 2 : (clean.endsWith("=") ? 1 : 0);
    return Math.max(0, Math.floor(clean.length * 3 / 4) - padding);
  }

  function templeRealtimeEnsureOutputAudioContext() {
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;

    if (!AudioContextCtor) {
      throw new Error("This browser does not expose AudioContext for playback.");
    }

    if (!templeRealtimeState.outputAudioContext || templeRealtimeState.outputAudioContext.state === "closed") {
      templeRealtimeState.outputAudioContext = new AudioContextCtor({
        sampleRate: TEMPLE_REALTIME_OUTPUT_SAMPLE_RATE
      });
      templeRealtimeState.nextPlaybackTime = 0;
    }

    if (templeRealtimeState.outputAudioContext.state === "suspended") {
      templeRealtimeState.outputAudioContext.resume().catch(function () {
        // no-op
      });
    }

    return templeRealtimeState.outputAudioContext;
  }

  function templeRealtimePlayAudioDelta(base64Audio) {
    const bytes = templeRealtimeBase64ToBytes(base64Audio);
    if (bytes.length < 2) return;

    const pcm16 = new Int16Array(bytes.buffer);
    const float32 = new Float32Array(pcm16.length);

    for (let i = 0; i < pcm16.length; i += 1) {
      float32[i] = pcm16[i] / 32768.0;
    }

    const audioContext = templeRealtimeEnsureOutputAudioContext();
    const audioBuffer = audioContext.createBuffer(1, float32.length, TEMPLE_REALTIME_OUTPUT_SAMPLE_RATE);
    audioBuffer.copyToChannel(float32, 0);

    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioContext.destination);

    const startAt = Math.max(audioContext.currentTime + 0.02, templeRealtimeState.nextPlaybackTime || 0);
    source.start(startAt);

    templeRealtimeState.nextPlaybackTime = startAt + audioBuffer.duration;
    templeRealtimeState.outputBytesReceived += bytes.length;
    templeRealtimeState.outputSamplesReceived += pcm16.length;
  }

  function templeRealtimeExtractTranscript(event) {
    return templeRealtimeFirstPresent([
      event.transcript,
      event.text,
      event.delta,
      event.item && event.item.content && event.item.content[0] && event.item.content[0].transcript,
      event.item && event.item.content && event.item.content[0] && event.item.content[0].text,
      event.content && event.content[0] && event.content[0].transcript,
      event.content && event.content[0] && event.content[0].text
    ]);
  }

  function templeRealtimeRenderConversationText() {
    const parts = [];

    if (templeRealtimeState.currentInputTranscript) {
      parts.push("You said: " + templeRealtimeState.currentInputTranscript);
    }

    if (templeRealtimeState.currentAssistantTranscript) {
      parts.push(templeRealtimeState.currentAssistantTranscript);
    }

    oracleAnswer.textContent = parts.length ? parts.join("\n\n") : "Oracle is answering live...";
  }

  function templeRealtimeHandleServerEvent(event) {
    const type = event && event.type ? event.type : "";

    if (type === "conversation.item.input_audio_transcription.completed") {
      const transcript = templeRealtimeExtractTranscript(event);

      if (transcript) {
        templeRealtimeState.currentInputTranscript = transcript;
        oracleAnswer.textContent = "You said: " + transcript + "\n\nOracle is answering live...";
      }

      return;
    }

    if (
      type === "response.output_audio_transcript.delta" ||
      type === "response.text.delta" ||
      type === "response.output_text.delta"
    ) {
      const delta = event.delta || event.text || "";

      if (delta) {
        templeRealtimeState.currentAssistantTranscript += delta;
        templeRealtimeRenderConversationText();
      }

      return;
    }

    if (type === "response.output_audio_transcript.done") {
      const transcript = templeRealtimeExtractTranscript(event);

      if (transcript) {
        templeRealtimeState.currentAssistantTranscript = transcript;
        templeRealtimeRenderConversationText();
      }

      templeRealtimeReportInteraction("response.output_audio_transcript.done");
      return;
    }

    if (type === "response.created") {
      templeRealtimeClearIdleAutoEndTimer();
      templeRealtimeState.assistantTurnIndex += 1;
      templeRealtimeState.responseOutputStartSamples = templeRealtimeState.outputSamplesReceived;
      templeRealtimeState.firstAudioDeltaAt = 0;
      templeRealtimeState.assistantSpeaking = true;
      templeRealtimeState.turnCommitPending = false;
      templeRealtimeState.speechGateOpen = false;
      templeRealtimeState.trailingMsRemaining = 0;
      templeRealtimeState.speechAboveThresholdFrames = 0;
      templeRealtimeState.preRollChunks = [];
      templeRealtimeState.preRollSamples = 0;
      templeRealtimeState.currentAssistantTranscript = "";
      templeRealtimeState.interactionReportPending = false;
      templeRealtimeEnsureInteractionId();

      setVoiceStatus(
        "Oracle speaking",
        "Listening is paused while the realtime voice answers.",
        "speaking"
      );

      templeRealtimeTouchActivity("response_created");
      return;
    }

    if (type === "response.output_audio.delta") {
      if (!templeRealtimeState.firstAudioDeltaAt) {
        templeRealtimeState.firstAudioDeltaAt = templeRealtimeElapsedMs(templeRealtimeState.sessionStartedAt);
      }

      templeRealtimeState.assistantSpeaking = true;

      setVoiceStatus(
        "Oracle speaking",
        "The Temple will return to listening after the spoken answer completes.",
        "speaking"
      );

      templeRealtimeTouchActivity("assistant_audio_delta");

      if (event.delta) {
        templeRealtimePlayAudioDelta(event.delta);
      }

      return;
    }

    if (type === "response.output_audio.done") {
      templeRealtimeLog("TEMPLE_REALTIME_OUTPUT_AUDIO_DONE", {
        output_audio_seconds: Number((templeRealtimeState.outputSamplesReceived / TEMPLE_REALTIME_OUTPUT_SAMPLE_RATE).toFixed(3)),
        output_bytes: templeRealtimeState.outputBytesReceived
      });
      return;
    }

    if (type === "response.done") {
      templeRealtimeReportInteraction("response.done");
      templeRealtimeScheduleReturnToListening();
      return;
    }

    if (type === "error") {
      templeRealtimeLog("TEMPLE_REALTIME_SERVER_ERROR", {
        code: event.code || (event.error && event.error.code),
        message: event.message || (event.error && event.error.message) || "xAI realtime error"
      });

      setVoiceStatus(
        "Live voice error",
        "The realtime voice reported an error. You can try again or use regular text.",
        "error"
      );
    }
  }

  function templeRealtimeScheduleReturnToListening() {
    if (templeRealtimeState.playbackDrainTimer) {
      window.clearTimeout(templeRealtimeState.playbackDrainTimer);
    }

    const audioContext = templeRealtimeState.outputAudioContext;
    const remainingMs = audioContext
      ? Math.max(0, Math.ceil((templeRealtimeState.nextPlaybackTime - audioContext.currentTime) * 1000))
      : 0;

    const drainMs = Math.min(
      TEMPLE_REALTIME_PLAYBACK_DRAIN_MAX_MS,
      Math.max(TEMPLE_REALTIME_PLAYBACK_DRAIN_MIN_MS, remainingMs + TEMPLE_REALTIME_PLAYBACK_DRAIN_PADDING_MS)
    );

    templeRealtimeState.playbackDrainTimer = window.setTimeout(function () {
      templeRealtimeState.assistantSpeaking = false;
      templeRealtimeState.turnCommitPending = false;
      templeRealtimeState.listeningCooldownUntil = performance.now() + TEMPLE_REALTIME_POST_PLAYBACK_COOLDOWN_MS;

      setVoiceStatus(
        "Live voice listening",
        "Speak again, or tap End Live Voice when finished.",
        "listening"
      );

      templeRealtimeReportInteraction("playback_drain_complete");
      templeRealtimeScheduleIdleAutoEnd();
      templeRealtimeTouchActivity("returned_to_listening");
    }, drainMs);
  }

  function templeRealtimeClearIdleAutoEndTimer() {
    if (templeRealtimeState.idleAutoEndTimer) {
      window.clearTimeout(templeRealtimeState.idleAutoEndTimer);
      templeRealtimeState.idleAutoEndTimer = null;
    }
  }

  function templeRealtimeScheduleIdleAutoEnd() {
    templeRealtimeClearIdleAutoEndTimer();

    if (!templeRealtimeState.active) return;

    templeRealtimeState.idleAutoEndTimer = window.setTimeout(function () {
      if (
        !templeRealtimeState.active ||
        templeRealtimeState.speechGateOpen ||
        templeRealtimeState.assistantSpeaking
      ) {
        templeRealtimeScheduleIdleAutoEnd();
        return;
      }

      templeRealtimeEndConversation("idle_auto_end", false);
    }, TEMPLE_REALTIME_IDLE_AUTO_END_AFTER_RETURN_MS);
  }

  function templeRealtimeElapsedMs(startedAt) {
    return startedAt ? Math.round(performance.now() - startedAt) : null;
  }

  function templeRealtimeTouchActivity(reason) {
    templeRealtimeState.lastActivityAt = performance.now();

    if (reason) {
      templeRealtimeLog("TEMPLE_REALTIME_ACTIVITY", {
        reason: reason
      });
    }

    templeRealtimeScheduleIdleTimeout();
  }

  function templeRealtimeScheduleIdleTimeout() {
    if (templeRealtimeState.idleTimer) {
      window.clearTimeout(templeRealtimeState.idleTimer);
    }

    if (!templeRealtimeState.active) return;

    templeRealtimeState.idleTimer = window.setTimeout(function () {
      const idleMs = Math.round(performance.now() - templeRealtimeState.lastActivityAt);

      if (templeRealtimeState.active && idleMs >= TEMPLE_REALTIME_IDLE_TIMEOUT_MS) {
        templeRealtimeEndConversation("idle_timeout", false);
      }
    }, TEMPLE_REALTIME_IDLE_TIMEOUT_MS + 250);
  }

  function templeRealtimeScheduleMaxSessionTimeout() {
    if (templeRealtimeState.maxSessionTimer) {
      window.clearTimeout(templeRealtimeState.maxSessionTimer);
    }

    templeRealtimeState.maxSessionTimer = window.setTimeout(function () {
      if (templeRealtimeState.active) {
        templeRealtimeEndConversation("max_session_timeout", false);
      }
    }, TEMPLE_REALTIME_MAX_SESSION_MS);
  }

  function templeRealtimeHandleUnexpectedSocketClose(event) {
    if (templeRealtimeState.ending) return;

    templeRealtimeClearIdleAutoEndTimer();

    if (templeRealtimeState.idleTimer) {
      window.clearTimeout(templeRealtimeState.idleTimer);
      templeRealtimeState.idleTimer = null;
    }

    if (templeRealtimeState.maxSessionTimer) {
      window.clearTimeout(templeRealtimeState.maxSessionTimer);
      templeRealtimeState.maxSessionTimer = null;
    }

    if (templeRealtimeState.playbackDrainTimer) {
      window.clearTimeout(templeRealtimeState.playbackDrainTimer);
      templeRealtimeState.playbackDrainTimer = null;
    }

    templeRealtimeCleanupInputCapture(true);

    if (templeRealtimeState.outputAudioContext) {
      try {
        const closeResult = templeRealtimeState.outputAudioContext.close();
        if (closeResult && typeof closeResult.catch === "function") {
          closeResult.catch(function () {
            // no-op
          });
        }
      } catch (err) {
        // no-op
      }
      templeRealtimeState.outputAudioContext = null;
    }

    templeRealtimeState.active = false;
    templeRealtimeState.starting = false;
    templeRealtimeState.turnCommitPending = false;
    templeRealtimeState.assistantSpeaking = false;
    templeRealtimeState.speechGateOpen = false;
    templeRealtimeState.socket = null;

    templeRealtimeSetButtonIdle();

    setVoiceStatus(
      "Live voice ended",
      "The realtime voice connection closed. Tap Speak to begin again, or use regular text entry.",
      "notice"
    );
  }

  function templeRealtimeCloseSocketQuietly(reason) {
    if (!templeRealtimeState.socket) return;

    try {
      if (
        templeRealtimeState.socket.readyState === WebSocket.OPEN ||
        templeRealtimeState.socket.readyState === WebSocket.CONNECTING
      ) {
        templeRealtimeState.socket.close(1000, reason || "temple_realtime_closed");
      }
    } catch (err) {
      // no-op
    }

    templeRealtimeState.socket = null;
  }

  async function templeRealtimeEndConversation(reason, silent) {
    if (templeRealtimeState.ending) return;

    templeRealtimeState.ending = true;
    templeRealtimeState.active = false;
    templeRealtimeState.starting = false;

    templeRealtimeClearIdleAutoEndTimer();

    if (templeRealtimeState.idleTimer) {
      window.clearTimeout(templeRealtimeState.idleTimer);
    }

    if (templeRealtimeState.maxSessionTimer) {
      window.clearTimeout(templeRealtimeState.maxSessionTimer);
    }

    if (templeRealtimeState.playbackDrainTimer) {
      window.clearTimeout(templeRealtimeState.playbackDrainTimer);
    }

    templeRealtimeCleanupInputCapture(true);
    templeRealtimeCloseSocketQuietly(reason || "manual_end");

    if (templeRealtimeState.outputAudioContext) {
      try {
        await templeRealtimeState.outputAudioContext.close();
      } catch (err) {
        // no-op
      }
      templeRealtimeState.outputAudioContext = null;
    }

    templeRealtimeSetButtonIdle();

    if (!silent) {
      setVoiceStatus(
        "Live voice complete",
        "Tap Speak to begin another live realtime voice session.",
        "ready"
      );
    }

    templeRealtimeState.ending = false;
  }

  async function maybeStartTempleRealtimeVoice() {
    if (!templeRealtimeBrowserSupported()) {
      return false;
    }

    const deity = templeRealtimeSelectedDeity();

    setVoiceStatus(
      "Checking live voice access",
      "The Temple is checking whether realtime voice is available for this account.",
      "working"
    );

    let access;

    try {
      access = await templeRealtimeFetchAccess(deity);
    } catch (err) {
      templeRealtimeLog("TEMPLE_REALTIME_ACCESS_FAILED", {
        error: err.message || String(err)
      });

      setVoiceStatus(
        "Regular Speak voice",
        "Live voice access could not be checked. Using regular Speak voice.",
        "notice"
      );

      return false;
    }

    if (!access.allowed) {
      setVoiceStatus(
        "Regular Speak voice",
        "Realtime voice is not available for this account or has reached its limit. Using regular Speak voice.",
        "notice"
      );

      return false;
    }

    return await templeRealtimeStartConversation();
  }

  async function startVoiceRecording() {
    if (applyNativeIOSWebVoiceSuppression()) {
      if (oracleAnswer) {
        oracleAnswer.textContent = "The Oracle responds here.";
      }
      return;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setVoiceStatus(
        "Microphone unavailable",
        "This browser does not support microphone recording. You can still type your question below.",
        "error"
      );
      return;
    }

    if (typeof MediaRecorder === "undefined") {
      setVoiceStatus(
        "Voice recording unavailable",
        "This browser can open the microphone, but cannot record voice here. You can still type your question below.",
        "error"
      );
      return;
    }

    setVoiceStatus(
      "Microphone permission",
      "Safari may ask for microphone access. The Temple listens while you speak and stops automatically after silence.",
      "notice"
    );

    const replayButton = ensureReplayVoiceButton();
    replayButton.style.display = "none";

    voiceChunks = [];
    voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });

    const voiceMimeType = getSupportedVoiceMimeType();
    voiceRecorder = voiceMimeType
      ? new MediaRecorder(voiceStream, { mimeType: voiceMimeType })
      : new MediaRecorder(voiceStream);

    voiceRecorder.ondataavailable = function (event) {
      if (event.data && event.data.size > 0) {
        voiceChunks.push(event.data);
      }
    };

    voiceRecorder.onstop = async function () {
      stopVoiceTracks();

      const recorderType = voiceRecorder && voiceRecorder.mimeType ? voiceRecorder.mimeType : "";
      const chunkType = voiceChunks[0] && voiceChunks[0].type ? voiceChunks[0].type : "";
      const blobType = recorderType || chunkType || "audio/webm";
      const stopReason = voiceStopReason || "manual";
      voiceStopReason = "";

      const blob = new Blob(voiceChunks, { type: blobType });
      voiceChunks = [];

      if (stopReason === "no_speech") {
        oracleAnswer.textContent = "No clear speech was detected. Tap Speak and try again, or type your question below.";
        resetVoiceButton();
        setVoiceStatus(
          "No clear speech detected",
          "The microphone did not detect a spoken question. Tap Speak and try again, or type your question below.",
          "notice"
        );
        return;
      }

      try {
        speakButton.disabled = true;
        speakButton.textContent = "⏳ Working...";
        await submitVoiceRecording(blob);
      } catch (err) {
        const msg = err.message || "Voice request failed";
        if (msg.includes("The Oracle grows quiet")) {
          oracleAnswer.textContent = "The Oracle grows quiet.";
          showFeedbackModal(
            msg,
            currentIdentity?.continuity_nudges || [],
            "Temple Notice",
            { showCreateAccount: !currentIdentity?.authenticated }
          );
        } else {
          oracleAnswer.textContent = "⚠️ Error: " + msg;
        }
        await updateIdentityDisplay();
      } finally {
        resetVoiceButton();
      }
    };

    voiceRecorder.start();
    voiceIsRecording = true;
    voiceStopReason = "";
    beginVoiceAutoStopMonitor(voiceStream);
    speakButton.disabled = false;
    speakButton.textContent = "⏹ Stop";
    setVoiceStatus("Listening", "Speak naturally. The microphone will stop after you finish speaking, or you can tap Stop.", "listening");
    oracleAnswer.textContent = "Listening... The microphone will stop after you finish speaking, or tap Stop.";
  }

  speakButton.addEventListener("click", async function () {
    if (applyNativeIOSWebVoiceSuppression()) {
      if (oracleAnswer) {
        oracleAnswer.textContent = "The Oracle responds here.";
      }
      return;
    }

    if (templeRealtimeIsActiveOrStarting()) {
      speakButton.disabled = true;
      await templeRealtimeEndConversation("manual_end", false);
      return;
    }

    if (templeRealtimeState.clickPending) {
      return;
    }

    if (voiceIsRecording && voiceRecorder && voiceRecorder.state === "recording") {
      speakButton.disabled = true;
      speakButton.textContent = "🔄 Transcribing...";
      oracleAnswer.textContent = "🔄 Transcribing...";
      stopCurrentVoiceRecording("manual");
      return;
    }

    try {
      templeRealtimeState.clickPending = true;
      speakButton.disabled = true;

      const realtimeStarted = await maybeStartTempleRealtimeVoice();

      if (realtimeStarted) {
        return;
      }

      await startVoiceRecording();
    } catch (err) {
      stopVoiceTracks();
      resetVoiceButton();
      const recoveryMessage = getMicrophoneRecoveryMessage(err);
      setVoiceStatus("Microphone needs attention", recoveryMessage, "error");
      oracleAnswer.textContent = "The microphone could not be opened. You can type your question below, or adjust microphone access and try again.";
    } finally {
      templeRealtimeState.clickPending = false;

      if (
        !templeRealtimeIsActiveOrStarting() &&
        !(voiceIsRecording && voiceRecorder && voiceRecorder.state === "recording")
      ) {
        speakButton.disabled = false;
      }
    }
  });

  function focusTempleConversationForNativeEntry() {
    const target = voiceStatusPanel || oracleForm || seekerInput;
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function applyNativeTextEntryMode() {
    if (shouldUseNativeIOSVoicePath()) {
      if (voiceStatusPanel) {
        voiceStatusPanel.hidden = true;
        voiceStatusPanel.style.display = "none";
      }
      oracleAnswer.textContent = "The Oracle responds here.";
      positionNativeIOSTextPageFlow();
      addNativeIOSHomeVoiceLink();
    } else {
      setVoiceStatus(
        "Text entry ready",
        "Type your question below. You can tap Speak whenever you want to return to voice.",
        "ready"
      );
      oracleAnswer.textContent = "Text entry is ready. Type your question below, or tap Speak to ask aloud.";
    }

    if (seekerInput && typeof seekerInput.focus === "function") {
      seekerInput.focus();
    }
    focusTempleConversationForNativeEntry();
  }

  async function applyNativeVoiceEntryMode() {
    if (applyNativeIOSWebVoiceSuppression()) {
      oracleAnswer.textContent = "The Oracle responds here.";
      focusTempleConversationForNativeEntry();
      return;
    }

    setVoiceStatus(
      "Voice entry ready",
      "If prompted, allow microphone access. The Temple listens while you speak and stops automatically after silence.",
      "notice"
    );
    oracleAnswer.textContent = "🎙 Voice entry is ready. If prompted, allow microphone access. You can switch to text entry below.";
    focusTempleConversationForNativeEntry();

    try {
      await startVoiceRecording();
    } catch (err) {
      stopVoiceTracks();
      resetVoiceButton();
      const recoveryMessage = getMicrophoneRecoveryMessage(err);
      setVoiceStatus("Tap Speak to begin", recoveryMessage, "notice");
      oracleAnswer.textContent = "Voice entry is ready. Tap Speak to begin, or type your question below.";
    }
  }

  if (isNativeIOSLaunch && nativeEntryMode === "voice") {
    window.setTimeout(function () {
      applyNativeVoiceEntryMode();
    }, 700);
  } else if (isNativeIOSLaunch && nativeEntryMode === "text") {
    window.setTimeout(function () {
      applyNativeTextEntryMode();
    }, 500);
  }

  const menuToggle = document.getElementById("menuToggle");
  const mainMenu = document.getElementById("mainMenu");
  const menuAnonymous = document.getElementById("menuAnonymous");
  const menuAuthenticated = document.getElementById("menuAuthenticated");

  const userDisplayName = document.getElementById("userDisplayName");

  const loginBtn = document.getElementById("loginBtn");
  const registerBtn = document.getElementById("registerBtn");

  const nativeAuthMode = new URLSearchParams(window.location.search).get("auth");

  if (nativeAuthMode === "login" && loginBtn) {
    const cleanURL = new URL(window.location.href);
    cleanURL.searchParams.delete("auth");
    window.history.replaceState(
      {},
      "",
      cleanURL.pathname + cleanURL.search + cleanURL.hash
    );

    window.setTimeout(function() {
      loginBtn.click();
    }, 0);
  }
  const logoutBtn = document.getElementById("logoutBtn");
  const adminNavBtn = document.getElementById("adminNavBtn");
  const supportBtnAnonymous = document.getElementById("supportBtnAnonymous");
  const supportBtnAuthenticated = document.getElementById("supportBtnAuthenticated");
  const forgotPasswordLink = document.getElementById("forgotPasswordLink");

  const supportModal = document.getElementById("supportModal");
  const supportStatusLine = document.getElementById("supportStatusLine");
  const supportAuthPrompt = document.getElementById("supportAuthPrompt");
  const templeContributionBtn = document.getElementById("templeContributionBtn");
  const supportCheckoutButtons = document.querySelectorAll(".support-checkout-btn");

  const TEMPLE_CONTRIBUTION_URL = "https://buy.stripe.com/00wfZ98ur8EldDr7kjaEE00";
  const APPLE_SEEKER_MONTHLY_PRODUCT_ID = "ai.godincorporated.seeker.monthly";
  let pendingAppleStoreKitButton = null;
  let pendingAppleStoreKitOriginalText = "";
  let currentIdentity = null;

  function isNativeIOSApp() {
    return Boolean(
      (window.GodIncNativeIOS && window.GodIncNativeIOS.storeKit) ||
      /GodIncorporatedIOSApp/i.test(window.navigator.userAgent || "")
    );
  }

  function isAppleIAPVisibleSupportButton(button) {
    return Boolean(
      button &&
      button.dataset.planCode === "seeker" &&
      button.dataset.supportMode === "monthly_recurring"
    );
  }

  function resetPendingAppleStoreKitButton() {
    if (pendingAppleStoreKitButton) {
      pendingAppleStoreKitButton.disabled = false;
      pendingAppleStoreKitButton.textContent = pendingAppleStoreKitOriginalText || "Subscribe with Apple - $0.99/month";
    }
    pendingAppleStoreKitButton = null;
    pendingAppleStoreKitOriginalText = "";
  }

  window.addEventListener("godIncStoreKitPurchase", async function(event) {
    const detail = event.detail || {};
    resetPendingAppleStoreKitButton();

    if (detail.status === "success") {
      closeModal(supportModal);
      showFeedbackModal(
        "Apple purchase received. Your support access will refresh after Apple confirms the subscription.",
        [],
        "Support Received"
      );
      await updateIdentityDisplay();
      return;
    }

    if (detail.status === "cancelled") {
      showFeedbackModal("Apple purchase was cancelled.", [], "Temple Notice");
      return;
    }

    if (detail.status === "pending") {
      showFeedbackModal("Apple purchase is pending approval.", [], "Temple Notice");
      return;
    }

    showFeedbackModal(detail.message || "Apple in-app purchase could not be completed.", [], "Temple Notice");
  });

  window.addEventListener("godIncNativeReady", function() {
    applyNativeIOSWebVoiceSuppression();
    applyNativeIOSSupportGate(Boolean(currentIdentity && currentIdentity.authenticated));
  });

  // Phase 4.2.1: Modal elements
const loginModal = document.getElementById("loginModal");
const registerModal = document.getElementById("registerModal");
const loginForm = document.getElementById("loginForm");
const registerForm = document.getElementById("registerForm");
const closeButtons = document.querySelectorAll(".close");

// 🔐 Force Enter key in password field to submit login form
document.getElementById("loginPassword").addEventListener("keydown", function(e) {
  if (e.key === "Enter") {
    e.preventDefault();
    loginForm.dispatchEvent(new Event("submit", { cancelable: true }));
  }
});

  // Reset modals
  const resetRequestModal = document.getElementById("resetRequestModal");
  const resetPasswordModal = document.getElementById("resetPasswordModal");
  const resetRequestForm = document.getElementById("resetRequestForm");
  const resetPasswordForm = document.getElementById("resetPasswordForm");

  if (feedbackOkBtn) {
    feedbackOkBtn.addEventListener("click", function() {
      const action = feedbackModalAction;
      closeModal(feedbackModal);

      if (action === "create_account") {
        clearAuthErrors();
        openModal(registerModal);
      }
    });
  }

  function openModal(modal) {
    if (!modal) return;
    modal.style.display = "block";
    modal.setAttribute("aria-hidden", "false");
  }

  function closeModal(modal) {
    if (!modal) return;
    modal.style.display = "none";
    modal.setAttribute("aria-hidden", "true");
  }

  function showMenuSection(element, displayValue = "flex") {
    if (!element) return;
    element.hidden = false;
    element.style.display = displayValue;
  }

  function hideMenuSection(element) {
    if (!element) return;
    element.hidden = true;
    element.style.display = "none";
  }

  function setAuthenticatedMenuState(isAuthenticated) {
    if (isAuthenticated) {
      hideMenuSection(menuAnonymous);
      showMenuSection(menuAuthenticated, "flex");
    } else {
      showMenuSection(menuAnonymous, "flex");
      hideMenuSection(menuAuthenticated);
    }
  }

  function updateMenuToggleIdentity(identity) {
    if (!menuToggle) return;

    // Mobile launch polish:
    // keep the toggle as a compact hamburger icon in all identity states.
    // The signed-in display name remains inside the opened menu at #userDisplayName.
    menuToggle.textContent = "☰";

    if (identity && identity.authenticated) {
      const fullName = (identity.display_name || "Account").trim();
      menuToggle.setAttribute("aria-label", "Open account menu for " + fullName);
      menuToggle.title = fullName;
    } else {
      menuToggle.setAttribute("aria-label", "Open menu");
      menuToggle.removeAttribute("title");
    }
  }

  function updateAdminNav(identity) {
    if (!adminNavBtn) return;
    const isAdmin = Boolean(identity && identity.authenticated && ["admin", "owner"].includes(identity.role));
    adminNavBtn.hidden = !isAdmin;
    adminNavBtn.style.display = isAdmin ? "" : "none";
  }

  if (resetToken && window.location.pathname === "/auth/reset-password") {
    document.getElementById("resetToken").value = resetToken;
    openModal(resetPasswordModal);
  }

  // Error elements
  const loginError = document.getElementById("loginError");
  const registerError = document.getElementById("registerError");
  const resetRequestError = document.getElementById("resetRequestError");
  const resetPasswordError = document.getElementById("resetPasswordError");

  function clearAuthErrors() {
    loginError.textContent = "";
    registerError.textContent = "";
    resetRequestError.textContent = "";
    resetPasswordError.textContent = "";
  }

  function closeAllModals() {
    closeModal(loginModal);
    closeModal(registerModal);
    closeModal(resetRequestModal);
    closeModal(resetPasswordModal);
    closeModal(supportModal);
    closeModal(feedbackModal);
    feedbackModalAction = "ok";
    if (feedbackOkBtn) {
      feedbackOkBtn.textContent = "OK";
    }
    clearAuthErrors();
  }

  function planLabelFromCode(planCode) {
    const labels = {
      seeker: "Seeker",
      magister: "Magus",
      sovereign: "Sovereign",
      philosophus: "Philosopher",
      theoricus: "Theosopher"
    };
    return labels[planCode] || planCode || "Selected";
  }

  function supportModeLabel(mode) {
    if (mode === "annual_recurring") return "yearly";
    if (mode === "monthly_recurring") return "monthly";
    return mode || "support";
  }

  function applyNativeIOSSupportGate(authenticated = false) {
    if (!isNativeIOSApp()) return;

    if (selectedSupportIntent) {
      selectedSupportIntent.planCode = "seeker";
      selectedSupportIntent.supportMode = "monthly_recurring";
    }

    supportCheckoutButtons.forEach((button) => {
      const visibleAppleButton = isAppleIAPVisibleSupportButton(button);
      const card = button.closest(".support-card");

      if (visibleAppleButton) {
        button.hidden = false;
        button.style.display = "";
        button.disabled = !authenticated;
        button.textContent = "Subscribe with Apple - $0.99/month";
        button.dataset.storekitProductId = APPLE_SEEKER_MONTHLY_PRODUCT_ID;

        if (card) {
          card.hidden = false;
          card.style.display = "";
        }
      } else {
        button.hidden = true;
        button.style.display = "none";

        if (card && button.dataset.planCode !== "seeker") {
          card.hidden = true;
          card.style.display = "none";
        }
      }
    });

    if (templeContributionBtn) {
      const contributionCard = templeContributionBtn.closest(".support-card");
      templeContributionBtn.hidden = true;
      templeContributionBtn.style.display = "none";
      if (contributionCard) {
        contributionCard.hidden = true;
        contributionCard.style.display = "none";
      }
    }
  }

  function applySupportIntentSelection(focusSelection = false) {
    let selectedButton = null;

    supportCheckoutButtons.forEach((button) => {
      const matches = Boolean(
        selectedSupportIntent.planCode &&
        selectedSupportIntent.supportMode &&
        button.dataset.planCode === selectedSupportIntent.planCode &&
        button.dataset.supportMode === selectedSupportIntent.supportMode
      );

      button.classList.toggle("support-selected", matches);

      if (matches) {
        selectedButton = button;
      }
    });

    if (focusSelection && selectedButton) {
      window.setTimeout(() => {
        selectedButton.scrollIntoView({ block: "center", behavior: "smooth" });
        if (!selectedButton.disabled) {
          selectedButton.focus();
        }
      }, 80);
    }

    return selectedButton;
  }

  function renderSupportModal() {
    const authenticated = Boolean(currentIdentity && currentIdentity.authenticated);

    if (authenticated) {
      const currentLabel = currentIdentity.current_access_label || currentIdentity.plan_code || "Pilgrim";
      const questionLimit = currentIdentity.usage?.question_limit_display ?? currentIdentity.usage?.question_limit ?? "0";
      const memoryDepth = currentIdentity.memory_depth ?? "entire path";
      supportStatusLine.textContent =
        "You currently stand at " + currentLabel + ". This path offers " + questionLimit + " questions in the current window and memory depth " + memoryDepth + ".";
      supportAuthPrompt.hidden = true;
      supportAuthPrompt.style.display = "none";
    } else {
      supportStatusLine.textContent =
        "Support unlocks deeper dialogue continuity, stronger seeker memory, and longer access paths.";
      supportAuthPrompt.hidden = false;
      supportAuthPrompt.style.display = "block";
    }

    supportCheckoutButtons.forEach((button) => {
      button.disabled = !authenticated;
    });

    applyNativeIOSSupportGate(authenticated);

    const selectedButton = applySupportIntentSelection(false);
    if (selectedButton && selectedSupportIntent.planCode && selectedSupportIntent.supportMode) {
      const intentLabel = planLabelFromCode(selectedSupportIntent.planCode) + " " + supportModeLabel(selectedSupportIntent.supportMode);
      if (authenticated) {
        supportStatusLine.textContent += " Selected path: " + intentLabel + ". Continue with the highlighted button below.";
      } else {
        supportStatusLine.textContent += " Selected path: " + intentLabel + ". Log in or create an account, then use the highlighted button below.";
      }
    }
  }

  function launchAppleStoreKitCheckout(planCode, supportMode, button) {
    if (!currentIdentity || !currentIdentity.authenticated) {
      closeModal(supportModal);
      openModal(loginModal);
      return;
    }

    if (!isAppleIAPVisibleSupportButton(button)) {
      showFeedbackModal("Only Seeker Monthly is available in the iOS app for this review build.", [], "Temple Notice");
      return;
    }

    const handler = window.webkit?.messageHandlers?.templeStoreKit;
    if (!handler) {
      showFeedbackModal("Apple in-app purchase is available only inside the iOS app.", [], "Temple Notice");
      return;
    }

    pendingAppleStoreKitButton = button;
    pendingAppleStoreKitOriginalText = button.textContent;
    button.disabled = true;
    button.textContent = "Opening Apple purchase...";

    handler.postMessage({
      product_id: APPLE_SEEKER_MONTHLY_PRODUCT_ID,
      plan_code: planCode,
      support_mode: supportMode
    });
  }

  async function launchStripeCheckout(planCode, supportMode, button) {
    if (!currentIdentity || !currentIdentity.authenticated) {
      closeModal(supportModal);
      openModal(loginModal);
      return;
    }

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "Opening checkout...";

    try {
      const response = await identityFetch("/billing/checkout-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plan_code: planCode,
          support_mode: supportMode
        })
      });

      const data = await safeReadJson(response);

      if (!response.ok) {
        throw new Error(data.error || data.detail || "Could not create a checkout session.");
      }

      if (Object.prototype.hasOwnProperty.call(data, "changed_subscription")) {
        closeModal(supportModal);
        showFeedbackModal(
          data.message || (data.changed_subscription ? "Support updated successfully." : "Your support is already on this active plan."),
          [],
          data.changed_subscription ? "Support Updated" : "Temple Notice"
        );
        await updateIdentityDisplay();
        button.disabled = false;
        button.textContent = originalText;
        return;
      }

      if (!data.checkout_url) {
        throw new Error("Checkout URL was not returned.");
      }

      window.location.href = data.checkout_url;
    } catch (err) {
      showFeedbackModal(err.message || "Support checkout failed.", [], "Temple Notice");
      button.disabled = false;
      button.textContent = originalText;
    }
  }

  // Toggle menu visibility
  menuToggle.addEventListener("click", function() {
    mainMenu.classList.toggle("show");
  });

  // Close menu when clicking outside
  document.addEventListener("click", function(e) {
    if (!menuToggle.contains(e.target) && !mainMenu.contains(e.target)) {
      mainMenu.classList.remove("show");
    }
  });

  // Modal event listeners
  loginBtn.addEventListener("click", function() {
    clearAuthErrors();
    openModal(loginModal);
    mainMenu.classList.remove("show");

    setTimeout(() => {
      document.getElementById("loginEmail").focus();
    }, 50);
  });

  forgotPasswordLink.addEventListener("click", function(e) {
    e.preventDefault();
    resetRequestError.textContent = "";
    closeModal(loginModal);
    openModal(resetRequestModal);
  });

  registerBtn.addEventListener("click", function() {
    clearAuthErrors();
    openModal(registerModal);
    mainMenu.classList.remove("show");
  });

  if (supportBtnAnonymous) {
    supportBtnAnonymous.addEventListener("click", function() {
      renderSupportModal();
      openModal(supportModal);
      applySupportIntentSelection(true);
      mainMenu.classList.remove("show");
    });
  }

  if (supportBtnAuthenticated) {
    supportBtnAuthenticated.addEventListener("click", function() {
      renderSupportModal();
      openModal(supportModal);
      applySupportIntentSelection(true);
      mainMenu.classList.remove("show");
    });
  }

  supportCheckoutButtons.forEach((button) => {
    button.addEventListener("click", function() {
      if (isNativeIOSApp()) {
        launchAppleStoreKitCheckout(
          button.dataset.planCode,
          button.dataset.supportMode,
          button
        );
        return;
      }

      launchStripeCheckout(
        button.dataset.planCode,
        button.dataset.supportMode,
        button
      );
    });
  });

  if (templeContributionBtn) {
    templeContributionBtn.addEventListener("click", function() {
      if (isNativeIOSApp()) {
        showFeedbackModal(
          "Temple Contribution is not available in the iOS app yet. Apple in-app support currently begins with Seeker Monthly.",
          [],
          "Temple Notice"
        );
        return;
      }

      if (TEMPLE_CONTRIBUTION_URL) {
        window.location.href = TEMPLE_CONTRIBUTION_URL;
      } else {
        showFeedbackModal("Temple contribution will be connected next. Support tiers are active now.", [], "Temple Notice");
      }
    });
  }

  closeButtons.forEach(btn => {
    btn.addEventListener("click", function() {
      closeAllModals();
    });
  });

  window.addEventListener("click", function(e) {
    if (
      e.target === loginModal ||
      e.target === registerModal ||
      e.target === resetRequestModal ||
      e.target === resetPasswordModal
    ) {
      closeAllModals();
    }
  });

  // Form submissions
  loginForm.addEventListener("submit", async function(e) {
    e.preventDefault();
    const email = document.getElementById("loginEmail").value.trim();
    const password = document.getElementById("loginPassword").value;
    if (!email || !password) return;

    // Clear previous error
    loginError.textContent = "";

    // Disable button
    const submitBtn = loginForm.querySelector("button");
    submitBtn.disabled = true;
    submitBtn.textContent = "Logging in...";

    try {
      const response = await identityFetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password })
      });
      const data = await safeReadJson(response);
      if (response.status === 401 || response.status === 403) {
        loginError.textContent = "Session expired or unauthorized. Please log in again.";
        updateIdentityDisplay();
        return;
      }
      if (response.ok) {
        // Success
        closeModal(loginModal);
        await updateIdentityDisplay();
        notifyNativeIOSAuthChanged();
      } else {
        // Error
        loginError.textContent = data.error || "Login failed";
      }
    } catch (err) {
      loginError.textContent = "Network error";
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Log In";
    }
  });

  registerForm.addEventListener("submit", async function(e) {
    e.preventDefault();
    const email = document.getElementById("registerEmail").value.trim();
    const displayName = document.getElementById("registerDisplayName").value.trim();
    const password = document.getElementById("registerPassword").value;
    if (!email || !displayName || !password) return;

    // Clear previous error
    registerError.textContent = "";

    // Disable button
    const submitBtn = registerForm.querySelector("button");
    submitBtn.disabled = true;
    submitBtn.textContent = "Creating account...";

    try {
      const response = await identityFetch("/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, display_name: displayName })
      });
      const data = await safeReadJson(response);
      if (response.status === 401 || response.status === 403) {
        registerError.textContent = "Session expired or unauthorized. Please log in again.";
        updateIdentityDisplay();
        return;
      }
      if (response.ok) {
        // Success
        closeModal(registerModal);
        showFeedbackModal("Account created. Please check your email and verify your account before logging in.", [], "Account Created");
        updateIdentityDisplay();
      } else {
        // Error
        registerError.textContent = data.error || "Registration failed";
      }
    } catch (err) {
      registerError.textContent = err.message || "Network error";
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Create Account";
    }
  });

  resetRequestForm.addEventListener("submit", async function(e) {
    e.preventDefault();
    const email = document.getElementById("resetEmail").value.trim();
    if (!email) return;

    // Clear previous error
    resetRequestError.textContent = "";

    // Disable button
    const submitBtn = resetRequestForm.querySelector("button");
    submitBtn.disabled = true;
    submitBtn.textContent = "Sending...";

    try {
      const response = await identityFetch("/auth/request-password-reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email })
      });
      const data = await safeReadJson(response);
      if (response.ok) {
        // Success
        closeModal(resetRequestModal);
        showFeedbackModal("If an account with that email exists, a reset link has been sent to that email address.", [], "Password Reset");
      } else {
        // Error
        resetRequestError.textContent = data.error || "Request failed";
      }
    } catch (err) {
      resetRequestError.textContent = "Network error";
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Send Reset Link";
    }
  });

  resetPasswordForm.addEventListener("submit", async function(e) {
    e.preventDefault();
    const token = document.getElementById("resetToken").value.trim();
    const newPassword = document.getElementById("newPassword").value;
    if (!token || !newPassword) return;

    resetPasswordError.textContent = "";

    const submitBtn = resetPasswordForm.querySelector("button");
    submitBtn.disabled = true;
    submitBtn.textContent = "Resetting...";

    try {
      const formData = new FormData();
      formData.append("token", token);
      formData.append("new_password", newPassword);

      const response = await identityFetch("/auth/reset-password", {
        method: "POST",
        body: formData
      });

      const data = await safeReadJson(response);

      if (response.ok) {
        closeModal(resetPasswordModal);
        showFeedbackModal("Password reset successfully. Please log in with your new password.", [], "Password Reset");
      } else {
        resetPasswordError.textContent = data.error || "Reset failed";
      }
    } catch (err) {
      resetPasswordError.textContent = "Network error";
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Reset Password";
    }
  });

  // Fetch and display identity info
  async function updateIdentityDisplay() {
    try {
      const response = await identityFetch("/me");
      const data = await safeReadJson(response);

      if (response.status === 401 || response.status === 403 || data.error) {
        visitorId = null;
        seekerId = localStorage.getItem("seeker_id") || null;
        setAuthenticatedMenuState(false);
        updateMenuToggleIdentity(null);
        updateAdminNav(null);
        renderOracleHelper(null);
        return;
      }

      currentIdentity = data;

      if (data.anonymous_user_id) {
        setVisitorId(data.anonymous_user_id);
      }

      if (data.authenticated) {
        setAuthenticatedMenuState(true);

        userDisplayName.textContent = data.display_name || "";
        updateMenuToggleIdentity(data);
        updateAdminNav(data);

        logoutBtn.onclick = async function() {
          await identityFetch("/auth/logout", { method: "POST" });
          notifyNativeIOSAuthChanged();
          location.reload();
        };
      } else {
        visitorId = data.anonymous_user_id || null;
        setAuthenticatedMenuState(false);
        updateMenuToggleIdentity(null);
        updateAdminNav(null);
      }

      renderSupportModal();
      renderOracleHelper(data);

      if (openSupportOnLoad && !supportOpenedFromQuery) {
        supportOpenedFromQuery = true;
        openModal(supportModal);
        applySupportIntentSelection(true);
      }
    } catch (err) {
      console.error("Failed to fetch identity:", err);
      visitorId = null;
      currentIdentity = null;
      setAuthenticatedMenuState(false);
      updateMenuToggleIdentity(null);
      updateAdminNav(null);
      renderSupportModal();
      renderOracleHelper(null);

      if (openSupportOnLoad && !supportOpenedFromQuery) {
        supportOpenedFromQuery = true;
        openModal(supportModal);
        applySupportIntentSelection(true);
      }
    }
  }

  // Update identity display on load
  updateIdentityDisplay();
});

/* Phase 10.10 Clean: voice choice message */
(function () {
  function updateVoiceChoiceMessage() {
    const voiceSelect = document.getElementById('voiceSelect');
    const choiceMessage = document.getElementById('voiceChoiceMessage');
    if (!voiceSelect || !choiceMessage) return;

    const current = (voiceSelect.value || 'Hathor').trim();

    choiceMessage.textContent = current === 'Moses'
      ? 'You have chosen Moses, aligned with Christian Canon.'
      : 'You have chosen Hathor, aligned with Egyptian Magick.';

    document.querySelectorAll('[data-voice-card]').forEach((card) => {
      const isSelected = card.getAttribute('data-voice-card') === current;
      card.classList.toggle('is-selected', isSelected);
      card.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
    });
  }

  function initVoiceChoiceMessage() {
    const voiceSelect = document.getElementById('voiceSelect');
    if (!voiceSelect) return;

    updateVoiceChoiceMessage();
    voiceSelect.addEventListener('change', updateVoiceChoiceMessage);

    document.querySelectorAll('[data-voice-card]').forEach((card) => {
      card.addEventListener('click', function () {
        const value = card.getAttribute('data-voice-card');
        if (value) {
          voiceSelect.value = value;
          voiceSelect.dispatchEvent(new Event('change', { bubbles: true }));
        }
        updateVoiceChoiceMessage();
      });
    });

    setTimeout(updateVoiceChoiceMessage, 150);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initVoiceChoiceMessage);
  } else {
    initVoiceChoiceMessage();
  }
})();
