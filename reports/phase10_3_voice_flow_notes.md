# Phase 10.3 Voice Flow Notes

## Current web voice objective

The web voice flow should behave like a simple conversational bridge: tap Speak, speak naturally, tap Stop, see the transcript, receive the Oracle answer, and hear the Oracle voice without exposing an ugly audio-file workflow.

## Current browser strategy

- Use tap-to-start and tap-to-stop recording for reliability on mobile browsers.
- Keep microphone lifetime explicit and short.
- Stop media tracks immediately after recording stops.
- Keep the transcribed seeker question visible.
- Use a hidden reusable audio element for playback.
- Use a clean Play Oracle Voice button for replay or autoplay fallback.

## Future-state objective

Move toward realtime or streaming voice for the mobile app: lower-latency turn taking, native microphone lifecycle control, cleaner interruption handling, and a voice-first chatbot experience without browser audio limitations.

## Deferred

- Browser silence detection.
- Realtime audio streaming.
- Native mobile app voice session architecture.
- Separate dedicated voice status UI component.
