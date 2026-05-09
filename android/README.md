# voci — Android

System-wide subtitle overlay for Android. Mirrors the desktop pipeline: captures
system playback audio, streams to Deepgram for STT, translates committed text
via MyMemory, and shows the result as a floating two-line overlay.

## Architecture

```
[MediaProjection + AudioPlaybackCaptureConfiguration]
    │  16 kHz mono PCM, ~25 ms frames
    ▼
[SystemAudioCapture]  (audio/SystemAudioCapture.kt)
    │
    ▼
[DeepgramStreamingClient]  (stt/DeepgramStreamingClient.kt)
    │  partial + final transcripts
    ▼
[VociService]  (foreground service, glue)
    │  partial → top line; commit → translate
    ▼
[MyMemoryClient]  (translate/MyMemoryClient.kt)
    │  translated text
    ▼
[OverlayManager]  (overlay/OverlayManager.kt; SYSTEM_ALERT_WINDOW)
```

`PaginatedLine` (util/PaginatedLine.kt) implements the same 10-words-per-page
display behavior as the desktop. Top line shows English with the live partial
appended; bottom line shows the accumulated Arabic translation.

## Build & install

Requires Android Studio (Iguana / Jellyfish or newer) with the Android SDK
installed. Open the `android/` folder as a Gradle project and let Studio sync.

```
./gradlew :app:installDebug
```

Tested on minSdk 29 (Android 10) — `AudioPlaybackCaptureConfiguration` requires
this. `compileSdk = 35`.

## Runtime flow

1. Launch the app. Paste your Deepgram API key, set the target language code
   (default `ar`), tap **Start**.
2. The app prompts for **Display over other apps** if not already granted —
   approve in Settings, return to the app, tap Start again.
3. The system shows the **MediaProjection consent dialog** ("voci wants to
   start capturing what's on your screen"). Approve.
4. A persistent notification appears. The two-line overlay shows up at the
   bottom of the screen.
5. Play any audio that doesn't opt out (most YouTube, Spotify, podcasts work;
   DRM'd Netflix/Disney+ won't).
6. Pull down the notification and tap **Stop** to terminate.

## What works / what doesn't

- ✅ Most apps' audio (YouTube, Spotify, Twitch, Discord)
- ✅ Calls IF the call app allows playback capture (most don't — privacy)
- ❌ DRM-protected video apps (Netflix, Disney+, prime premium)
- ❌ Mic input — this captures **playback** not microphone. For a different mode
  you'd swap `AudioPlaybackCaptureConfiguration` for `MediaRecorder.AudioSource.MIC`.

## Free-tier limits

- **Deepgram**: $200 starter credit ≈ ~750 hours of streaming (~12+ months of
  daily personal use). After that ~$0.006/min.
- **MyMemory**: 5,000 chars/day anonymous, 50,000 chars/day if you set
  `MyMemoryClient(email = "your@email")`.

## Files

- `app/src/main/AndroidManifest.xml` — permissions, service registration
- `app/src/main/kotlin/co/voci/MainActivity.kt` — settings UI + permission flow
- `app/src/main/kotlin/co/voci/VociService.kt` — foreground service glue
- `app/src/main/kotlin/co/voci/audio/SystemAudioCapture.kt` — system audio loop
- `app/src/main/kotlin/co/voci/stt/DeepgramStreamingClient.kt` — Deepgram WS
- `app/src/main/kotlin/co/voci/translate/MyMemoryClient.kt` — MyMemory HTTP
- `app/src/main/kotlin/co/voci/overlay/OverlayManager.kt` — floating window
- `app/src/main/kotlin/co/voci/util/PaginatedLine.kt` — 10-words paginator
- `app/src/main/res/layout/activity_main.xml` — settings screen
- `app/src/main/res/layout/overlay_lines.xml` — overlay layout

## Known gaps in this scaffold (next steps when iterating)

- No drag-to-move on overlay yet (desktop has it; would need to flip
  `FLAG_NOT_TOUCHABLE` off + handle MotionEvents and rewrite `params.x/y`)
- No clear/toggle hotkey — Android lacks global hotkeys; use the notification
  action button instead, or add a draggable bubble (BubbleService)
- No silence-based auto-clear — easy: track last activity timestamp in
  `VociService` and reset `englishLine` / `arabicLine` after N seconds
- Deepgram SDK could replace the OkHttp WebSocket if you want their official
  client; current direct-WS implementation matches the desktop's settings exactly
