(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    const panel = document.getElementById("xaiConversationLabPanel");
    if (!panel) return;

    const eventLog = document.getElementById("eventLog");
    const startButton = document.getElementById("conversationStartButton");
    const endButton = document.getElementById("conversationEndButton");
    const deitySelect = document.getElementById("conversationDeitySelect");
    const statusEl = document.getElementById("conversationStatus");
    const inputTranscriptEl = document.getElementById("conversationInputTranscript");
    const assistantTranscriptEl = document.getElementById("conversationAssistantTranscript");
    const timingEl = document.getElementById("conversationTiming");

    const INPUT_SAMPLE_RATE = 24000;
    const OUTPUT_SAMPLE_RATE = 24000;
    const AUDIO_PRICE_PER_MINUTE_USD = 0.05;

    // Cost/pacing tuning:
    // - Require more than one loud frame before opening the speech gate.
    // - Add a short cooldown after playback before listening resumes.
    // - Keep enough trailing audio to avoid clipping the seeker's last word.
    const SPEECH_RMS_THRESHOLD = 0.014;
    const SPEECH_START_FRAMES_REQUIRED = 3;
    const POST_PLAYBACK_COOLDOWN_MS = 900;
    const IDLE_AUTO_END_AFTER_RETURN_MS = 12000;
    const PRE_ROLL_MS = 320;
    const CLIENT_TURN_COMMIT_SILENCE_MS = 2400;
    const TRAILING_AUDIO_MS = CLIENT_TURN_COMMIT_SILENCE_MS;
    const IDLE_TIMEOUT_MS = 90000;
    const MAX_SESSION_MS = 300000;
    const PLAYBACK_DRAIN_PADDING_MS = 1200;
    const PLAYBACK_DRAIN_MIN_MS = 1500;
    const PLAYBACK_DRAIN_MAX_MS = 60000;

    const state = {
      socket: null,
      sessionData: null,
      active: false,
      starting: false,
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
      idleAutoEndTimer: null,

      inputSamplesSent: 0,
      inputBytesSent: 0,
      inputChunksSent: 0,
      outputSamplesReceived: 0,
      outputBytesReceived: 0,
      firstAudioDeltaAt: 0,

      currentInputTranscript: "",
      currentAssistantTranscript: ""
    };

    function nowIso() {
      return new Date().toISOString();
    }

    function elapsedMs(startedAt) {
      return startedAt ? Math.round(performance.now() - startedAt) : null;
    }

    function log(label, payload) {
      const line = "[" + nowIso() + "] " + label;
      if (eventLog) {
        eventLog.textContent += line + "\n";
        if (typeof payload !== "undefined") {
          try {
            eventLog.textContent += JSON.stringify(payload, null, 2) + "\n";
          } catch (err) {
            eventLog.textContent += String(payload) + "\n";
          }
        }
        eventLog.scrollTop = eventLog.scrollHeight;
      }

      try {
        console.log("[xAI conversation lab]", label, payload || "");
      } catch (err) {
        /* no-op */
      }
    }

    function setStatus(message) {
      if (statusEl) statusEl.textContent = message || "";
    }

    function setButtons(activeMode) {
      if (startButton) startButton.disabled = activeMode || state.starting;
      if (endButton) endButton.disabled = !activeMode && !state.starting;
      if (startButton) startButton.textContent = state.starting ? "Starting conversation..." : "Begin Voice Conversation";
    }

    function estimatedAudioCostUsd(inputSeconds, outputSeconds) {
      return ((inputSeconds + outputSeconds) / 60) * AUDIO_PRICE_PER_MINUTE_USD;
    }

    function clearIdleAutoEndTimer() {
      if (state.idleAutoEndTimer) {
        window.clearTimeout(state.idleAutoEndTimer);
        state.idleAutoEndTimer = null;
      }
    }

    function scheduleIdleAutoEnd() {
      clearIdleAutoEndTimer();

      if (!state.active) return;

      state.idleAutoEndTimer = window.setTimeout(function () {
        if (!state.active || state.speechGateOpen || state.assistantSpeaking) {
          scheduleIdleAutoEnd();
          return;
        }

        log("CONVERSATION_IDLE_AUTO_END", {
          idle_after_return_ms: IDLE_AUTO_END_AFTER_RETURN_MS,
          input_audio_seconds: Number((state.inputSamplesSent / INPUT_SAMPLE_RATE).toFixed(3)),
          output_audio_seconds: Number((state.outputSamplesReceived / OUTPUT_SAMPLE_RATE).toFixed(3)),
          note: "Auto-ending after quiet local listening. No silence streaming is intended."
        });

        endConversation("idle_auto_end");
      }, IDLE_AUTO_END_AFTER_RETURN_MS);
    }

    function updateTiming() {
      if (!timingEl) return;

      const sessionMs = state.sessionStartedAt ? elapsedMs(state.sessionStartedAt) : 0;
      const inputSeconds = state.inputSamplesSent / INPUT_SAMPLE_RATE;
      const outputSeconds = state.outputSamplesReceived / OUTPUT_SAMPLE_RATE;
      const estimatedCost = estimatedAudioCostUsd(inputSeconds, outputSeconds);

      timingEl.textContent = [
        "session_ms=" + (sessionMs || 0),
        "input_audio_seconds=" + inputSeconds.toFixed(3),
        "output_audio_seconds=" + outputSeconds.toFixed(3),
        "estimated_audio_cost_usd=" + estimatedCost.toFixed(4),
        "input_chunks=" + state.inputChunksSent,
        "input_bytes=" + state.inputBytesSent,
        "output_bytes=" + state.outputBytesReceived,
        "speech_turns=" + state.speechTurnIndex,
        "assistant_turns=" + state.assistantTurnIndex,
        "first_audio_delta_ms=" + (state.firstAudioDeltaAt || "-")
      ].join(" | ");
    }

    function touchActivity(reason) {
      state.lastActivityAt = performance.now();
      if (reason) log("CONVERSATION_ACTIVITY", { reason: reason });
      scheduleIdleTimeout();
    }

    function scheduleIdleTimeout() {
      if (state.idleTimer) {
        window.clearTimeout(state.idleTimer);
        state.idleTimer = null;
      }

      if (!state.active) return;

      state.idleTimer = window.setTimeout(function () {
        const idleMs = Math.round(performance.now() - state.lastActivityAt);
        if (state.active && idleMs >= IDLE_TIMEOUT_MS) {
          endConversation("idle_timeout");
        }
      }, IDLE_TIMEOUT_MS + 250);
    }

    function scheduleMaxSessionTimeout() {
      if (state.maxSessionTimer) {
        window.clearTimeout(state.maxSessionTimer);
        state.maxSessionTimer = null;
      }

      state.maxSessionTimer = window.setTimeout(function () {
        if (state.active) {
          endConversation("max_session_timeout");
        }
      }, MAX_SESSION_MS);
    }

    function getSelectedDeity() {
      const value = deitySelect && deitySelect.value ? deitySelect.value : "Hathor";
      return value === "Moses" ? "Moses" : "Hathor";
    }

    function getSelectedRealtimeVoice(deity) {
      const selectId = deity === "Moses" ? "mosesVoiceSelect" : "hathorVoiceSelect";
      const fallback = deity === "Moses" ? "leo" : "eve";
      const select = document.getElementById(selectId);
      return select && select.value ? select.value : fallback;
    }

    function getConversationInstructions(deity) {
      if (deity === "Moses") {
        return [
          "You are Moses in the God Incorporated realtime voice lab.",
          "This is a live conversational voice test.",
          "Speak with clarity, moral seriousness, patience, and humane strength.",
          "Keep voice answers short: one to three spoken sentences unless the seeker asks for more.",
          "Give one clear thought, then invite the seeker to continue.",
          "Avoid markdown, headings, numbered lists, and long formal explanations.",
          "If the seeker pauses briefly, do not rush to fill every silence."
        ].join(" ");
      }

      return [
        "You are Hathor in the God Incorporated realtime voice lab.",
        "This is a live conversational voice test.",
        "Speak with warmth, luminous presence, emotional intelligence, and gentle sacredness.",
        "Keep voice answers short: one to three spoken sentences unless the seeker asks for more.",
        "Give one clear, warm thought, then invite the seeker to continue.",
        "Avoid markdown, headings, numbered lists, and ornate over-poetry.",
        "If the seeker pauses briefly, do not rush to fill every silence."
      ].join(" ");
    }

    async function readJsonResponse(response) {
      const text = await response.text();
      try {
        return text ? JSON.parse(text) : {};
      } catch (err) {
        return { error: text || "Non-JSON response" };
      }
    }

    async function createRealtimeSession(deity, realtimeVoice) {
      const response = await fetch("/voice/xai/realtime/session", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          voice: deity,
          deity: deity,
          realtime_voice: realtimeVoice,
          voice_name: realtimeVoice,
          xai_voice: realtimeVoice,
          lab_input_mode: "conversation"
        })
      });

      const data = await readJsonResponse(response);

      if (!response.ok) {
        throw new Error(data.detail || data.error || "xAI realtime conversation session could not be prepared.");
      }

      return data;
    }

    function firstPresent(values) {
      for (const value of values) {
        if (value) return value;
      }
      return "";
    }

    function normalizeProtocolValue(value) {
      if (!value) return [];
      if (Array.isArray(value)) return value.filter(Boolean);
      if (typeof value === "string") return [value];
      return [];
    }

    function resolveWebSocketUrl(data) {
      const explicitUrl = firstPresent([
        data.websocket_url,
        data.ws_url,
        data.realtime_url,
        data.url,
        data.session && data.session.websocket_url,
        data.session && data.session.ws_url,
        data.session && data.session.url
      ]);

      if (explicitUrl) return explicitUrl;

      const model = firstPresent([
        data.model,
        data.session && data.session.model
      ]) || "grok-voice-latest";

      return "wss://api.x.ai/v1/realtime?model=" + encodeURIComponent(model);
    }

    function resolveWebSocketProtocols(data) {
      const explicitProtocols = []
        .concat(normalizeProtocolValue(data.websocket_protocols))
        .concat(normalizeProtocolValue(data.protocols))
        .concat(normalizeProtocolValue(data.websocket_protocol))
        .concat(normalizeProtocolValue(data.protocol));

      if (explicitProtocols.length) return explicitProtocols;

      const clientSecret = firstPresent([
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

    function openWebSocket(data) {
      return new Promise(function (resolve, reject) {
        const wsUrl = resolveWebSocketUrl(data);
        const protocols = resolveWebSocketProtocols(data);

        log("CONVERSATION_WEBSOCKET_CONNECTING", {
          url_present: Boolean(wsUrl),
          protocols_present: protocols.length > 0,
          protocol_count: protocols.length
        });

        let ws;
        try {
          ws = protocols.length ? new WebSocket(wsUrl, protocols) : new WebSocket(wsUrl);
        } catch (err) {
          reject(err);
          return;
        }

        state.socket = ws;

        ws.onopen = function () {
          log("CONVERSATION_WEBSOCKET_OPEN", {
            deity: state.selectedDeity,
            realtime_voice: state.selectedRealtimeVoice
          });
          resolve(ws);
        };

        ws.onerror = function (event) {
          log("CONVERSATION_WEBSOCKET_ERROR", {
            message: event && event.message ? event.message : "websocket error"
          });
        };

        ws.onclose = function (event) {
          log("CONVERSATION_WEBSOCKET_CLOSE", {
            code: event.code,
            reason: event.reason,
            was_clean: event.wasClean
          });
          cleanupInputCapture(true);
          state.active = false;
          state.starting = false;
          setButtons(false);
          updateTiming();
        };

        ws.onmessage = function (event) {
          let data;
          try {
            data = JSON.parse(event.data);
          } catch (err) {
            log("CONVERSATION_MESSAGE_RAW", String(event.data).slice(0, 500));
            return;
          }

          handleServerEvent(data);
        };
      });
    }

    function sendJson(event) {
      if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
        throw new Error("xAI conversation WebSocket is not open.");
      }
      state.socket.send(JSON.stringify(event));
    }

    function sendSessionUpdate() {
      sendJson({
        type: "session.update",
        session: {
          voice: state.selectedRealtimeVoice,
          instructions: getConversationInstructions(state.selectedDeity),
          turn_detection: null,
          audio: {
            input: {
              format: { type: "audio/pcm", rate: INPUT_SAMPLE_RATE },
              transcription: { model: "grok-transcribe" }
            },
            output: {
              format: { type: "audio/pcm", rate: OUTPUT_SAMPLE_RATE }
            }
          }
        }
      });

      log("CONVERSATION_SESSION_UPDATE_SENT", {
        turn_detection: "client_commit_local_speech_gate",
        client_turn_commit_silence_ms: CLIENT_TURN_COMMIT_SILENCE_MS,
        input_rate: INPUT_SAMPLE_RATE,
        output_rate: OUTPUT_SAMPLE_RATE,
        transcription_model: "grok-transcribe"
      });
    }

    function resetSessionMetrics() {
      state.nextPlaybackTime = 0;
      state.speechGateOpen = false;
      state.speechTurnIndex = 0;
      state.assistantTurnIndex = 0;
      state.assistantSpeaking = false;
      state.preRollChunks = [];
      state.preRollSamples = 0;
      state.trailingMsRemaining = 0;
      state.speechAboveThresholdFrames = 0;
      state.listeningCooldownUntil = 0;
      state.turnInputStartSamples = 0;
      state.responseOutputStartSamples = 0;
      if (state.idleAutoEndTimer) {
        window.clearTimeout(state.idleAutoEndTimer);
        state.idleAutoEndTimer = null;
      }
      state.inputSamplesSent = 0;
      state.inputBytesSent = 0;
      state.inputChunksSent = 0;
      state.outputSamplesReceived = 0;
      state.outputBytesReceived = 0;
      state.firstAudioDeltaAt = 0;
      state.currentInputTranscript = "";
      state.currentAssistantTranscript = "";

      if (state.playbackDrainTimer) {
        window.clearTimeout(state.playbackDrainTimer);
        state.playbackDrainTimer = null;
      }

      if (inputTranscriptEl) inputTranscriptEl.textContent = "";
      if (assistantTranscriptEl) assistantTranscriptEl.textContent = "";
      updateTiming();
    }

    async function startConversation() {
      if (state.active || state.starting) return;

      state.starting = true;
      state.ending = false;
      state.selectedDeity = getSelectedDeity();
      state.selectedRealtimeVoice = getSelectedRealtimeVoice(state.selectedDeity);
      state.sessionStartedAt = performance.now();
      state.lastActivityAt = performance.now();

      resetSessionMetrics();
      setButtons(true);
      setStatus("Starting xAI conversation session...");

      log("CONVERSATION_START_REQUESTED", {
        deity: state.selectedDeity,
        realtime_voice: state.selectedRealtimeVoice,
        mode: "client_commit_local_speech_gate"
      });

      try {
        state.sessionData = await createRealtimeSession(state.selectedDeity, state.selectedRealtimeVoice);

        log("CONVERSATION_SESSION_CREATED", {
          provider: state.sessionData.provider,
          model: state.sessionData.model,
          deity: state.sessionData.deity,
          realtime_voice: state.sessionData.realtime_voice,
          transport: state.sessionData.transport,
          total_ms: state.sessionData.total_ms
        });

        await openWebSocket(state.sessionData);
        sendSessionUpdate();

        state.active = true;
        await startInputCapture();

        state.starting = false;
        setButtons(true);
        setStatus("Conversation listening. Speak naturally. Use End Conversation when finished.");
        touchActivity("conversation_started");
        scheduleMaxSessionTimeout();
      } catch (err) {
        log("CONVERSATION_START_FAILED", { error: err.message || String(err) });
        setStatus("Conversation failed: " + (err.message || err));
        cleanupInputCapture(true);
        closeSocketQuietly("conversation_start_failed");
        state.active = false;
        state.starting = false;
        setButtons(false);
      }
    }

    async function startInputCapture() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error("This browser does not expose getUserMedia microphone capture.");
      }

      const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextCtor) {
        throw new Error("This browser does not expose AudioContext.");
      }

      state.inputStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      });

      state.inputAudioContext = new AudioContextCtor();

      if (state.inputAudioContext.state === "suspended") {
        await state.inputAudioContext.resume();
      }

      state.inputSource = state.inputAudioContext.createMediaStreamSource(state.inputStream);
      state.inputProcessor = state.inputAudioContext.createScriptProcessor(4096, 1, 1);
      state.inputProcessor.onaudioprocess = handleAudioProcess;
      state.inputSource.connect(state.inputProcessor);
      state.inputProcessor.connect(state.inputAudioContext.destination);

      log("CONVERSATION_MIC_OPEN_LOCAL", {
        input_context_rate: state.inputAudioContext.sampleRate,
        target_rate: INPUT_SAMPLE_RATE,
        local_gate_threshold: SPEECH_RMS_THRESHOLD,
        pre_roll_ms: PRE_ROLL_MS,
        trailing_audio_ms: TRAILING_AUDIO_MS,
        note: "Mic is open locally, but audio is streamed only when local speech gate opens."
      });
    }

    function cleanupInputCapture(closeContext) {
      if (state.inputProcessor) {
        try { state.inputProcessor.disconnect(); } catch (err) { /* no-op */ }
        state.inputProcessor.onaudioprocess = null;
        state.inputProcessor = null;
      }

      if (state.inputSource) {
        try { state.inputSource.disconnect(); } catch (err) { /* no-op */ }
        state.inputSource = null;
      }

      if (state.inputStream) {
        state.inputStream.getTracks().forEach(function (track) {
          try { track.stop(); } catch (err) { /* no-op */ }
        });
        state.inputStream = null;
      }

      if (closeContext && state.inputAudioContext) {
        try { state.inputAudioContext.close(); } catch (err) { /* no-op */ }
        state.inputAudioContext = null;
      }
    }

    function handleAudioProcess(event) {
      if (!state.active || !state.socket || state.socket.readyState !== WebSocket.OPEN) return;
      if (state.assistantSpeaking) return;

      const input = event.inputBuffer.getChannelData(0);
      const sourceRate = state.inputAudioContext ? state.inputAudioContext.sampleRate : event.inputBuffer.sampleRate;
      const chunkMs = (input.length / sourceRate) * 1000;
      const rms = computeRms(input);
      if (state.assistantSpeaking) {
        state.speechAboveThresholdFrames = 0;
        state.preRollChunks = [];
        state.preRollSamples = 0;
        return;
      }

      const resampled = resampleFloat32(input, sourceRate, INPUT_SAMPLE_RATE);

      if (performance.now() < state.listeningCooldownUntil) {
        state.speechAboveThresholdFrames = 0;
        state.preRollChunks = [];
        state.preRollSamples = 0;
        return;
      }

      rememberPreRoll(resampled);

      if (rms >= SPEECH_RMS_THRESHOLD) {
        state.speechAboveThresholdFrames += 1;

        if (!state.speechGateOpen && state.speechAboveThresholdFrames < SPEECH_START_FRAMES_REQUIRED) {
          return;
        }

        if (!state.speechGateOpen) {
          state.speechGateOpen = true;
          state.speechTurnIndex += 1;
          state.turnInputStartSamples = state.inputSamplesSent;
          state.currentInputTranscript = "";
          if (inputTranscriptEl) inputTranscriptEl.textContent = "";

          clearIdleAutoEndTimer();

          log("CONVERSATION_SPEECH_GATE_OPEN", {
            speech_turn: state.speechTurnIndex,
            rms: Number(rms.toFixed(5)),
            speech_start_frames_required: SPEECH_START_FRAMES_REQUIRED
          });

          flushPreRoll();
          touchActivity("local_speech_started");
        }

        state.trailingMsRemaining = TRAILING_AUDIO_MS;
        sendAudioChunk(resampled, "speech");
        return;
      }

      state.speechAboveThresholdFrames = 0;

      if (state.speechGateOpen && state.trailingMsRemaining > 0) {
        state.trailingMsRemaining -= chunkMs;
        sendAudioChunk(resampled, "trailing_audio");
        return;
      }

      if (state.speechGateOpen && state.trailingMsRemaining <= 0) {
        state.speechGateOpen = false;

        const turnInputSeconds = (state.inputSamplesSent - state.turnInputStartSamples) / INPUT_SAMPLE_RATE;

        log("CONVERSATION_SPEECH_GATE_CLOSED", {
          speech_turn: state.speechTurnIndex,
          input_audio_seconds: Number((state.inputSamplesSent / INPUT_SAMPLE_RATE).toFixed(3)),
          turn_input_audio_seconds: Number(turnInputSeconds.toFixed(3))
        });

        log("CONVERSATION_TURN_INPUT_AUDIO_COST_RELEVANT", {
          speech_turn: state.speechTurnIndex,
          turn_input_audio_seconds: Number(turnInputSeconds.toFixed(3)),
          estimated_turn_input_cost_usd: estimatedAudioCostUsd(turnInputSeconds, 0).toFixed(4)
        });

        commitConversationTurn(turnInputSeconds);
      }
    }

    function commitConversationTurn(turnInputSeconds) {
      if (state.turnCommitPending || state.assistantSpeaking || !state.active) {
        return;
      }

      state.turnCommitPending = true;

      try {
        sendJson({ type: "input_audio_buffer.commit" });

        log("CONVERSATION_INPUT_COMMITTED", {
          speech_turn: state.speechTurnIndex,
          input_audio_seconds: Number((state.inputSamplesSent / INPUT_SAMPLE_RATE).toFixed(3)),
          turn_input_audio_seconds: Number(turnInputSeconds.toFixed(3)),
          client_turn_commit_silence_ms: CLIENT_TURN_COMMIT_SILENCE_MS
        });

        sendJson({
          type: "response.create",
          response: {
            modalities: ["text", "audio"]
          }
        });

        log("CONVERSATION_RESPONSE_CREATE_SENT", {
          speech_turn: state.speechTurnIndex,
          modalities: ["text", "audio"],
          client_turn_commit_silence_ms: CLIENT_TURN_COMMIT_SILENCE_MS
        });

        setStatus("Speech sent. Oracle is preparing a spoken response...");
        touchActivity("client_turn_committed");
      } catch (err) {
        state.turnCommitPending = false;
        log("CONVERSATION_CLIENT_COMMIT_FAILED", { error: err.message || String(err) });
        setStatus("Could not commit conversation turn: " + (err.message || err));
      }
    }

    function rememberPreRoll(resampled) {
      state.preRollChunks.push(resampled);
      state.preRollSamples += resampled.length;

      const maxSamples = Math.round((PRE_ROLL_MS / 1000) * INPUT_SAMPLE_RATE);
      while (state.preRollSamples > maxSamples && state.preRollChunks.length > 1) {
        const removed = state.preRollChunks.shift();
        state.preRollSamples -= removed.length;
      }
    }

    function flushPreRoll() {
      const chunks = state.preRollChunks.slice();
      state.preRollChunks = [];
      state.preRollSamples = 0;

      chunks.forEach(function (chunk) {
        sendAudioChunk(chunk, "pre_roll");
      });

      log("CONVERSATION_PRE_ROLL_SENT", { chunks: chunks.length });
    }

    function sendAudioChunk(resampled, reason) {
      const audioBase64 = float32ToBase64PCM16(resampled);

      sendJson({
        type: "input_audio_buffer.append",
        audio: audioBase64
      });

      state.inputChunksSent += 1;
      state.inputSamplesSent += resampled.length;
      state.inputBytesSent += resampled.length * 2;

      if (state.inputChunksSent === 1 || state.inputChunksSent % 12 === 0 || reason === "pre_roll") {
        log("CONVERSATION_AUDIO_SENT", {
          reason: reason,
          chunks_sent: state.inputChunksSent,
          input_audio_seconds: Number((state.inputSamplesSent / INPUT_SAMPLE_RATE).toFixed(3)),
          socket_buffered_amount: state.socket ? state.socket.bufferedAmount : "-"
        });
      }

      updateTiming();
    }

    function computeRms(float32Array) {
      let sum = 0;
      for (let i = 0; i < float32Array.length; i += 1) {
        sum += float32Array[i] * float32Array[i];
      }
      return Math.sqrt(sum / Math.max(1, float32Array.length));
    }

    function resampleFloat32(input, sourceRate, targetRate) {
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

    function bytesToBase64(bytes) {
      let binary = "";
      const chunkSize = 0x8000;
      for (let i = 0; i < bytes.length; i += chunkSize) {
        const chunk = bytes.subarray(i, i + chunkSize);
        binary += String.fromCharCode.apply(null, chunk);
      }
      return btoa(binary);
    }

    function float32ToBase64PCM16(float32Array) {
      const bytes = new Uint8Array(float32Array.length * 2);
      const view = new DataView(bytes.buffer);

      for (let i = 0; i < float32Array.length; i += 1) {
        const sample = Math.max(-1, Math.min(1, float32Array[i]));
        const pcm = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        view.setInt16(i * 2, pcm, true);
      }

      return bytesToBase64(bytes);
    }

    function base64ToBytes(base64String) {
      const binary = atob(base64String);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
      }
      return bytes;
    }

    function base64ByteLength(base64String) {
      const clean = String(base64String || "").replace(/\s/g, "");
      if (!clean) return 0;
      const padding = clean.endsWith("==") ? 2 : (clean.endsWith("=") ? 1 : 0);
      return Math.max(0, Math.floor(clean.length * 3 / 4) - padding);
    }

    function ensureOutputAudioContext() {
      const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextCtor) {
        throw new Error("This browser does not expose AudioContext for playback.");
      }

      if (!state.outputAudioContext || state.outputAudioContext.state === "closed") {
        state.outputAudioContext = new AudioContextCtor({ sampleRate: OUTPUT_SAMPLE_RATE });
        state.nextPlaybackTime = 0;
      }

      if (state.outputAudioContext.state === "suspended") {
        state.outputAudioContext.resume().catch(function () {
          /* no-op */
        });
      }

      return state.outputAudioContext;
    }

    function playAudioDelta(base64Audio) {
      const bytes = base64ToBytes(base64Audio);
      if (bytes.length < 2) return;

      const pcm16 = new Int16Array(bytes.buffer);
      const float32 = new Float32Array(pcm16.length);

      for (let i = 0; i < pcm16.length; i += 1) {
        float32[i] = pcm16[i] / 32768.0;
      }

      const audioContext = ensureOutputAudioContext();
      const audioBuffer = audioContext.createBuffer(1, float32.length, OUTPUT_SAMPLE_RATE);
      audioBuffer.copyToChannel(float32, 0);

      const source = audioContext.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(audioContext.destination);

      const startAt = Math.max(audioContext.currentTime + 0.02, state.nextPlaybackTime || 0);
      source.start(startAt);
      state.nextPlaybackTime = startAt + audioBuffer.duration;

      state.outputBytesReceived += bytes.length;
      state.outputSamplesReceived += pcm16.length;
      updateTiming();
    }

    function extractTranscript(event) {
      return firstPresent([
        event.transcript,
        event.text,
        event.delta,
        event.item && event.item.content && event.item.content[0] && event.item.content[0].transcript,
        event.item && event.item.content && event.item.content[0] && event.item.content[0].text,
        event.content && event.content[0] && event.content[0].transcript,
        event.content && event.content[0] && event.content[0].text
      ]);
    }

    function handleServerEvent(event) {
      const type = event && event.type ? event.type : "";

      if (
        type === "session.created" ||
        type === "session.updated" ||
        type === "input_audio_buffer.speech_started" ||
        type === "input_audio_buffer.speech_stopped" ||
        type === "input_audio_buffer.committed" ||
        type === "response.created" ||
        type === "response.done"
      ) {
        log("CONVERSATION_SERVER_EVENT " + type, {
          event_id: event.event_id,
          response_id: event.response_id,
          item_id: event.item_id
        });
      }

      if (type === "conversation.item.input_audio_transcription.completed") {
        const transcript = extractTranscript(event);
        if (transcript) {
          state.currentInputTranscript = transcript;
          if (inputTranscriptEl) inputTranscriptEl.textContent = transcript;
        }
        log("CONVERSATION_TRANSCRIPT_DONE", {
          kind: "input_audio",
          transcript: state.currentInputTranscript || transcript || ""
        });
        return;
      }

      if (type === "response.output_audio_transcript.delta" || type === "response.text.delta" || type === "response.output_text.delta") {
        const delta = event.delta || event.text || "";
        if (delta) {
          state.currentAssistantTranscript += delta;
          if (assistantTranscriptEl) assistantTranscriptEl.textContent = state.currentAssistantTranscript;
        }
        return;
      }

      if (type === "response.output_audio_transcript.done") {
        const transcript = extractTranscript(event);
        if (transcript) {
          state.currentAssistantTranscript = transcript;
          if (assistantTranscriptEl) assistantTranscriptEl.textContent = transcript;
        }
        log("CONVERSATION_TRANSCRIPT_DONE", {
          kind: "assistant_audio",
          transcript_chars: state.currentAssistantTranscript.length
        });
        return;
      }

      if (type === "response.created") {
        clearIdleAutoEndTimer();
        state.assistantTurnIndex += 1;
        state.responseOutputStartSamples = state.outputSamplesReceived;
        state.firstAudioDeltaAt = 0;
        state.assistantSpeaking = true;
        state.turnCommitPending = false;
        state.speechGateOpen = false;
        state.trailingMsRemaining = 0;
        state.speechAboveThresholdFrames = 0;
        state.preRollChunks = [];
        state.preRollSamples = 0;
        state.currentAssistantTranscript = "";
        if (assistantTranscriptEl) assistantTranscriptEl.textContent = "";
        setStatus("Oracle is preparing a spoken response. Listening is paused until playback completes...");
        touchActivity("response_created");
        return;
      }

      if (type === "response.output_audio.delta") {
        if (!state.firstAudioDeltaAt) {
          state.firstAudioDeltaAt = elapsedMs(state.sessionStartedAt);
          log("CONVERSATION_FIRST_AUDIO_DELTA", {
            first_audio_delta_ms: state.firstAudioDeltaAt,
            output_delta_bytes: base64ByteLength(event.delta || "")
          });
        }

        state.assistantSpeaking = true;
        setStatus("Oracle speaking. Conversation will return to listening after playback.");
        touchActivity("assistant_audio_delta");

        if (event.delta) {
          playAudioDelta(event.delta);
        }

        return;
      }

      if (type === "response.output_audio.done") {
        log("CONVERSATION_SERVER_EVENT response.output_audio.done", {
          output_audio_seconds: Number((state.outputSamplesReceived / OUTPUT_SAMPLE_RATE).toFixed(3)),
          output_bytes: state.outputBytesReceived
        });
        return;
      }

      if (type === "response.done") {
        scheduleReturnToListening();
        return;
      }

      if (type === "error") {
        log("CONVERSATION_SERVER_EVENT error", {
          code: event.code || (event.error && event.error.code),
          message: event.message || (event.error && event.error.message) || "xAI realtime error"
        });
        setStatus("Conversation error. See event log.");
      }
    }

    function scheduleReturnToListening() {
      if (state.playbackDrainTimer) {
        window.clearTimeout(state.playbackDrainTimer);
      }

      const audioContext = state.outputAudioContext;
      const remainingMs = audioContext
        ? Math.max(0, Math.ceil((state.nextPlaybackTime - audioContext.currentTime) * 1000))
        : 0;

      const drainMs = Math.min(
        PLAYBACK_DRAIN_MAX_MS,
        Math.max(PLAYBACK_DRAIN_MIN_MS, remainingMs + PLAYBACK_DRAIN_PADDING_MS)
      );

      log("CONVERSATION_PLAYBACK_DRAIN_SCHEDULED", {
        drain_ms: drainMs,
        remaining_audio_ms: remainingMs,
        output_audio_seconds: Number((state.outputSamplesReceived / OUTPUT_SAMPLE_RATE).toFixed(3)),
        output_bytes: state.outputBytesReceived
      });

      state.playbackDrainTimer = window.setTimeout(function () {
        const turnOutputSeconds = (state.outputSamplesReceived - state.responseOutputStartSamples) / OUTPUT_SAMPLE_RATE;
        state.assistantSpeaking = false;
        state.currentAssistantTranscript = "";
        state.firstAudioDeltaAt = 0;
        state.speechAboveThresholdFrames = 0;
        state.preRollChunks = [];
        state.preRollSamples = 0;
        state.listeningCooldownUntil = performance.now() + POST_PLAYBACK_COOLDOWN_MS;

        setStatus("Returned to listening after a short cooldown. Speak again, or tap End Conversation.");

        log("CONVERSATION_TURN_OUTPUT_AUDIO_COST_RELEVANT", {
          assistant_turn: state.assistantTurnIndex,
          turn_output_audio_seconds: Number(turnOutputSeconds.toFixed(3)),
          estimated_turn_output_cost_usd: estimatedAudioCostUsd(0, turnOutputSeconds).toFixed(4)
        });

        log("CONVERSATION_RETURN_TO_LISTENING", {
          active: state.active,
          input_audio_seconds: Number((state.inputSamplesSent / INPUT_SAMPLE_RATE).toFixed(3)),
          output_audio_seconds: Number((state.outputSamplesReceived / OUTPUT_SAMPLE_RATE).toFixed(3)),
          post_playback_cooldown_ms: POST_PLAYBACK_COOLDOWN_MS,
          idle_auto_end_after_return_ms: IDLE_AUTO_END_AFTER_RETURN_MS
        });
        touchActivity("returned_to_listening");
        scheduleIdleAutoEnd();
      }, drainMs);
    }

    function closeSocketQuietly(reason) {
      if (!state.socket) return;

      try {
        if (state.socket.readyState === WebSocket.OPEN || state.socket.readyState === WebSocket.CONNECTING) {
          state.socket.close(1000, reason || "conversation_close");
        }
      } catch (err) {
        /* no-op */
      }
    }

    function endConversation(reason) {
      if (state.ending) return;

      state.ending = true;

      if (state.idleTimer) {
        window.clearTimeout(state.idleTimer);
        state.idleTimer = null;
      }

      if (state.maxSessionTimer) {
        window.clearTimeout(state.maxSessionTimer);
        state.maxSessionTimer = null;
      }

      if (state.playbackDrainTimer) {
        window.clearTimeout(state.playbackDrainTimer);
        state.playbackDrainTimer = null;
      }

      cleanupInputCapture(true);

      log("CONVERSATION_SESSION_ENDED", {
        reason: reason || "user_end",
        session_ms: elapsedMs(state.sessionStartedAt),
        input_audio_seconds: Number((state.inputSamplesSent / INPUT_SAMPLE_RATE).toFixed(3)),
        output_audio_seconds: Number((state.outputSamplesReceived / OUTPUT_SAMPLE_RATE).toFixed(3)),
        input_bytes: state.inputBytesSent,
        output_bytes: state.outputBytesReceived,
        speech_turns: state.speechTurnIndex,
        assistant_turns: state.assistantTurnIndex
      });

      const finalInputSeconds = state.inputSamplesSent / INPUT_SAMPLE_RATE;
      const finalOutputSeconds = state.outputSamplesReceived / OUTPUT_SAMPLE_RATE;

      log("CONVERSATION_AUDIO_DURATION_COST_RELEVANT", {
        input_audio_seconds: Number(finalInputSeconds.toFixed(3)),
        output_audio_seconds: Number(finalOutputSeconds.toFixed(3)),
        estimated_audio_cost_usd: estimatedAudioCostUsd(finalInputSeconds, finalOutputSeconds).toFixed(4),
        audio_price_per_minute_usd: AUDIO_PRICE_PER_MINUTE_USD,
        idle_auto_end_after_return_ms: IDLE_AUTO_END_AFTER_RETURN_MS,
        local_gate_threshold: SPEECH_RMS_THRESHOLD,
        speech_start_frames_required: SPEECH_START_FRAMES_REQUIRED,
        note: "xAI Voice Agent audio is billed by duration; this is client-side lab telemetry."
      });

      state.active = false;
      state.starting = false;
      closeSocketQuietly(reason || "conversation_end");
      setButtons(false);
      setStatus("Conversation ended.");
      updateTiming();

      window.setTimeout(function () {
        state.ending = false;
      }, 250);
    }

    if (startButton) {
      startButton.addEventListener("click", startConversation);
    }

    if (endButton) {
      endButton.addEventListener("click", function () {
        endConversation("user_end");
      });
    }

    setButtons(false);
    setStatus("Conversation mode ready. Start once, speak naturally, then End Conversation.");
    updateTiming();
  });
})();
