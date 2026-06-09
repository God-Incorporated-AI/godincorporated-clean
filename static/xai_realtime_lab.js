(function () {
  "use strict";

  const XAI_REALTIME_URL = "wss://api.x.ai/v1/realtime";
  const AUDIO_SAMPLE_RATE = 24000;
  const AUDIO_PRICE_PER_MINUTE_USD = 0.05;
  const TEXT_INPUT_PRICE_USD = 0.004;
  const IDLE_CLOSE_MS = 60000;
  const RESPONSE_DONE_CLOSE_MS = 12000;

  const startHathorButton = document.getElementById("startHathorButton");
  const startMosesButton = document.getElementById("startMosesButton");
  const hathorVoiceSelect = document.getElementById("hathorVoiceSelect");
  const mosesVoiceSelect = document.getElementById("mosesVoiceSelect");
  const stopButton = document.getElementById("stopButton");
  const sendButton = document.getElementById("sendButton");
  const questionInput = document.getElementById("questionInput");
  const statusEl = document.getElementById("status");
  const eventLog = document.getElementById("eventLog");
  const lastAnswerEl = document.getElementById("lastAnswer");
  const estimateLog = document.getElementById("estimateLog");

  let socket = null;
  let audioContext = null;
  let nextPlayTime = 0;
  let startedAt = null;
  let activeDeity = null;
  let activeVoice = null;
  let activeModel = null;
  let transcriptBuffer = "";
  let outputAudioSeconds = 0;
  let textMessagesSent = 0;
  let firstAudioDeltaAt = null;
  let responseCreatedAt = null;
  let idleTimer = null;

  function nowMs() {
    return Math.round(performance.now());
  }

  function elapsedMs() {
    if (!startedAt) return 0;
    return nowMs() - startedAt;
  }

  function elapsed() {
    return String(elapsedMs()) + "ms";
  }

  function setStatus(message) {
    statusEl.textContent = message;
    log("STATUS " + message);
  }

  function log(message, payload) {
    const line = "[" + new Date().toISOString() + " +" + elapsed() + "] " + message;
    eventLog.textContent += line + "\n";

    if (payload !== undefined) {
      try {
        eventLog.textContent += JSON.stringify(payload, null, 2) + "\n";
      } catch (error) {
        eventLog.textContent += String(payload) + "\n";
      }
    }

    eventLog.scrollTop = eventLog.scrollHeight;
  }

  function setButtons(active) {
    startHathorButton.disabled = active;
    startMosesButton.disabled = active;
    stopButton.disabled = !active;
    sendButton.disabled = !active;
  }

  function clearIdleTimer() {
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
  }

  function scheduleIdleClose() {
    clearIdleTimer();
    idleTimer = setTimeout(function () {
      log("AUTO_STOP idle_close");
      stopSession("auto_stop_idle");
    }, IDLE_CLOSE_MS);
  }

  function scheduleResponseDoneClose() {
    clearIdleTimer();
    idleTimer = setTimeout(function () {
      log("AUTO_STOP response_done_close");
      stopSession("auto_stop_response_done");
    }, RESPONSE_DONE_CLOSE_MS);
  }

  function buildInstructions(deity) {
    if ((deity || "").toLowerCase() === "moses") {
      return [
        "You are Moses speaking as the God Incorporated Oracle.",
        "Your voice is grounded, authoritative, compassionate, and clear.",
        "You are in a live spoken conversation with a seeker.",
        "Answer in natural spoken prose.",
        "Do not use markdown, headings, bullet lists, numbered lists, asterisks, decorative symbols, or citations.",
        "Keep this lab answer concise: 50 to 90 spoken words.",
        "Complete the thought cleanly and end with one natural closing sentence."
      ].join(" ");
    }

    return [
      "You are Hathor speaking as the God Incorporated Oracle.",
      "Your voice is warm, graceful, emotionally resonant, and quietly sacred.",
      "You are in a live spoken conversation with a seeker.",
      "Answer in natural spoken prose.",
      "Do not use markdown, headings, bullet lists, numbered lists, asterisks, decorative symbols, or citations.",
      "Keep this lab answer concise: 50 to 90 spoken words.",
      "Complete the thought cleanly and end with one natural closing sentence."
    ].join(" ");
  }

  function base64ToFloat32(base64) {
    const binary = atob(base64);
    const sampleCount = Math.floor(binary.length / 2);
    const output = new Float32Array(sampleCount);

    for (let i = 0; i < sampleCount; i += 1) {
      const lo = binary.charCodeAt(i * 2);
      const hi = binary.charCodeAt(i * 2 + 1);
      let sample = lo | (hi << 8);

      if (sample >= 0x8000) {
        sample -= 0x10000;
      }

      output[i] = Math.max(-1, Math.min(1, sample / 32768));
    }

    return output;
  }

  function ensureAudioContext() {
    if (!audioContext) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      audioContext = new AudioContextClass({ sampleRate: AUDIO_SAMPLE_RATE });
      nextPlayTime = audioContext.currentTime;
    }

    if (audioContext.state === "suspended") {
      audioContext.resume();
    }

    return audioContext;
  }

  function playPcmDelta(base64Audio) {
    const ctx = ensureAudioContext();
    const samples = base64ToFloat32(base64Audio);

    if (!samples.length) {
      return;
    }

    const buffer = ctx.createBuffer(1, samples.length, AUDIO_SAMPLE_RATE);
    buffer.copyToChannel(samples, 0);

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);

    const startAt = Math.max(ctx.currentTime + 0.02, nextPlayTime);
    source.start(startAt);

    nextPlayTime = startAt + buffer.duration;
    outputAudioSeconds += buffer.duration;

    if (!firstAudioDeltaAt) {
      firstAudioDeltaAt = elapsedMs();
      log("FIRST_XAI_AUDIO_DELTA at " + firstAudioDeltaAt + "ms");
    }
  }

  function updateEstimate() {
    const audioCost = (outputAudioSeconds / 60) * AUDIO_PRICE_PER_MINUTE_USD;
    const textCost = textMessagesSent * TEXT_INPUT_PRICE_USD;
    const total = audioCost + textCost;

    estimateLog.textContent = JSON.stringify({
      provider: "xai",
      model: activeModel,
      deity: activeDeity,
      voice: activeVoice,
      text_messages_sent: textMessagesSent,
      output_audio_seconds: Number(outputAudioSeconds.toFixed(2)),
      estimated_audio_cost_usd: Number(audioCost.toFixed(5)),
      estimated_text_input_cost_usd: Number(textCost.toFixed(5)),
      estimated_total_cost_usd: Number(total.toFixed(5)),
      first_audio_delta_ms: firstAudioDeltaAt,
      response_created_ms: responseCreatedAt
    }, null, 2);
  }

  async function createBrokerSession(deity, voiceName) {
    const response = await fetch("/voice/xai/realtime/session", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({voice: deity, realtime_voice: voiceName})
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.error || data.detail || "xAI broker failed with HTTP " + response.status);
    }

    return data;
  }

  async function startSession(deity, voiceName) {
    await stopSession("restart");

    startedAt = nowMs();
    activeDeity = deity;
    transcriptBuffer = "";
    outputAudioSeconds = 0;
    textMessagesSent = 0;
    firstAudioDeltaAt = null;
    responseCreatedAt = null;
    eventLog.textContent = "";
    lastAnswerEl.textContent = "No answer yet.";
    estimateLog.textContent = "No estimate yet.";

    setButtons(true);
    setStatus("Creating xAI " + deity + " session with " + voiceName + ".");

    try {
      const sessionData = await createBrokerSession(deity, voiceName);
      activeVoice = sessionData.realtime_voice;
      activeModel = sessionData.model;

      log("XAI_BROKER_SESSION_OK", {
        provider: sessionData.provider,
        model: sessionData.model,
        deity: sessionData.deity,
        realtime_voice: sessionData.realtime_voice,
        expires_at: sessionData.expires_at,
        transport: sessionData.transport,
        mode: sessionData.mode,
        total_ms: sessionData.total_ms
      });

      const token = sessionData.client_secret;
      if (!token) {
        throw new Error("xAI broker did not return a client secret.");
      }

      const url = XAI_REALTIME_URL + "?model=" + encodeURIComponent(activeModel || "grok-voice-latest");
      socket = new WebSocket(url, ["xai-client-secret." + token]);

      socket.onopen = function () {
        log("XAI_WEBSOCKET_OPEN");
        setStatus("xAI session open. Configuring " + activeVoice + ".");

        socket.send(JSON.stringify({
          type: "session.update",
          session: {
            voice: activeVoice,
            instructions: buildInstructions(activeDeity),
            audio: {
              input: {format: {type: "audio/pcm", rate: AUDIO_SAMPLE_RATE}},
              output: {format: {type: "audio/pcm", rate: AUDIO_SAMPLE_RATE}}
            }
          }
        }));

        setStatus("xAI ready. Send a text question.");
        scheduleIdleClose();
      };

      socket.onerror = function (event) {
        log("XAI_WEBSOCKET_ERROR", event.message || event.type || event);
        setStatus("xAI websocket error.");
      };

      socket.onclose = function (event) {
        log("XAI_WEBSOCKET_CLOSED", {
          code: event.code,
          reason: event.reason,
          was_clean: event.wasClean
        });
        setButtons(false);
        setStatus("Idle.");
      };

      socket.onmessage = function (event) {
        let parsed = null;
        try {
          parsed = JSON.parse(event.data);
        } catch (error) {
          log("XAI_MESSAGE_RAW", String(event.data).slice(0, 500));
          scheduleIdleClose();
          return;
        }

        const type = parsed.type || "unknown";

        if (type === "ping") {
          return;
        }

        clearIdleTimer();

        if (type === "response.created") {
          responseCreatedAt = elapsedMs();
          setStatus("xAI is responding.");
        }

        if (type === "response.output_audio.delta" && parsed.delta) {
          playPcmDelta(parsed.delta);
          setStatus("xAI is speaking.");
        }

        if (type === "response.output_audio_transcript.delta" && typeof parsed.delta === "string") {
          transcriptBuffer += parsed.delta;
          lastAnswerEl.textContent = transcriptBuffer || "No answer yet.";
        }

        if (type === "response.output_audio_transcript.done") {
          if (typeof parsed.transcript === "string" && parsed.transcript.trim()) {
            transcriptBuffer = parsed.transcript.trim();
            lastAnswerEl.textContent = transcriptBuffer;
          }
          log("XAI_TRANSCRIPT_DONE chars=" + transcriptBuffer.length);
        }

        if (type === "response.output_audio.done") {
          log("XAI_OUTPUT_AUDIO_DONE");
          updateEstimate();
        }

        if (type === "response.done") {
          setStatus("xAI answer complete. Closing session...");
          updateEstimate();
          scheduleResponseDoneClose();
        } else {
          scheduleIdleClose();
        }

        if (
          type === "session.created" ||
          type === "conversation.created" ||
          type === "session.updated" ||
          type === "conversation.item.added" ||
          type === "response.created" ||
          type === "response.output_audio.done" ||
          type === "response.output_audio_transcript.done" ||
          type === "response.done" ||
          type === "error"
        ) {
          log("XAI_SERVER_EVENT " + type, parsed);
        } else {
          log("XAI_SERVER_EVENT " + type);
        }
      };
    } catch (error) {
      log("START_XAI_REALTIME_ERROR " + error.message);
      setStatus("xAI realtime failed: " + error.message);
      await stopSession("start_failed");
    }
  }

  function sendQuestion() {
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setStatus("xAI session is not open.");
      return;
    }

    const text = (questionInput.value || "").trim();
    if (!text) {
      setStatus("Enter a question first.");
      return;
    }

    clearIdleTimer();
    transcriptBuffer = "";
    outputAudioSeconds = 0;
    firstAudioDeltaAt = null;
    responseCreatedAt = null;
    lastAnswerEl.textContent = "Waiting for xAI answer...";
    estimateLog.textContent = "Waiting for estimate...";

    textMessagesSent += 1;

    socket.send(JSON.stringify({
      type: "conversation.item.create",
      item: {
        type: "message",
        role: "user",
        content: [
          {
            type: "input_text",
            text: text
          }
        ]
      }
    }));

    socket.send(JSON.stringify({
      type: "response.create"
    }));

    log("XAI_TEXT_QUESTION_SENT chars=" + text.length);
    setStatus("Question sent to xAI.");
  }

  async function stopSession(reason) {
    clearIdleTimer();

    if (socket) {
      try {
        socket.close();
      } catch (error) {}
      socket = null;
    }

    if (audioContext) {
      try {
        await audioContext.close();
      } catch (error) {}
      audioContext = null;
    }

    nextPlayTime = 0;

    if (activeDeity) {
      log("XAI_SESSION_STOPPED reason=" + (reason || "manual_stop") + " deity=" + activeDeity);
    }

    activeDeity = null;
    activeVoice = null;
    activeModel = null;
    setButtons(false);
    setStatus("Idle.");
  }

  startHathorButton.addEventListener("click", function () {
    startSession("Hathor", hathorVoiceSelect.value || "sal");
  });

  startMosesButton.addEventListener("click", function () {
    startSession("Moses", mosesVoiceSelect.value || "leo");
  });

  sendButton.addEventListener("click", function () {
    sendQuestion();
  });

  stopButton.addEventListener("click", function () {
    stopSession("manual_stop");
  });

  window.addEventListener("beforeunload", function () {
    stopSession("page_unload");
  });
})();
