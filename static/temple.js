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
      visitor_id: visitorId
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
    formData.append("visitor_id", visitorId);
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

  // Phase 3.1: Lightweight registration (optional)
  const registerForm = document.createElement("form");
  registerForm.id = "registerForm";
  registerForm.innerHTML = `
    <h3>Leave a name so the Temple can remember you.</h3>
    <input type="text" id="displayName" placeholder="one word name that will be solely yours." maxlength="50" style="width: 300px;">
    <button type="submit">Register</button>
    <p style="font-size: 0.9em; color: #666;">"Leaving a name allows the Temple to remember your questions and offerings. You may always remain anonymous if you wish."</p>
  `;
  document.body.appendChild(registerForm);

  registerForm.addEventListener("submit", function (e) {
    e.preventDefault();
    const displayName = document.getElementById("displayName").value.trim() || null;
    fetch("/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: displayName }),
    })
      .then((res) => res.json())
      .then((data) => {
        if (data.seeker_id) {
          seekerId = data.seeker_id;
          localStorage.setItem("seeker_id", seekerId);
          alert(data.message);
          registerForm.style.display = "none"; // Hide after registration
        } else {
          alert("Registration failed.");
        }
      })
      .catch((err) => {
        alert("Registration error: " + err.message);
      });
  });

  // Hide registration if already registered
  if (seekerId) {
    registerForm.style.display = "none";
  }
});
