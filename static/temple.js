document.addEventListener("DOMContentLoaded", function () {
  // Check for reset token in URL
  const urlParams = new URLSearchParams(window.location.search);
  const resetToken = urlParams.get("token");
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

  // Phase 3.1: Anonymous continuity and seeker identity
  let visitorId = null; // Will be set from backend
  let seekerId = localStorage.getItem("seeker_id") || null;

  // Safe JSON parsing helper
  async function safeReadJson(response) {
    const text = await response.text();
    const contentType = response.headers.get("content-type");
    if (contentType && contentType.includes("application/json")) {
      try {
        return JSON.parse(text);
      } catch (e) {
        return { error: text || "Request failed" };
      }
    } else {
      return { error: text || "Request failed" };
    }
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

    const response = await fetch("/ask", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.error || "Oracle request failed");
    }

    return await response.json();
  }

  // Fetch scroll count on load
  fetch("/scrolls")
    .then((res) => res.json())
    .then((data) => {
      scrollCount.textContent = data.count;
    });

  // Oracle selection helper text
  voiceSelect.addEventListener("change", function () {
    const selected = voiceSelect.value;
    if (selected === "Hathor") {
      oracleHelper.textContent = "Hathor speaks from Egyptian Magick.";
    } else if (selected === "Moses") {
      oracleHelper.textContent = "Moses speaks from the Christian Canon.";
    } else {
      oracleHelper.textContent = "";
    }
  });
  // Trigger initial helper text
  voiceSelect.dispatchEvent(new Event("change"));

  // Upload scroll
  scrollForm.addEventListener("submit", function (e) {
    e.preventDefault();
    const formData = new FormData(scrollForm);
    formData.append("anonymous_user_id", visitorId);
    if (seekerId) formData.append("seeker_id", seekerId);
    fetch("/upload_scroll", {
      method: "POST",
      body: formData,
    })
      .then((res) => res.json())
      .then((data) => {
        alert(data.message);
        scrollInput.value = ""; // Clear file input after upload
        return fetch("/scrolls");
      })
      .then((res) => res.json())
      .then((data) => {
        scrollCount.textContent = data.count;
      });
  });

  // Ask Oracle (text input)
  oracleForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    const question = seekerInput.value.trim();
    if (!question) return;
    const voice = voiceSelect.value;

    // Clear input and previous answer, show waiting message
    seekerInput.value = "";
    oracleAnswer.textContent = "🔮 Consulting the Oracle...";
    // Disable Ask button
    askButton.disabled = true;

    try {
      const data = await submitOracleQuestion(question, voice);
      if (data.answer) {
        oracleAnswer.textContent = data.answer;
      } else if (data.error) {
        oracleAnswer.textContent = "⚠️ Error: " + data.error;
      } else {
        oracleAnswer.textContent = "⚠️ No response received.";
      }
    } catch (err) {
      oracleAnswer.textContent = "⚠️ Error: " + err.message;
    } finally {
      askButton.disabled = false;
    }
  });

  // Voice input and TTS output
  speakButton.addEventListener("click", function () {
    if (!navigator.mediaDevices) {
      alert("🎤 Microphone not supported in this browser.");
      return;
    }

    speakButton.disabled = true;
    speakButton.textContent = "🎙 Listening...";
    oracleAnswer.textContent = "🔄 Transcribing...";

    navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
      const mediaRecorder = new MediaRecorder(stream);
      const chunks = [];

      mediaRecorder.ondataavailable = (e) => chunks.push(e.data);

      mediaRecorder.onstop = () => {
        const blob = new Blob(chunks, { type: "audio/webm" });
        const formData = new FormData();
        formData.append("file", blob, "voice_input.webm");
        formData.append("voice", voiceSelect.value);
        formData.append("anonymous_user_id", visitorId);
        if (seekerId) formData.append("seeker_id", seekerId);

        fetch("/whisper", {
          method: "POST",
          body: formData,
        })
          .then((res) => res.json())
          .then((data) => {
            oracleAnswer.textContent = data.answer || "⚠️ No response";
            seekerInput.value = "";

            if (data.audio_url) {
              // Create audio element instead of autoplay
              const audioContainer = document.createElement('div');
              audioContainer.innerHTML = '<audio controls><source src="' + data.audio_url + '" type="audio/mpeg"></audio>';
              oracleAnswer.appendChild(audioContainer);
            }
          })
          .catch((err) => {
            oracleAnswer.textContent = "⚠️ Error: " + err.message;
          })
          .finally(() => {
            speakButton.disabled = false;
            speakButton.textContent = "🎤 Speak";
          });
      };

      mediaRecorder.start();

      setTimeout(() => {
        mediaRecorder.stop();
        stream.getTracks().forEach((track) => track.stop());
      }, 5000); // 5 seconds recording
    });
  });

  const menuToggle = document.getElementById("menuToggle");
  const mainMenu = document.getElementById("mainMenu");
  const menuAnonymous = document.getElementById("menuAnonymous");
  const menuAuthenticated = document.getElementById("menuAuthenticated");

  const userDisplayName = document.getElementById("userDisplayName");
  const userCombinedTitle = document.getElementById("userCombinedTitle");
  const userPlanCode = document.getElementById("userPlanCode");
  const userScrollCount = document.getElementById("userScrollCount");
  const userMoneyDonated = document.getElementById("userMoneyDonated");
  const userDonationCount = document.getElementById("userDonationCount");
  const userQuestionsUsed = document.getElementById("userQuestionsUsed");
  const userQuestionLimit = document.getElementById("userQuestionLimit");
  const userQuestionsRemaining = document.getElementById("userQuestionsRemaining");
  const userEmailVerified = document.getElementById("userEmailVerified");

  const loginBtn = document.getElementById("loginBtn");
  const registerBtn = document.getElementById("registerBtn");
  const logoutBtn = document.getElementById("logoutBtn");
  const supportBtnAnonymous = document.getElementById("supportBtnAnonymous");
  const supportBtnAuthenticated = document.getElementById("supportBtnAuthenticated");
  const forgotPasswordLink = document.getElementById("forgotPasswordLink");

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

  if (resetToken && window.location.pathname === "/auth/reset-password") {
    document.getElementById("resetToken").value = resetToken;
    resetPasswordModal.style.display = "block";
  }

  // Error elements
  const loginError = document.getElementById("loginError");
  const registerError = document.getElementById("registerError");
  const resetRequestError = document.getElementById("resetRequestError");
  const resetPasswordError = document.getElementById("resetPasswordError");

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
    loginError.textContent = "";
    loginModal.style.display = "block";
    mainMenu.classList.remove("show");

    // Auto-focus email field so Enter works immediately
    setTimeout(() => {
      document.getElementById("loginEmail").focus();
    }, 50);
  });

  forgotPasswordLink.addEventListener("click", function(e) {
    e.preventDefault();
    resetRequestError.textContent = "";
    resetRequestModal.style.display = "block";
    loginModal.style.display = "none";
  });

  registerBtn.addEventListener("click", function() {
    registerError.textContent = "";
    registerModal.style.display = "block";
    mainMenu.classList.remove("show");
  });

  if (supportBtnAnonymous) {
    supportBtnAnonymous.addEventListener("click", function() {
      // Support functionality coming soon
    });
  }

  if (supportBtnAuthenticated) {
    supportBtnAuthenticated.addEventListener("click", function() {
      // Support functionality coming soon
    });
  }

  // Close modals
  closeButtons.forEach(btn => {
    btn.addEventListener("click", function() {
      loginModal.style.display = "none";
      registerModal.style.display = "none";
      resetRequestModal.style.display = "none";
      resetPasswordModal.style.display = "none";
      loginError.textContent = "";
      registerError.textContent = "";
      resetRequestError.textContent = "";
      resetPasswordError.textContent = "";
    });
  });

  // Close modal when clicking outside
  window.addEventListener("click", function(e) {
    if (e.target === loginModal) {
      loginModal.style.display = "none";
      loginError.textContent = "";
    }
    if (e.target === registerModal) {
      registerModal.style.display = "none";
      registerError.textContent = "";
    }
    if (e.target === resetRequestModal) {
      resetRequestModal.style.display = "none";
      resetRequestError.textContent = "";
    }
    if (e.target === resetPasswordModal) {
      resetPasswordModal.style.display = "none";
      resetPasswordError.textContent = "";
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
      const response = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
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
        loginModal.style.display = "none";
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
      const response = await fetch("/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
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
        registerModal.style.display = "none";
        alert("Account created. Please check your email and verify your account before logging in.");
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
      const response = await fetch("/auth/request-password-reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email })
      });
      const data = await safeReadJson(response);
      if (response.ok) {
        // Success
        resetRequestModal.style.display = "none";
        alert("If an account with that email exists, a reset link has been sent to that email address.");
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

      const response = await fetch("/auth/reset-password", {
        method: "POST",
        body: formData
      });

      const data = await safeReadJson(response);

      if (response.ok) {
        resetPasswordModal.style.display = "none";
        alert("Password reset successfully. Please log in with your new password.");
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
      const response = await fetch("/me", { credentials: "same-origin" });
      const data = await safeReadJson(response);
      if (response.status === 401 || response.status === 403 || data.error) {
        // Treat as anonymous
        visitorId = null;
        seekerId = localStorage.getItem("seeker_id") || null;
        menuAnonymous.style.display = "flex";
        menuAuthenticated.style.display = "none";
        return;
      }
      
      if (data.authenticated) {
        visitorId = data.anonymous_user_id || null;
        menuAnonymous.style.display = "none";
        menuAuthenticated.style.display = "flex";

        userDisplayName.textContent = data.display_name || "";
        userCombinedTitle.textContent = data.combined_title || "";
        userPlanCode.textContent = data.plan_code || "";
        userScrollCount.textContent = data.scrolls_donated ?? 0;
        userMoneyDonated.textContent = data.money_donated ?? 0;
        userDonationCount.textContent = data.donation_count ?? 0;
        userQuestionsUsed.textContent = data.usage?.questions_used ?? 0;
        userQuestionLimit.textContent = data.usage?.question_limit ?? 0;
        userQuestionsRemaining.textContent = data.usage?.questions_remaining ?? 0;
        userEmailVerified.textContent = data.email_verified ? "Yes" : "No";

        logoutBtn.onclick = async function() {
          await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
          location.reload();
        };
      } else {

        // Anonymous user
        visitorId = data.anonymous_user_id;
        menuAnonymous.style.display = "flex";
        menuAuthenticated.style.display = "none";
      }
    } catch (err) {
      console.error("Failed to fetch identity:", err);
      // Fallback to anonymous without id
      menuAnonymous.style.display = "flex";
      menuAuthenticated.style.display = "none";
    }
  }

  // Update identity display on load
  updateIdentityDisplay();
});
