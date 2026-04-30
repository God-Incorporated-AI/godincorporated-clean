# Phase 10.5 Mobile API Contract

## Purpose

Phase 10.5 prepares God Incorporated for mobile app development by documenting the split voice API, TTS policy, and usage reporting foundation.

## Current voice pipeline

The current mobile-ready voice flow is:

record voice
transcribe
ask Oracle
prepare or play TTS

## Endpoints

### POST /voice/transcribe

Request:

multipart/form-data
file: audio file
voice: Hathor or Moses

Response:

question: transcribed text
transcript: transcribed text

Mobile behavior:

Show the transcript immediately as the seeker's message.

### POST /voice/ask

Request:

question: transcribed or typed question
deity: Hathor or Moses
anonymous_user_id: optional browser/device/session UUID
seeker_id: optional legacy seeker identifier

Response:

answer: Oracle response text

Mobile behavior:

Show the text answer immediately. Do not wait for TTS audio.

### POST /voice/tts

Request:

answer: Oracle response text
voice: Hathor or Moses

Response:

audio_url: /audio/example.mp3

Mobile behavior:

Treat TTS as a follow-on stage. Play automatically only if the native mobile audio session allows it. Otherwise show a Play Oracle Voice control.

### GET /audio/{filename}

Returns generated MP3 audio.

The mobile app may stream or play the audio URL returned by /voice/tts.

## TTS policy recommendation

Text should always appear first.

TTS should remain optional or deferred for cost and latency control.

Recommended future policy:

browser web: text first, TTS optional or deferred
native mobile: text first, autoplay only if permissions allow
lower tiers: consider on-demand TTS
higher tiers: consider automatic TTS
long answers: consider TTS only when requested

## Phase 10.5 reporting foundation

The new tables support future cost and performance reporting:

oracle_usage_events
voice_usage_events

These allow reporting by:

user
plan
deity
provider
model
input mode
tokens
voice timing
TTS timing
date

## Staging validation

Phase 10.5 staging validation confirmed:

oracle_usage_events persists text asks
oracle_usage_events persists voice asks
voice_usage_events persists transcribe stages
voice_usage_events persists TTS stages

A real staging voice test showed roughly:

transcription: 18 seconds
Oracle ask path: 20 seconds
TTS: 6 seconds
full voice path: about 44 seconds

This confirms the split pipeline works, while also showing that voice latency still needs product attention.

## Deferred work

Do not start native mobile implementation until after production promotion and Phase 10.5 verification.

Deferred:

native mobile app
admin reporting UI
conversation_turns table
TTS tier gating
audio retention policy
legacy /whisper removal
realtime streaming voice
browser silence detection
Ollama/LLaMA return path
