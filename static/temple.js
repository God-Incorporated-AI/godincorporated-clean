document.addEventListener("DOMContentLoaded", function () {
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
  let visitorId = localStorage.getItem("visitor_id");
  if (!visitorId) {
    visitorId = crypto.randomUUID();
    localStorage.setItem("visitor_id", visitorId);
  }
  let seekerId = localStorage.getItem("seeker_id") || null;

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
        formData.append("visitor_id", visitorId);
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

  // Phase 4.2: Hamburger menu for auth and identity
  const menuToggle = document.getElementById("menuToggle");
  const mainMenu = document.getElementById("mainMenu");
  const menuAnonymous = document.getElementById("menuAnonymous");
  const menuAuthenticated = document.getElementById("menuAuthenticated");
  const userEmail = document.getElementById("userEmail");
  const usageCount = document.getElementById("usageCount");
  const loginBtn = document.getElementById("loginBtn");
  const registerBtn = document.getElementById("registerBtn");
  const logoutBtn = document.getElementById("logoutBtn");
  const supportBtn = document.getElementById("supportBtn");

  // Phase 4.2.1: Modal elements
  const loginModal = document.getElementById("loginModal");
  const registerModal = document.getElementById("registerModal");
  const loginForm = document.getElementById("loginForm");
  const registerForm = document.getElementById("registerForm");
  const closeButtons = document.querySelectorAll(".close");

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
    loginModal.style.display = "block";
    mainMenu.classList.remove("show");
  });

  registerBtn.addEventListener("click", function() {
    registerModal.style.display = "block";
    mainMenu.classList.remove("show");
  });

  supportBtn.addEventListener("click", function() {
    // Support functionality coming soon
  });

  // Close modals
  closeButtons.forEach(btn => {
    btn.addEventListener("click", function() {
      loginModal.style.display = "none";
      registerModal.style.display = "none";
    });
  });

  // Close modal when clicking outside
  window.addEventListener("click", function(e) {
    if (e.target === loginModal) {
      loginModal.style.display = "none";
    }
    if (e.target === registerModal) {
      registerModal.style.display = "none";
    }
  });

  // Form submissions (stubbed)
  loginForm.addEventListener("submit", async function(e) {
    e.preventDefault();
    const email = document.getElementById("loginEmail").value;
    const password = document.getElementById("loginPassword").value;
    // UI scaffolding - backend integration pending
  });

  registerForm.addEventListener("submit", async function(e) {
    e.preventDefault();
    const email = document.getElementById("registerEmail").value;
    const password = document.getElementById("registerPassword").value;
    // UI scaffolding - backend integration pending
  });

  // Fetch and display identity info
  async function updateIdentityDisplay() {
    try {
      const url = `/me?anonymous_user_id=${encodeURIComponent(visitorId)}`;
      const response = await fetch(url);
      const data = await response.json();
      
      if (data.authenticated) {
        // Show authenticated menu
        menuAnonymous.style.display = "none";
        menuAuthenticated.style.display = "flex";
        userEmail.textContent = data.email;
        usageCount.textContent = data.usage.questions_asked;
        
        // Logout handler
        logoutBtn.addEventListener("click", async function() {
          await fetch("/auth/logout", { method: "POST" });
          location.reload();
        });
      } else {
        // Show anonymous menu
        menuAnonymous.style.display = "flex";
        menuAuthenticated.style.display = "none";
      }
    } catch (err) {
      console.error("Failed to fetch identity:", err);
      // Fallback to anonymous
      menuAnonymous.style.display = "flex";
      menuAuthenticated.style.display = "none";
    }
  }

  // Update identity display on load
  updateIdentityDisplay();
});
