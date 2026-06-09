(function () {
  "use strict";

  const FIRST_SPEECH_TIMEOUT_MS = 20000;
  const POST_RESPONSE_IDLE_TIMEOUT_MS = 30000;
  const MAX_SESSION_TIMEOUT_MS = 180000;

  const startHathorButton = document.getElementById("startHathorButton");
  const startMosesButton = document.getElementById("startMosesButton");
  const stopButton = document.getElementById("stopButton");
  const statusEl = document.getElementById("status");
  const eventLog = document.getElementById("eventLog");
  const remoteAudio = document.getElementById("remoteAudio");

  let peerConnection = null;
  let dataChannel = null;
  let localStream = null;
  let sessionStartedAt = null;
  let firstTrackAt = null;
  let firstPlayingAt = null;
  let activeDeity = null;
  let sawSpeech = false;

  let firstSpeechTimer = null;
  let postResponseIdleTimer = null;
  let maxSessionTimer = null;

  function nowMs() {
    return Math.round(performance.now());
  }

  function elapsed() {
    if (!sessionStartedAt) return "0ms";
    return String(nowMs() - sessionStartedAt) + "ms";
  }

  function setStatus(message) {
    statusEl.textContent = message;
    log("STATUS " + message);
  }

  function redactForLog(value) {
    const clone = JSON.parse(JSON.stringify(value));

    if (clone.session && clone.session.instructions) {
      clone.session.instructions = "[redacted in lab log]";
    }

    if (clone.response && clone.response.instructions) {
      clone.response.instructions = "[redacted in lab log]";
    }

    return clone;
  }

  function log(message, payload) {
    const line = "[" + new Date().toISOString() + " +" + elapsed() + "] " + message;
    eventLog.textContent += line + "\n";

    if (payload !== undefined) {
      try {
        eventLog.textContent += JSON.stringify(redactForLog(payload), null, 2) + "\n";
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
  }

  function clearFirstSpeechTimer() {
    if (firstSpeechTimer) {
      clearTimeout(firstSpeechTimer);
      firstSpeechTimer = null;
    }
  }

  function clearPostResponseIdleTimer() {
    if (postResponseIdleTimer) {
      clearTimeout(postResponseIdleTimer);
      postResponseIdleTimer = null;
    }
  }

  function clearMaxSessionTimer() {
    if (maxSessionTimer) {
      clearTimeout(maxSessionTimer);
      maxSessionTimer = null;
    }
  }

  function clearSessionTimers() {
    clearFirstSpeechTimer();
    clearPostResponseIdleTimer();
    clearMaxSessionTimer();
  }

  function scheduleFirstSpeechTimeout() {
    clearFirstSpeechTimer();

    firstSpeechTimer = setTimeout(function () {
      if (!sawSpeech) {
        log("AUTO_STOP first_speech_timeout");
        stopRealtime("auto_stop_no_first_speech");
      }
    }, FIRST_SPEECH_TIMEOUT_MS);
  }

  function schedulePostResponseIdleTimeout() {
    clearPostResponseIdleTimer();

    postResponseIdleTimer = setTimeout(function () {
      log("AUTO_STOP post_response_idle_timeout");
      stopRealtime("auto_stop_post_response_idle");
    }, POST_RESPONSE_IDLE_TIMEOUT_MS);
  }

  function scheduleMaxSessionTimeout() {
    clearMaxSessionTimer();

    maxSessionTimer = setTimeout(function () {
      log("AUTO_STOP max_session_timeout");
      stopRealtime("auto_stop_max_session");
    }, MAX_SESSION_TIMEOUT_MS);
  }

  function extractEphemeralKey(sessionData) {
    if (sessionData && typeof sessionData.client_secret === "string") {
      return sessionData.client_secret;
    }

    if (sessionData && sessionData.client_secret && typeof sessionData.client_secret.value === "string") {
      return sessionData.client_secret.value;
    }

    if (sessionData && typeof sessionData.value === "string") {
      return sessionData.value;
    }

    return "";
  }

  function safeSessionSummary(sessionData) {
    return {
      provider: sessionData.provider,
      model: sessionData.model,
      deity: sessionData.deity,
      realtime_voice: sessionData.realtime_voice,
      expires_at: sessionData.expires_at,
      session_id: sessionData.session_id,
      fallback_mode: sessionData.fallback_mode,
      transport: sessionData.transport
    };
  }

  function usageSummary(responseDoneEvent) {
    const response = responseDoneEvent.response || {};
    const usage = response.usage || {};
    const inputDetails = usage.input_token_details || {};
    const outputDetails = usage.output_token_details || {};

    return {
      response_id: response.id,
      status: response.status,
      incomplete_reason: response.status_details && response.status_details.reason,
      total_tokens: usage.total_tokens,
      input_tokens: usage.input_tokens,
      output_tokens: usage.output_tokens,
      input_text_tokens: inputDetails.text_tokens,
      input_audio_tokens: inputDetails.audio_tokens,
      output_text_tokens: outputDetails.text_tokens,
      output_audio_tokens: outputDetails.audio_tokens
    };
  }

  async function createRealtimeBrokerSession(deity) {
    const response = await fetch("/voice/realtime/session", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      credentials: "same-origin",
      body: JSON.stringify({
        voice: deity,
        provider: "openai"
      })
    });

    const text = await response.text();
    let data = null;

    try {
      data = JSON.parse(text);
    } catch (error) {
      throw new Error("Broker returned non-JSON response: " + text.slice(0, 500));
    }

    if (!response.ok) {
      throw new Error(data.error || "Realtime broker failed with HTTP " + response.status);
    }

    return data;
  }

  async function startRealtime(deity) {
    await stopRealtime("restart");

    sessionStartedAt = nowMs();
    firstTrackAt = null;
    firstPlayingAt = null;
    activeDeity = deity;
    sawSpeech = false;
    eventLog.textContent = "";

    setButtons(true);
    setStatus("Creating " + deity + " realtime session.");

    try {
      scheduleMaxSessionTimeout();

      const sessionData = await createRealtimeBrokerSession(deity);
      const ephemeralKey = extractEphemeralKey(sessionData);

      log("BROKER_SESSION_OK", safeSessionSummary(sessionData));

      if (!ephemeralKey) {
        throw new Error("Broker response did not include a usable ephemeral realtime key.");
      }

      setStatus("Opening microphone. Mic will be live until Stop or auto-stop.");
      localStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      });

      setStatus("Creating WebRTC peer connection.");
      peerConnection = new RTCPeerConnection();

      peerConnection.onconnectionstatechange = function () {
        log("PEER_CONNECTION_STATE " + peerConnection.connectionState);

        if (peerConnection.connectionState === "connected") {
          setStatus("Mic is live. Speak naturally, or tap Stop.");
        }

        if (
          peerConnection.connectionState === "failed" ||
          peerConnection.connectionState === "disconnected" ||
          peerConnection.connectionState === "closed"
        ) {
          setStatus("Realtime connection " + peerConnection.connectionState + ".");
        }
      };

      peerConnection.oniceconnectionstatechange = function () {
        log("ICE_CONNECTION_STATE " + peerConnection.iceConnectionState);
      };

      peerConnection.ontrack = function (event) {
        if (!firstTrackAt) {
          firstTrackAt = nowMs();
          log("FIRST_REMOTE_AUDIO_TRACK at " + String(firstTrackAt - sessionStartedAt) + "ms");
        }

        remoteAudio.srcObject = event.streams[0];
        remoteAudio.play().catch(function (error) {
          log("REMOTE_AUDIO_PLAY_WARNING " + error.message);
        });
      };

      remoteAudio.onplaying = function () {
        if (!firstPlayingAt) {
          firstPlayingAt = nowMs();
          log("REMOTE_AUDIO_PLAYING at " + String(firstPlayingAt - sessionStartedAt) + "ms");
        }
      };

      localStream.getTracks().forEach(function (track) {
        peerConnection.addTrack(track, localStream);
      });

      dataChannel = peerConnection.createDataChannel("oai-events");

      dataChannel.onopen = function () {
        log("DATA_CHANNEL_OPEN");
      };

      dataChannel.onclose = function () {
        log("DATA_CHANNEL_CLOSED");
      };

      dataChannel.onerror = function (event) {
        log("DATA_CHANNEL_ERROR", event.message || event);
      };

      dataChannel.onmessage = function (event) {
        let parsed = null;

        try {
          parsed = JSON.parse(event.data);
        } catch (error) {
          log("DATA_CHANNEL_MESSAGE_RAW " + String(event.data).slice(0, 500));
          return;
        }

        const type = parsed.type || "unknown";

        if (type === "input_audio_buffer.speech_started") {
          sawSpeech = true;
          clearFirstSpeechTimer();
          clearPostResponseIdleTimer();
          setStatus("Mic is live. Listening to your question.");
        }

        if (type === "input_audio_buffer.speech_stopped") {
          setStatus("Speech captured. The Oracle is responding.");
        }

        if (type === "response.created") {
          clearPostResponseIdleTimer();
          setStatus("The Oracle is speaking. Mic remains live; you may interrupt.");
        }

        if (type === "response.done") {
          const summary = usageSummary(parsed);
          log("USAGE_SUMMARY", summary);

          if (summary.status === "incomplete") {
            log("RESPONSE_INCOMPLETE " + (summary.incomplete_reason || "unknown"));
            setStatus("Answer ended incomplete. This needs tuning. Mic is still live.");
          } else {
            setStatus("Answer complete. Mic is still live. Ask again or tap Stop.");
          }

          schedulePostResponseIdleTimeout();
        }

        if (
          type === "session.created" ||
          type === "session.updated" ||
          type === "input_audio_buffer.speech_started" ||
          type === "input_audio_buffer.speech_stopped" ||
          type === "input_audio_buffer.timeout_triggered" ||
          type === "response.created" ||
          type === "response.done" ||
          type === "response.cancelled" ||
          type === "output_audio_buffer.started" ||
          type === "output_audio_buffer.stopped" ||
          type === "error"
        ) {
          log("SERVER_EVENT " + type, parsed);
        } else {
          log("SERVER_EVENT " + type);
        }
      };

      setStatus("Creating local SDP offer.");
      const offer = await peerConnection.createOffer();
      await peerConnection.setLocalDescription(offer);

      setStatus("Calling OpenAI Realtime.");
      const sdpResponse = await fetch("https://api.openai.com/v1/realtime/calls", {
        method: "POST",
        body: offer.sdp,
        headers: {
          "Authorization": "Bearer " + ephemeralKey,
          "Content-Type": "application/sdp"
        }
      });

      const answerSdp = await sdpResponse.text();

      if (!sdpResponse.ok) {
        throw new Error("OpenAI realtime SDP failed with HTTP " + sdpResponse.status + ": " + answerSdp.slice(0, 1000));
      }

      await peerConnection.setRemoteDescription({
        type: "answer",
        sdp: answerSdp
      });

      setStatus("Mic is live. Speak naturally, or tap Stop.");
      scheduleFirstSpeechTimeout();
      log("REMOTE_DESCRIPTION_SET");
    } catch (error) {
      log("START_REALTIME_ERROR " + error.message);
      setStatus("Realtime failed: " + error.message);
      await stopRealtime("start_failed");
    }
  }

  async function stopRealtime(reason) {
    const stopReason = reason || "manual_stop";
    const hadActiveSession = Boolean(dataChannel || peerConnection || localStream || (remoteAudio && remoteAudio.srcObject));

    clearSessionTimers();

    if (dataChannel) {
      try {
        dataChannel.close();
      } catch (error) {}
      dataChannel = null;
    }

    if (peerConnection) {
      try {
        peerConnection.close();
      } catch (error) {}
      peerConnection = null;
    }

    if (localStream) {
      localStream.getTracks().forEach(function (track) {
        track.stop();
      });
      localStream = null;
    }

    if (remoteAudio) {
      remoteAudio.pause();
      remoteAudio.srcObject = null;
      remoteAudio.removeAttribute("src");
      remoteAudio.load();
    }

    if (hadActiveSession) {
      log("SESSION_STOPPED reason=" + stopReason + " deity=" + (activeDeity || "unknown"));
    }

    activeDeity = null;
    sawSpeech = false;
    setButtons(false);
    setStatus("Idle.");
  }

  startHathorButton.addEventListener("click", function () {
    startRealtime("Hathor");
  });

  startMosesButton.addEventListener("click", function () {
    startRealtime("Moses");
  });

  stopButton.addEventListener("click", function () {
    stopRealtime("manual_stop");
  });

  window.addEventListener("beforeunload", function () {
    stopRealtime("page_unload");
  });
})();
