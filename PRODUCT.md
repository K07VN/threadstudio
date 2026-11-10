# PRODUCT.md — Threads Studio Pro

## What It Is
**Threads Studio Pro** is a high-throughput Windows desktop production studio for Vietnamese short-form creators who turn Threads discussions and multi-part stories into `9:16` (`1080x1920`) narrated videos for TikTok, Reels, and YouTube Shorts.

## Primary User & Scene
- **Who**: Solo channel operators and multi-channel content teams producing 5–50 vertical videos per session.
- **Scene**: Desk workstation (`1920x1080` or `2560x1440`), extended editing sessions under dim/indoor lighting (Dark theme default, Light theme one click away).
- **Surface Mode**: **Operate** — speed, scanability, batch visibility, inline review without modal lockups, and direct canvas manipulation take priority over decorative chrome.

## Core Capabilities
1. **Batch Ingestion & Scraping**: Accept 1 to 100+ Threads URLs (or manual story scripts) simultaneously via Playwright persistent login, filtering spam/affiliate replies across 3 selection modes (*Author Only*, *Top Likes excl. Author*, *Top Likes incl. Author*).
2. **Vietnamese Teencode & Image Normalization**: Expand Vietnamese abbreviations/slang via Gemini Flash Vision API + deterministic offline Vietnamese teencode dictionary.
3. **Inline Script Inspector**: Reorder segments, toggle inclusion, edit spoken text, preview individual voice lines, and see live Threads card renders side-by-side without blocking background render workers.
4. **Multi-Engine AI Voice Router**: Mix and match voices from `K07VN/capcut-tts-api` (24 Vietnamese streaming voices), `edge-tts` (`Hoài My`, `Nam Minh`), and `pnnbao97/VieNeu-TTS` (local 48kHz + voice cloning) within a single video.
5. **Direct `9:16` Stage & GPU FFmpeg Compositor**: Drag/resize card and B-roll regions directly on a `9:16` preview stage and render `1080x1920` H.264 MP4s concurrently via `h264_nvenc` / `libx264`.

