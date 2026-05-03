# RoundTable Web Smoke Checklist

Last updated: 2026-05-03

## Scope

- ASR smoke: 1 case
- TTS smoke: 1 case
- Replay smoke: 1 case

## Precheck (2-3 minutes)

1. Backend is running on port 8001.
2. Web static assets are up to date.
3. Dev panel is running on port 8888.
4. Browser microphone permission is granted for localhost.

Suggested commands:

```bash
cd /Users/m3max/IdeaProjects/RoundTable
./build_web.sh
node devpanel.js
```

Quick URLs:

- App: [http://localhost:8001](http://localhost:8001)
- Admin: [http://localhost:8001/admin/](http://localhost:8001/admin/)
- Dev panel: [http://localhost:8888](http://localhost:8888)
- Browser ASR test page: [http://localhost:8001/browser-asr-test.html](http://localhost:8001/browser-asr-test.html)

## Smoke Case 1: ASR (browser-side)

Goal: verify microphone capture + browser/web streaming recognition path still works.

Steps:

1. Open the app in Chrome: [http://localhost:8001](http://localhost:8001)
2. Enter immersive session.
3. Ensure ASR provider is one of: funasr / capswriter / browser.
4. Press and hold your push-to-talk key (default is Right Alt) and say:
   "今天我想从环保角度讨论这个问题。"
5. Release the key.

Pass criteria:

- During speech, interim text appears (for streaming providers).
- After release, final text is committed and visible in session transcript.
- No permission error or ASR exception toast.

Common fail hints:

- If no text appears, verify browser microphone permission.
- If provider is funasr/capswriter, verify local service endpoint is reachable.
- If hotkey does not trigger, check microphone hotkey mapping in settings.

## Smoke Case 2: TTS (browser playback)

Goal: verify synthesis response + browser playback path works with current provider.

Steps:

1. Stay in immersive session.
2. Trigger one AI reply (or send one short user line and wait for AI response).
3. Observe the first AI sentence playback.

Pass criteria:

- Audio starts within a few seconds.
- Subtitle and speaker state are aligned with playback.
- Playback finishes cleanly (no stuck speaking state).

Optional API probe (backend path only):

```bash
curl -sS -X POST http://localhost:8001/api/v1/voice/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"RoundTable TTS smoke test","provider":"edge_tts"}' \
  -o /tmp/roundtable_tts_smoke.bin && ls -lh /tmp/roundtable_tts_smoke.bin
```

## Smoke Case 3: Replay

Goal: verify replay ZIP load + audio playback (recording first, TTS fallback) still works.

Steps:

1. Open replay page: [http://localhost:8001/#/replay](http://localhost:8001/#/replay)
2. Load a replay ZIP exported from dev panel history.
3. Click play.
4. Let at least one line with recording and one line with fallback playback run.

Pass criteria:

- ZIP is parsed successfully.
- Timeline advances line by line.
- Audio plays for available recordings.
- Missing recording lines can still be played via fallback path.

## Final Gate

Mark this build as Web smoke PASS only if all three cases pass.

- [ ] ASR pass
- [ ] TTS pass
- [ ] Replay pass

If any case fails, capture:

1. provider used
2. exact action step
3. browser console/network error summary
4. backend log excerpt around failure time
