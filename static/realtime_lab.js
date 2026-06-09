(function () {
  "use strict";

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
    await stopRealtime();

    sessionStartedAt = nowMs();
    firstTrackAt = null;
    firstPlayingAt = null;
    eventLog.textContent = "";

    setButtons(true);
    setStatus("Creating " + deity + " realtime session.");

    try {
      const sessionData = await createRealtimeBrokerSession(deity);
      const ephemeralKey = extractEphemeralKey(sessionData);

      log("BROKER_SESSION_OK", safeSessionSummary(sessionData));

      if (!ephemeralKey) {
        throw new Error("Broker response did not include a usable ephemeral realtime key.");
      }

      setStatus("Opening microphone.");
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
          setStatus("Realtime connected. Speak naturally.");
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
        if (
          type === "session.created" ||
          type === "session.updated" ||
          type === "input_audio_buffer.speech_started" ||
          type === "input_audio_buffer.speech_stopped" ||
          type === "input_audio_buffer.timeout_triggered" ||
          type === "response.created" ||
          type === "response.done" ||
          type === "response.cancelled" ||
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

      setStatus("Realtime session ready. Speak naturally.");
      log("REMOTE_DESCRIPTION_SET");
    } catch (error) {
      log("START_REALTIME_ERROR " + error.message);
      setStatus("Realtime failed: " + error.message);
      await stopRealtime();
    }
  }

  async function stopRealtime() {
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
    stopRealtime();
  });

  window.addEventListener("beforeunload", function () {
    stopRealtime();
  });
})();
