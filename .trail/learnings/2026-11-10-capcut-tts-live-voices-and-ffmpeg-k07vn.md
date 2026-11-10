---
title: CapCut TTS live voices and FFmpeg static PNG overlay loop
author: K07VN
date: 2026-11-10
id: l-b30075ec
scope: repo
status: active
src: agent
touches:
  - src/threadstudio/tts/router.py
  - src/threadstudio/render/ffmpeg_compositor.py
---

In src/threadstudio/tts/router.py (TTSRouter), K07VN/capcut-tts-api provides 22 live Vietnamese voices without needing device.json, whereas its 2 legacy i-VN-*Neural aliases fail with TTSInvalidSpeaker and edge-tts rejects skewed system clocks via Sec-MS-GEC; filter out legacy aliases and fall back to capcut:BV075_streaming / capcut:BV421_vivn_streaming. In src/threadstudio/render/ffmpeg_compositor.py (_run_ffmpeg_compositor), static card PNG inputs require -loop 1 -t <total_dur> before -i because single-frame PNG streams hit EOF after frame 1 when eof_action=pass is set.
