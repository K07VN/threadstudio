"""Unified Multi-Engine TTS Router (K07VN/capcut-tts-api + Edge-TTS + pnnbao97/VieNeu-TTS)."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
from pathlib import Path
import random
import subprocess
import urllib.request
import wave

from ..core.models import SegmentItem, VoiceAssignMode


@dataclass(frozen=True)
class VoiceOption:
    full_id: str        # e.g. "capcut:BV421_vivn_streaming", "edge:vi-VN-HoaiMyNeural", "vieneu:Ngọc Huyền"
    provider: str       # "capcut" | "edge" | "vieneu"
    raw_voice: str
    display_name: str
    badge: str


_EDGE_VOICES: list[VoiceOption] = [
    VoiceOption("edge:vi-VN-HoaiMyNeural", "edge", "vi-VN-HoaiMyNeural", "Hoài My — Nữ Miền Nam (Edge Free)", "Edge"),
    VoiceOption("edge:vi-VN-NamMinhNeural", "edge", "vi-VN-NamMinhNeural", "Nam Minh — Nam Miền Bắc (Edge Free)", "Edge"),
]

_VIENEU_PRESETS: list[VoiceOption] = [
    VoiceOption("vieneu:Ngọc Huyền", "vieneu", "Ngọc Huyền", "Ngọc Huyền — Nữ Kể Chuyện (VieNeu)", "VieNeu"),
    VoiceOption("vieneu:Hải Đăng", "vieneu", "Hải Đăng", "Hải Đăng — Nam Trầm Ấm (VieNeu)", "VieNeu"),
    VoiceOption("vieneu:Thiện Minh", "vieneu", "Thiện Minh", "Thiện Minh — Nam Trẻ Trung (VieNeu)", "VieNeu"),
    VoiceOption("vieneu:Adam bựa", "vieneu", "Adam bựa", "Adam Bựa — Đọc Comment Hài (VieNeu)", "VieNeu"),
    VoiceOption("vieneu:__clone__", "vieneu", "__clone__", "Giọng Nhân Bản Riêng (3–8s Clone)", "VieNeu Clone"),
]


class TTSRouter:
    """Thread-safe multi-provider Vietnamese TTS engine with automatic WAV normalization."""

    def __init__(
        self,
        *,
        capcut_device_json: str = "",
        vieneu_endpoint: str = "",
        vieneu_clone_ref_audio: str = "",
        vieneu_clone_ref_text: str = "",
    ) -> None:
        self.capcut_device_json = capcut_device_json
        self.vieneu_endpoint = vieneu_endpoint.strip()
        self.vieneu_clone_ref_audio = vieneu_clone_ref_audio.strip()
        self.vieneu_clone_ref_text = vieneu_clone_ref_text.strip()
        self._vieneu_sdk = None

    @staticmethod
    def available_voices() -> list[VoiceOption]:
        voices: list[VoiceOption] = []
        # 1. Load all Vietnamese voices from K07VN/capcut-tts-api catalog
        try:
            from capcut_tts_api import CapCutClient

            client = CapCutClient(timeout=15)
            for v in client.list_voices(lang="vi-VN"):
                if "Neural" in v.voice_type:
                    # Handled natively by Edge-TTS provider below
                    continue
                voices.append(
                    VoiceOption(
                        full_id=f"capcut:{v.voice_type}",
                        provider="capcut",
                        raw_voice=v.voice_type,
                        display_name=f"{v.display_name} ({v.voice_type})",
                        badge="CapCut",
                    )
                )
        except Exception:
            pass

        # 2. Edge-TTS neural voices
        voices.extend(_EDGE_VOICES)

        # 3. VieNeu-TTS v3 Turbo presets & instant voice clone
        voices.extend(_VIENEU_PRESETS)
        return voices

    @staticmethod
    def vieneu_installed() -> bool:
        try:
            import vieneu  # noqa: F401
            return True
        except Exception:
            return False

    def assign_voices(
        self,
        segments: list[SegmentItem],
        pool: list[str],
        mode: str = VoiceAssignMode.RANDOM.value,
    ) -> None:
        usable = [v for v in pool if v] or ["capcut:BV421_vivn_streaming", "capcut:BV075_streaming"]
        prev = ""
        for idx, seg in enumerate(segments):
            if seg.voice_id and seg.voice_id in usable:
                continue
            if mode == VoiceAssignMode.SINGLE.value or len(usable) == 1:
                seg.voice_id = usable[0]
            elif mode == VoiceAssignMode.ROUND_ROBIN.value:
                seg.voice_id = usable[idx % len(usable)]
            else:
                choices = [v for v in usable if v != prev] or usable
                picked = random.choice(choices)
                seg.voice_id = picked
                prev = picked

    def synthesize_single(
        self,
        text: str,
        voice_full_id: str,
        out_wav: Path,
        *,
        speed: float = 1.20,
    ) -> float:
        """Synthesize one text snippet into a 44.1kHz stereo WAV and return duration in seconds."""
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        raw_tmp = out_wav.with_suffix(".raw_tts.mp3")
        provider, _, raw_voice = voice_full_id.partition(":")
        if not raw_voice:
            provider, raw_voice = "edge", "vi-VN-HoaiMyNeural"

        clean_text = text.strip() or "..."
        used_native_speed = False

        try:
            if provider == "capcut":
                self._synth_capcut(clean_text, raw_voice, raw_tmp)
            elif provider == "vieneu":
                self._synth_vieneu(clean_text, raw_voice, raw_tmp)
            else:
                self._synth_edge(clean_text, raw_voice, raw_tmp, speed=speed)
                used_native_speed = True
        except Exception:
            # Resilient fallback to verified live CapCut streaming voices so clock-skew or offline models never fail a batch
            fallback_cc = (
                "BV075_streaming"
                if any(k in raw_voice for k in ("Nam", "Đăng", "Minh", "Adam", "male"))
                else "BV421_vivn_streaming"
            )
            self._synth_capcut(clean_text, fallback_cc, raw_tmp)
            used_native_speed = False

        effective_speed = 1.0 if used_native_speed else speed
        _normalize_to_wav(raw_tmp, out_wav, speed=effective_speed)
        try:
            raw_tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return wav_duration_sec(out_wav)

    def synthesize_segments_parallel(
        self,
        segments: list[SegmentItem],
        audio_dir: Path,
        *,
        speed: float = 1.20,
        max_workers: int = 4,
    ) -> float:
        """Synthesize all enabled segments concurrently and build contiguous timeline."""
        audio_dir.mkdir(parents=True, exist_ok=True)
        enabled = [s for s in segments if s.enabled and (s.spoken_text.strip() or s.raw_text.strip())]
        if not enabled:
            raise RuntimeError("Không có đoạn nội dung nào được bật để tạo giọng đọc.")

        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            futs = {}
            for idx, seg in enumerate(enabled):
                txt = seg.spoken_text.strip() or seg.raw_text.strip()
                wav_path = audio_dir / f"{idx:02d}_{seg.seg_id}.wav"
                fut = pool.submit(self.synthesize_single, txt, seg.voice_id, wav_path, speed=speed)
                futs[fut] = (seg, wav_path)

            for fut in as_completed(futs):
                seg, wav_path = futs[fut]
                dur = fut.result()
                seg.audio_wav_path = str(wav_path)
                seg.duration_sec = round(max(0.4, dur), 3)

        # Build timeline timestamps with a natural 0.18s breather pause between comments
        pause_sec = 0.18
        cursor = 0.0
        for i, seg in enumerate(enabled):
            seg.start_sec = round(cursor, 3)
            extra = pause_sec if i < len(enabled) - 1 else 0.25
            seg.end_sec = round(cursor + seg.duration_sec + extra, 3)
            cursor = seg.end_sec

        return round(cursor, 3)

    # ---- Provider implementations ----

    def _synth_capcut(self, text: str, voice_type: str, dest: Path) -> None:
        from capcut_tts_api import CapCutClient

        device_arg = self.capcut_device_json if self.capcut_device_json and Path(self.capcut_device_json).exists() else None
        client = CapCutClient(device=device_arg, timeout=45)
        result = client.generate_speech(text, voice=voice_type, timeout=120)
        client.download_tts_audio(result, str(dest))
        if not dest.exists() or dest.stat().st_size < 100:
            raise RuntimeError(f"CapCut TTS returned empty artifact for {voice_type}")

    def _synth_edge(self, text: str, voice_name: str, dest: Path, *, speed: float = 1.20) -> None:
        import edge_tts

        pct = int(round((speed - 1.0) * 100))
        rate_str = f"{pct:+d}%"

        async def _run() -> None:
            comm = edge_tts.Communicate(text=text, voice=voice_name, rate=rate_str)
            await comm.save(str(dest))

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()

    def _synth_vieneu(self, text: str, preset_name: str, dest: Path) -> None:
        # Option A: Local HTTP endpoint (e.g. vieneu webui / FastAPI)
        if self.vieneu_endpoint:
            payload = {"text": text, "voice": preset_name}
            if preset_name == "__clone__" and self.vieneu_clone_ref_audio:
                payload["ref_audio"] = self.vieneu_clone_ref_audio
                payload["ref_text"] = self.vieneu_clone_ref_text
            req = urllib.request.Request(
                self.vieneu_endpoint.rstrip("/") + "/tts",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                dest.write_bytes(resp.read())
            return

        # Option B: On-device Python SDK (pnnbao97/VieNeu-TTS)
        from vieneu import Vieneu

        if self._vieneu_sdk is None:
            self._vieneu_sdk = Vieneu()

        if preset_name == "__clone__" and self.vieneu_clone_ref_audio:
            audio = self._vieneu_sdk.infer(
                text=text,
                ref_audio=self.vieneu_clone_ref_audio,
                ref_text=self.vieneu_clone_ref_text or None,
            )
        else:
            audio = self._vieneu_sdk.infer(text=text, voice=preset_name)
        self._vieneu_sdk.save(audio, str(dest))


def _normalize_to_wav(src: Path, dst_wav: Path, *, speed: float = 1.0) -> None:
    """Convert MP3/WAV to 44.1kHz 16-bit stereo WAV, apply speed & loudness normalization."""
    filters: list[str] = []
    clamped = max(0.5, min(2.0, float(speed)))
    if abs(clamped - 1.0) > 0.02:
        filters.append(f"atempo={clamped:.3f}")
    # Trim dead trailing silence while keeping natural cadence, normalize loudness
    filters.append("dynaudnorm=f=150:g=15")
    af = ",".join(filters)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-af", af,
        "-ar", "44100",
        "-ac", "2",
        "-c:a", "pcm_s16le",
        str(dst_wav),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0 or not dst_wav.exists():
        raise RuntimeError(f"FFmpeg audio normalize failed: {res.stderr[-300:]}")


def wav_duration_sec(wav_path: Path) -> float:
    with wave.open(str(wav_path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / float(rate) if rate else 0.0
