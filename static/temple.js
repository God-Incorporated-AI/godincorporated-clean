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

  const feedbackModal = document.getElementById("feedbackModal");
  const feedbackTitle = document.getElementById("feedbackTitle");
  const feedbackBody = document.getElementById("feedbackBody");
  const feedbackOkBtn = document.getElementById("feedbackOkBtn");

  let feedbackModalAction = "ok";

  const ANON_STORAGE_KEY = "godinc_anon_id";
  const ORACLE_VOICE_STORAGE_KEY = "godinc_oracle_voice";

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

  function normalizeUploadFeedback(response, data) {
    const nudges = Array.isArray(data?.continuity_nudges) ? data.continuity_nudges : [];

    if ((response && response.status >= 500) || looksLikeHtmlResponse(data)) {
      return {
        message: "The Temple could not read that scroll right now. This file may be image-heavy or photo-scanned, and the live upload path could not process it reliably. Please try a text-based PDF, TXT, DOCX, or an OCR-processed scan.",
        nudges,
        title: "Scroll Upload"
      };
    }

    return {
      message: data?.error || data?.detail || data?.message || (typeof data === "string" && data.trim() ? data.trim() : "Scroll upload failed."),
      nudges,
      title: "Temple Notice"
    };
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

  // Restore the last Oracle voice used on this browser
  const validOracleVoices = new Set(Array.from(voiceSelect.options).map((option) => option.value));
  const savedOracleVoice = localStorage.getItem(ORACLE_VOICE_STORAGE_KEY);
  if (savedOracleVoice && validOracleVoices.has(savedOracleVoice)) {
    voiceSelect.value = savedOracleVoice;
  }

  // Oracle selection helper text
  voiceSelect.addEventListener("change", function () {
    const selected = voiceSelect.value;
    localStorage.setItem(ORACLE_VOICE_STORAGE_KEY, selected);
    renderOracleHelper(null);
  });
  // Trigger initial helper text
  voiceSelect.dispatchEvent(new Event("change"));

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

      const data = await safeReadJson(response);
      const continuityNudges = Array.isArray(data?.continuity_nudges) ? data.continuity_nudges : [];
      const shouldOfferClaim = !currentIdentity?.authenticated && (Boolean(data?.claim_required) || continuityNudges.length > 0);

      if (!response.ok) {
        const normalized = normalizeUploadFeedback(response, data);
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

      showFeedbackModal(
        data.message || "📜 Your scroll has been uploaded.",
        continuityNudges,
        "Temple Notice",
        { showCreateAccount: shouldOfferClaim }
      );
      scrollInput.value = "";

      const countResponse = await fetch("/scrolls");
      const countData = await safeReadJson(countResponse);

      if (countResponse.ok && typeof countData.count !== "undefined") {
        scrollCount.textContent = countData.count;
      }
    } catch (err) {
      showFeedbackModal(err.message || "Scroll upload failed.", [], "Temple Notice");
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
      }
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

  function stopVoiceTracks() {
    if (voiceStream) {
      voiceStream.getTracks().forEach((track) => track.stop());
      voiceStream = null;
    }
  }

  function resetVoiceButton() {
    voiceIsRecording = false;
    speakButton.disabled = false;
    speakButton.textContent = "🎤 Speak";
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
    };

    audio.onended = setReplayReady;

    audio.onpause = function () {
      if (audio.ended) {
        setReplayReady();
      }
    };

    try {
      await audio.play();
    } catch (err) {
      setReplayReady();
    }
  }

  async function submitVoiceRecording(blob) {
    const selectedVoice = voiceSelect.value;
    const formData = new FormData();
    formData.append("file", blob, "voice_input.webm");
    formData.append("voice", selectedVoice);

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
    oracleAnswer.textContent = "You said: " + spokenQuestion + "\n\n🔮 Consulting the Oracle...";

    const answerData = await submitOracleVoiceQuestion(spokenQuestion, selectedVoice);

    if (answerData.answer) {
      oracleAnswer.textContent = "You said: " + spokenQuestion + "\n\n" + answerData.answer;
      await updateIdentityDisplay();
    } else if (answerData.error) {
      oracleAnswer.textContent = "⚠️ Error: " + answerData.error;
      return;
    } else {
      oracleAnswer.textContent = "⚠️ No response received.";
      return;
    }

    const replayButton = ensureReplayVoiceButton();
    replayButton.style.display = "inline-block";
    replayButton.textContent = "Preparing Oracle Voice...";
    replayButton.disabled = true;

    try {
      const ttsData = await prepareOracleVoice(answerData.answer, selectedVoice);
      if (ttsData.audio_url) {
        await playOracleAudio(ttsData.audio_url);
      } else {
        replayButton.textContent = "Voice unavailable";
        replayButton.disabled = true;
      }
    } catch (err) {
      replayButton.textContent = "Voice unavailable";
      replayButton.disabled = true;
    }
  }

  async function startVoiceRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      alert("🎤 Microphone not supported in this browser.");
      return;
    }

    const replayButton = ensureReplayVoiceButton();
    replayButton.style.display = "none";

    voiceChunks = [];
    voiceStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    voiceRecorder = new MediaRecorder(voiceStream);

    voiceRecorder.ondataavailable = function (event) {
      if (event.data && event.data.size > 0) {
        voiceChunks.push(event.data);
      }
    };

    voiceRecorder.onstop = async function () {
      stopVoiceTracks();

      const blob = new Blob(voiceChunks, { type: "audio/webm" });
      voiceChunks = [];

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
    speakButton.disabled = false;
    speakButton.textContent = "⏹ Stop";
    oracleAnswer.textContent = "🎙 Listening... Tap Stop when you are finished.";
  }

  speakButton.addEventListener("click", async function () {
    if (voiceIsRecording && voiceRecorder && voiceRecorder.state === "recording") {
      speakButton.disabled = true;
      speakButton.textContent = "🔄 Transcribing...";
      oracleAnswer.textContent = "🔄 Transcribing...";
      voiceRecorder.stop();
      return;
    }

    try {
      await startVoiceRecording();
    } catch (err) {
      stopVoiceTracks();
      resetVoiceButton();
      oracleAnswer.textContent = "⚠️ Microphone error: " + (err.message || "Could not start recording.");
    }
  });

  const menuToggle = document.getElementById("menuToggle");
  const mainMenu = document.getElementById("mainMenu");
  const menuAnonymous = document.getElementById("menuAnonymous");
  const menuAuthenticated = document.getElementById("menuAuthenticated");

  const userDisplayName = document.getElementById("userDisplayName");

  const loginBtn = document.getElementById("loginBtn");
  const registerBtn = document.getElementById("registerBtn");
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
  let currentIdentity = null;

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

    if (identity && identity.authenticated) {
      const fullName = (identity.display_name || "Account").trim();
      const shortName = fullName.length > 14 ? fullName.slice(0, 14) + "..." : fullName;
      menuToggle.textContent = shortName + " ▾";
      menuToggle.setAttribute("aria-label", "Open account menu for " + fullName);
      menuToggle.title = fullName;
    } else {
      menuToggle.textContent = "☰";
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

  async function launchStripeCheckout(planCode, supportMode, button) {
    if (!currentIdentity || !currentIdentity.authenticated) {
      closeModal(supportModal);
      openModal(loginModal);
      return;
    }

    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "Opening Stripe...";

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
        throw new Error(data.error || data.detail || "Could not create Stripe checkout session.");
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
        throw new Error("Stripe checkout URL was not returned.");
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
      launchStripeCheckout(
        button.dataset.planCode,
        button.dataset.supportMode,
        button
      );
    });
  });

  if (templeContributionBtn) {
    templeContributionBtn.addEventListener("click", function() {
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
        updateIdentityDisplay();
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
      card.classList.toggle('is-selected', card.getAttribute('data-voice-card') === current);
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
