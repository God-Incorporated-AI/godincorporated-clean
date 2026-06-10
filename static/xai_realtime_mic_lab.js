(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    const panel = document.getElementById("xaiMicLabPanel");
    if (!panel) return;

    const eventLog = document.getElementById("eventLog");
    const micDeitySelect = document.getElementById("micDeitySelect");
    const micStartButton = document.getElementById("micStartButton");
    const micStopButton = document.getElementById("micStopButton");
    const micHoldButton = document.getElementById("micHoldButton");
    const micStatus = document.getElementById("micStatus");
    const micInputTranscript = document.getElementById("micInputTranscript");
    const micAssistantTranscript = document.getElementById("micAssistantTranscript");
    const micTiming = document.getElementById("micTiming");

    const INPUT_SAMPLE_RATE = 24000;
    const OUTPUT_SAMPLE_RATE = 24000;
    const LOCAL_SPEECH_RMS_THRESHOLD = 0.008;
    const AUTO_STOP_SILENCE_MS = 950;
    const MIN_CAPTURE_BEFORE_AUTO_STOP_MS = 650;
    const MAX_CAPTURE_MS = 20000;
    const PLAYBACK_DRAIN_PADDING_MS = 1200;
    const PLAYBACK_DRAIN_MIN_MS = 1500;
    const PLAYBACK_DRAIN_MAX_MS = 60000;

    const state = {
      socket: null,
      sessionData: null,
      isStarting: false,
      isCapturing: false,
      isCommitting: false,
      pendingStopAfterStart: false,
      responseDone: false,
      cleanAutoStopDone: false,

      inputStream: null,
      inputAudioContext: null,
      inputSource: null,
      inputProcessor: null,

      outputAudioContext: null,
      nextPlaybackTime: 0,
      playbackDrainTimer: null,

      captureStartedAt: 0,
      captureStoppedAt: 0,
      speechDetected: false,
      quietMs: 0,
      chunksSent: 0,
      inputBytesSent: 0,
      inputSamplesSent: 0,
      outputBytesReceived: 0,
      outputSamplesReceived: 0,
      firstAudioDeltaAt: 0,
      transcriptText: "",
      assistantTranscriptText: "",
      selectedDeity: "Hathor",
      selectedRealtimeVoice: "eve"
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
        console.log("[xAI mic lab]", label, payload || "");
      } catch (err) {
        /* no-op */
      }
    }

    function setMicStatus(message) {
      if (micStatus) {
        micStatus.textContent = message || "";
      }
    }

    function setButtons(mode) {
      const starting = mode === "starting";
      const capturing = mode === "capturing";
      const waiting = mode === "waiting";

      if (micStartButton) micStartButton.disabled = starting || capturing || waiting;
      if (micStopButton) micStopButton.disabled = !(capturing || starting);
      if (micHoldButton) micHoldButton.disabled = starting || waiting;

      if (micStartButton) {
        micStartButton.textContent = starting ? "Opening mic..." : "Start Mic Capture";
      }
      if (micStopButton) {
        micStopButton.textContent = capturing ? "Stop + Commit Audio" : "Stop";
      }
    }

    function updateTiming() {
      if (!micTiming) return;

      const inputAudioSeconds = state.inputSamplesSent / INPUT_SAMPLE_RATE;
      const outputAudioSeconds = state.outputSamplesReceived / OUTPUT_SAMPLE_RATE;
      const captureMs = state.captureStoppedAt && state.captureStartedAt
        ? Math.round(state.captureStoppedAt - state.captureStartedAt)
        : (state.captureStartedAt ? elapsedMs(state.captureStartedAt) : 0);

      micTiming.textContent = [
        "capture_ms=" + (captureMs || 0),
        "input_audio_seconds=" + inputAudioSeconds.toFixed(3),
        "output_audio_seconds=" + outputAudioSeconds.toFixed(3),
        "chunks_sent=" + state.chunksSent,
        "input_bytes=" + state.inputBytesSent,
        "output_bytes=" + state.outputBytesReceived,
        "first_audio_delta_ms=" + (state.firstAudioDeltaAt || "-")
      ].join(" | ");
    }

    function getSelectedDeity() {
      const value = micDeitySelect && micDeitySelect.value ? micDeitySelect.value : "Hathor";
      return value === "Moses" ? "Moses" : "Hathor";
    }

    function getSelectedRealtimeVoice(deity) {
      const selectId = deity === "Moses" ? "mosesVoiceSelect" : "hathorVoiceSelect";
      const fallback = deity === "Moses" ? "leo" : "eve";
      const voiceSelect = document.getElementById(selectId);
      return voiceSelect && voiceSelect.value ? voiceSelect.value : fallback;
    }

    function getMicInstructions(deity) {
      if (deity === "Moses") {
        return [
          "You are Moses in the God Incorporated xAI realtime voice lab.",
          "This is a lab-only microphone test.",
          "Answer spoken input with clear, concise, morally grounded guidance.",
          "Keep the response brief enough for voice playback."
        ].join(" ");
      }

      return [
        "You are Hathor in the God Incorporated xAI realtime voice lab.",
        "This is a lab-only microphone test.",
        "Answer spoken input with warm, lucid, gently sacred guidance.",
        "Keep the response brief enough for voice playback."
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
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          voice: deity,
          deity: deity,
          realtime_voice: realtimeVoice,
          voice_name: realtimeVoice,
          xai_voice: realtimeVoice,
          lab_input_mode: "mic"
        })
      });

      const data = await readJsonResponse(response);

      if (!response.ok) {
        throw new Error(data.detail || data.error || "xAI realtime session could not be prepared.");
      }

      return data;
    }

    function firstPresent(values) {
      for (const value of values) {
        if (value) return value;
      }
      return "";
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

      if (explicitUrl) {
        return explicitUrl;
      }

      const model = firstPresent([
        data.model,
        data.session && data.session.model
      ]) || "grok-voice-latest";

      return "wss://api.x.ai/v1/realtime?model=" + encodeURIComponent(model);
    }

    function normalizeProtocolValue(value) {
      if (!value) return [];
      if (Array.isArray(value)) return value.filter(Boolean);
      if (typeof value === "string") return [value];
      return [];
    }

    function resolveWebSocketProtocols(data) {
      const explicitProtocols = []
        .concat(normalizeProtocolValue(data.websocket_protocols))
        .concat(normalizeProtocolValue(data.protocols))
        .concat(normalizeProtocolValue(data.websocket_protocol))
        .concat(normalizeProtocolValue(data.protocol));

      if (explicitProtocols.length) {
        return explicitProtocols;
      }

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

        if (!wsUrl) {
          log("XAI_MIC_SESSION_KEYS", Object.keys(data || {}));
          reject(new Error("No WebSocket URL was returned by the broker."));
          return;
        }

        log("XAI_WEBSOCKET_CONNECTING", {
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
          log("XAI_WEBSOCKET_OPEN", {
            url_present: Boolean(wsUrl),
            protocols_present: protocols.length > 0,
            deity: state.selectedDeity,
            realtime_voice: state.selectedRealtimeVoice
          });
          resolve(ws);
        };

        ws.onerror = function (event) {
          log("XAI_WEBSOCKET_ERROR", {
            message: event && event.message ? event.message : "websocket error"
          });
        };

        ws.onclose = function (event) {
          log("XAI_WEBSOCKET_CLOSE", {
            code: event.code,
            reason: event.reason,
            was_clean: event.wasClean
          });

          if (state.responseDone && !state.cleanAutoStopDone) {
            state.cleanAutoStopDone = true;
            log("CLEAN_AUTO_STOP", {
              reason: "websocket_closed_after_response_done",
              was_clean: event.wasClean
            });
          }

          cleanupInputCapture(false);
          setButtons("idle");
        };

        ws.onmessage = function (event) {
          let data;
          try {
            data = JSON.parse(event.data);
          } catch (err) {
            log("XAI_MESSAGE_RAW", String(event.data).slice(0, 500));
            return;
          }

          handleServerEvent(data);
        };
      });
    }

    function sendJson(event) {
      if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
        throw new Error("xAI WebSocket is not open.");
      }
      state.socket.send(JSON.stringify(event));
    }

    function sendSessionUpdate() {
      sendJson({
        type: "session.update",
        session: {
          voice: state.selectedRealtimeVoice,
          instructions: getMicInstructions(state.selectedDeity),
          turn_detection: null,
          audio: {
            input: {
              format: {
                type: "audio/pcm",
                rate: INPUT_SAMPLE_RATE
              },
              transcription: {
                model: "grok-transcribe"
              }
            },
            output: {
              format: {
                type: "audio/pcm",
                rate: OUTPUT_SAMPLE_RATE
              }
            }
          }
        }
      });

      log("MIC_SESSION_UPDATE_SENT", {
        turn_detection: null,
        input_rate: INPUT_SAMPLE_RATE,
        output_rate: OUTPUT_SAMPLE_RATE,
        transcription_model: "grok-transcribe"
      });
    }

    function resetMicMetrics() {
      state.responseDone = false;
      state.cleanAutoStopDone = false;
      state.captureStartedAt = 0;
      state.captureStoppedAt = 0;
      state.speechDetected = false;
      state.quietMs = 0;
      state.chunksSent = 0;
      state.inputBytesSent = 0;
      state.inputSamplesSent = 0;
      state.outputBytesReceived = 0;
      state.outputSamplesReceived = 0;
      state.firstAudioDeltaAt = 0;
      state.transcriptText = "";
      state.assistantTranscriptText = "";
      state.nextPlaybackTime = 0;

      if (state.playbackDrainTimer) {
        window.clearTimeout(state.playbackDrainTimer);
        state.playbackDrainTimer = null;
      }

      if (micInputTranscript) micInputTranscript.textContent = "";
      if (micAssistantTranscript) micAssistantTranscript.textContent = "";
      updateTiming();
    }

    async function startControlledMic(trigger) {
      if (state.isStarting || state.isCapturing || state.isCommitting) return;

      state.isStarting = true;
      state.pendingStopAfterStart = false;
      state.selectedDeity = getSelectedDeity();
      state.selectedRealtimeVoice = getSelectedRealtimeVoice(state.selectedDeity);

      resetMicMetrics();
      setButtons("starting");
      setMicStatus("Preparing xAI realtime microphone session...");
      log("MIC_START_REQUESTED", {
        trigger: trigger || "button",
        deity: state.selectedDeity,
        realtime_voice: state.selectedRealtimeVoice
      });

      try {
        state.sessionData = await createRealtimeSession(state.selectedDeity, state.selectedRealtimeVoice);
        log("MIC_SESSION_CREATED", {
          provider: state.sessionData.provider,
          model: state.sessionData.model,
          deity: state.sessionData.deity,
          realtime_voice: state.sessionData.realtime_voice,
          transport: state.sessionData.transport,
          total_ms: state.sessionData.total_ms
        });

        await openWebSocket(state.sessionData);
        sendSessionUpdate();
        await startInputCapture();

        state.isStarting = false;

        if (state.pendingStopAfterStart) {
          await stopAndCommitAudio("pending_stop_after_start");
        }
      } catch (err) {
        state.isStarting = false;
        cleanupInputCapture(false);
        closeSocketQuietly("mic_start_failed");
        setButtons("idle");
        setMicStatus("Mic path failed: " + (err.message || err));
        log("MIC_START_FAILED", {
          error: err.message || String(err)
        });
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

      state.inputProcessor.onaudioprocess = handleMicAudioProcess;
      state.inputSource.connect(state.inputProcessor);
      state.inputProcessor.connect(state.inputAudioContext.destination);

      state.captureStartedAt = performance.now();
      state.isCapturing = true;
      state.isCommitting = false;

      setButtons("capturing");
      setMicStatus("Mic is capturing. Speak now. Stop is manual, and silence auto-stops after speech.");
      log("MIC_CAPTURE_START", {
        input_context_rate: state.inputAudioContext.sampleRate,
        target_rate: INPUT_SAMPLE_RATE,
        auto_stop_silence_ms: AUTO_STOP_SILENCE_MS,
        max_capture_ms: MAX_CAPTURE_MS
      });
    }

    function requestStop(reason) {
      if (state.isStarting && !state.isCapturing) {
        state.pendingStopAfterStart = true;
        log("MIC_STOP_QUEUED", { reason: reason || "stop_requested_before_capture" });
        return;
      }

      if (state.isCapturing) {
        stopAndCommitAudio(reason || "manual_stop");
      }
    }

    function cleanupInputCapture(closeContext) {
      state.isCapturing = false;

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

    async function stopAndCommitAudio(reason) {
      if (state.isCommitting) return;

      state.isCommitting = true;
      state.captureStoppedAt = performance.now();

      cleanupInputCapture(true);
      setButtons("waiting");

      const inputAudioSeconds = state.inputSamplesSent / INPUT_SAMPLE_RATE;
      const captureMs = Math.round(state.captureStoppedAt - state.captureStartedAt);

      log("MIC_CAPTURE_STOP", {
        reason: reason || "manual_stop",
        capture_ms: captureMs,
        speech_detected: state.speechDetected,
        chunks_sent: state.chunksSent,
        input_audio_seconds: Number(inputAudioSeconds.toFixed(3)),
        input_bytes: state.inputBytesSent
      });

      log("AUDIO_DURATION_COST_RELEVANT", {
        input_audio_seconds: Number(inputAudioSeconds.toFixed(3)),
        note: "xAI Voice Agent audio is billed by duration. This is client-side lab telemetry."
      });

      updateTiming();

      try {
        if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
          throw new Error("Cannot commit audio because the xAI WebSocket is not open.");
        }

        if (state.chunksSent <= 0) {
          sendJson({ type: "input_audio_buffer.clear" });
          log("MIC_AUDIO_CLEAR_SENT", { reason: "no_audio_chunks" });
          closeSocketQuietly("no_audio_chunks");
          setButtons("idle");
          setMicStatus("No mic audio was sent.");
          state.isCommitting = false;
          return;
        }

        sendJson({ type: "input_audio_buffer.commit" });
        log("MIC_INPUT_COMMITTED", {
          chunks_sent: state.chunksSent,
          input_audio_seconds: Number(inputAudioSeconds.toFixed(3)),
          input_bytes: state.inputBytesSent
        });

        sendJson({
          type: "response.create",
          response: {
            modalities: ["text", "audio"]
          }
        });

        log("MIC_RESPONSE_CREATE_SENT", {
          modalities: ["text", "audio"]
        });

        setMicStatus("Audio committed to xAI. Waiting for response...");
      } catch (err) {
        log("MIC_COMMIT_FAILED", {
          error: err.message || String(err)
        });
        setMicStatus("Mic commit failed: " + (err.message || err));
        closeSocketQuietly("mic_commit_failed");
        setButtons("idle");
      } finally {
        state.isCommitting = false;
      }
    }

    function handleMicAudioProcess(event) {
      if (!state.isCapturing || !state.socket || state.socket.readyState !== WebSocket.OPEN) {
        return;
      }

      const input = event.inputBuffer.getChannelData(0);
      const sourceRate = state.inputAudioContext ? state.inputAudioContext.sampleRate : event.inputBuffer.sampleRate;
      const chunkMs = (input.length / sourceRate) * 1000;
      const captureElapsed = elapsedMs(state.captureStartedAt);

      const rms = computeRms(input);
      if (rms >= LOCAL_SPEECH_RMS_THRESHOLD) {
        if (!state.speechDetected) {
          state.speechDetected = true;
          log("MIC_SPEECH_DETECTED", {
            rms: Number(rms.toFixed(5)),
            capture_elapsed_ms: captureElapsed
          });
        }
        state.quietMs = 0;
      } else if (state.speechDetected) {
        state.quietMs += chunkMs;
      }

      const resampled = resampleFloat32(input, sourceRate, INPUT_SAMPLE_RATE);
      const audioBase64 = float32ToBase64PCM16(resampled);

      try {
        sendJson({
          type: "input_audio_buffer.append",
          audio: audioBase64
        });

        state.chunksSent += 1;
        state.inputSamplesSent += resampled.length;
        state.inputBytesSent += resampled.length * 2;

        if (state.chunksSent === 1 || state.chunksSent % 12 === 0) {
          log("MIC_AUDIO_SENT", {
            first_chunk: state.chunksSent === 1,
            chunks_sent: state.chunksSent,
            input_audio_seconds: Number((state.inputSamplesSent / INPUT_SAMPLE_RATE).toFixed(3)),
            socket_buffered_amount: state.socket.bufferedAmount
          });
        }

        updateTiming();
      } catch (err) {
        log("MIC_AUDIO_SEND_FAILED", {
          error: err.message || String(err)
        });
        requestStop("audio_send_failed");
        return;
      }

      if (
        state.speechDetected &&
        captureElapsed >= MIN_CAPTURE_BEFORE_AUTO_STOP_MS &&
        state.quietMs >= AUTO_STOP_SILENCE_MS
      ) {
        log("MIC_CAPTURE_AUTO_STOP", {
          reason: "local_silence_after_speech",
          quiet_ms: Math.round(state.quietMs),
          capture_elapsed_ms: captureElapsed
        });
        requestStop("auto_silence_after_speech");
        return;
      }

      if (captureElapsed >= MAX_CAPTURE_MS) {
        log("MIC_CAPTURE_AUTO_STOP", {
          reason: "max_capture_ms",
          capture_elapsed_ms: captureElapsed
        });
        requestStop("max_capture_ms");
      }
    }

    function computeRms(float32Array) {
      let sum = 0;
      for (let i = 0; i < float32Array.length; i += 1) {
        sum += float32Array[i] * float32Array[i];
      }
      return Math.sqrt(sum / Math.max(1, float32Array.length));
    }

    function resampleFloat32(input, sourceRate, targetRate) {
      if (sourceRate === targetRate) {
        return new Float32Array(input);
      }

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
          /* Browser may require user gesture; this lab is started by a button. */
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
        type === "conversation.created" ||
        type === "session.updated" ||
        type === "input_audio_buffer.committed" ||
        type === "response.created" ||
        type === "response.done"
      ) {
        log("SERVER_EVENT " + type, {
          event_id: event.event_id,
          response_id: event.response_id,
          item_id: event.item_id
        });
      }

      if (type === "conversation.item.input_audio_transcription.updated") {
        const transcript = extractTranscript(event);
        if (transcript) {
          state.transcriptText = transcript;
          if (micInputTranscript) micInputTranscript.textContent = transcript;
        }
        return;
      }

      if (type === "conversation.item.input_audio_transcription.completed") {
        const transcript = extractTranscript(event);
        if (transcript) {
          state.transcriptText = transcript;
          if (micInputTranscript) micInputTranscript.textContent = transcript;
        }
        log("TRANSCRIPT_DONE", {
          kind: "input_audio",
          transcript: state.transcriptText || transcript || ""
        });
        return;
      }

      if (type === "response.output_audio_transcript.delta" || type === "response.text.delta" || type === "response.output_text.delta") {
        const delta = event.delta || event.text || "";
        if (delta) {
          state.assistantTranscriptText += delta;
          if (micAssistantTranscript) micAssistantTranscript.textContent = state.assistantTranscriptText;
        }
        return;
      }

      if (type === "response.output_audio_transcript.done") {
        const transcript = extractTranscript(event);
        if (transcript) {
          state.assistantTranscriptText = transcript;
          if (micAssistantTranscript) micAssistantTranscript.textContent = transcript;
        }
        log("TRANSCRIPT_DONE", {
          kind: "assistant_audio",
          transcript_chars: state.assistantTranscriptText.length
        });
        return;
      }

      if (type === "response.output_audio.delta") {
        if (!state.firstAudioDeltaAt) {
          state.firstAudioDeltaAt = elapsedMs(state.captureStartedAt);
          log("FIRST_AUDIO_DELTA", {
            first_audio_delta_ms: state.firstAudioDeltaAt,
            output_delta_bytes: base64ByteLength(event.delta || "")
          });
          setMicStatus("xAI voice response is playing...");
        }

        if (event.delta) {
          playAudioDelta(event.delta);
        }

        return;
      }

      if (type === "response.output_audio.done") {
        log("SERVER_EVENT response.output_audio.done", {
          output_audio_seconds: Number((state.outputSamplesReceived / OUTPUT_SAMPLE_RATE).toFixed(3)),
          output_bytes: state.outputBytesReceived
        });
        return;
      }

      if (type === "response.done") {
        state.responseDone = true;
        setMicStatus("xAI response done. Waiting for playback drain before clean close.");
        schedulePlaybackDrainClose();
        return;
      }

      if (type === "error") {
        log("SERVER_EVENT error", {
          code: event.code || (event.error && event.error.code),
          message: event.message || (event.error && event.error.message) || "xAI realtime error"
        });
        setMicStatus("xAI realtime error. See event log.");
      }
    }

    function schedulePlaybackDrainClose() {
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

      log("PLAYBACK_DRAIN_SCHEDULED", {
        drain_ms: drainMs,
        remaining_audio_ms: remainingMs,
        output_audio_seconds: Number((state.outputSamplesReceived / OUTPUT_SAMPLE_RATE).toFixed(3)),
        output_bytes: state.outputBytesReceived
      });

      state.playbackDrainTimer = window.setTimeout(function () {
        cleanAutoStop("playback_drain_complete");
      }, drainMs);
    }

    function closeSocketQuietly(reason) {
      if (!state.socket) return;

      try {
        if (state.socket.readyState === WebSocket.OPEN || state.socket.readyState === WebSocket.CONNECTING) {
          state.socket.close(1000, reason || "mic_lab_close");
        }
      } catch (err) {
        /* no-op */
      }
    }

    function cleanAutoStop(reason) {
      if (state.cleanAutoStopDone) return;

      state.cleanAutoStopDone = true;
      cleanupInputCapture(true);
      closeSocketQuietly(reason || "clean_auto_stop");
      setButtons("idle");
      setMicStatus("Mic lab turn complete. Ready for another controlled capture.");

      log("CLEAN_AUTO_STOP", {
        reason: reason || "clean_auto_stop",
        response_done: state.responseDone,
        input_audio_seconds: Number((state.inputSamplesSent / INPUT_SAMPLE_RATE).toFixed(3)),
        output_audio_seconds: Number((state.outputSamplesReceived / OUTPUT_SAMPLE_RATE).toFixed(3))
      });
    }

    if (micStartButton) {
      micStartButton.addEventListener("click", function () {
        startControlledMic("start_button");
      });
    }

    if (micStopButton) {
      micStopButton.addEventListener("click", function () {
        requestStop("stop_button");
      });
    }

    if (micHoldButton) {
      micHoldButton.addEventListener("pointerdown", function (event) {
        if (event.button && event.button !== 0) return;
        event.preventDefault();
        startControlledMic("hold_button_down");
      });

      ["pointerup", "pointercancel", "pointerleave"].forEach(function (eventName) {
        micHoldButton.addEventListener(eventName, function (event) {
          event.preventDefault();
          requestStop("hold_button_release");
        });
      });
    }

    setButtons("idle");
    setMicStatus("Mic lab ready. Use Start/Stop or hold-to-speak.");
    updateTiming();
  });
})();
