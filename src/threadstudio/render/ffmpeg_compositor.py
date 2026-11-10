"""High-speed FFmpeg compositor (1080x1920 9:16, NVENC GPU auto-detection, 3-channel audio mix)."""
from __future__ import annotations

from functools import lru_cache
import json
import math
from pathlib import Path
import random
import subprocess
import wave

import numpy as np

from ..core.models import LayoutConfig, MediaConfig, SegmentItem, VideoJob

CANVAS_W = 1080
CANVAS_H = 1920
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}


@lru_cache(maxsize=1)
def nvenc_available() -> bool:
    """Test whether h264_nvenc actually initializes on an NVIDIA GPU."""
    cmd = [
        "ffmpeg", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
        "-c:v", "h264_nvenc", "-f", "null", "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=8)
        return res.returncode == 0
    except Exception:
        return False


def probe_duration(media_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(media_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        info = json.loads(res.stdout)
        return float(info["format"]["duration"])
    except Exception:
        return 4.0


def list_media_files(folder: str, exts: set[str]) -> list[Path]:
    if not folder:
        return []
    p = Path(folder)
    if not p.exists() or not p.is_dir():
        return []
    return sorted([f for f in p.iterdir() if f.is_file() and f.suffix.lower() in exts])


def compose_final_video(
    job: VideoJob,
    out_dir: Path,
    *,
    prefer_nvenc: bool = True,
) -> Path:
    """Assemble narration, BGM, SFX, looped 3D grid background, random B-roll, and timed cards."""
    segments = job.enabled_segments
    if not segments:
        raise RuntimeError("Job không có đoạn nội dung hợp lệ để ghép video.")

    total_dur = max(1.0, segments[-1].end_sec)
    job.total_duration_sec = total_dur

    # 1. Build master audio track (narration + transition SFX + looped BGM)
    narration_wav = out_dir / "narration.wav"
    _build_narration_wav(segments, narration_wav, total_dur)

    master_audio_wav = out_dir / "audio" / "master_mix.wav"
    selected_bgm, selected_sfx = _mix_master_audio(
        narration_wav=narration_wav,
        segments=segments,
        media=job.media,
        total_dur=total_dur,
        out_wav=master_audio_wav,
    )

    # 2. Pick background video
    bg_candidates = list_media_files(job.media.bg_video_dir, VIDEO_EXTS)
    if not bg_candidates:
        raise RuntimeError(f"Không tìm thấy video nền (.mp4) trong thư mục: {job.media.bg_video_dir}")
    bg_video = random.choice(bg_candidates)

    # 3. Prepare concatenated muted B-roll stream if `sub_enabled`
    sub_reel_mp4: Path | None = None
    picked_subs: list[str] = []
    if job.layout.sub_enabled:
        sub_candidates = list_media_files(job.media.sub_video_dir, VIDEO_EXTS)
        if sub_candidates:
            sub_reel_mp4, picked_subs = _build_sub_video_reel(
                sub_candidates,
                out_dir / "video-phu" / "sub_reel.mp4",
                target_dur=total_dur,
                layout=job.layout,
            )

    # Save diagnostic JSON artifacts matching original Threads Studio's output layout
    (out_dir / "selected-media.json").write_text(
        json.dumps(
            {
                "background": str(bg_video),
                "sub_videos": picked_subs,
                "bgm": selected_bgm,
                "sfx": selected_sfx,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "v2-layout.json").write_text(
        json.dumps(job.layout.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "timeline.json").write_text(
        json.dumps([s.to_dict() for s in segments], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # 4. Build single-pass FFmpeg filter_complex graph
    final_mp4 = out_dir / f"{job.title_slug}.mp4"
    _run_ffmpeg_compositor(
        bg_video=bg_video,
        sub_reel_mp4=sub_reel_mp4,
        master_audio_wav=master_audio_wav,
        segments=segments,
        layout=job.layout,
        total_dur=total_dur,
        final_mp4=final_mp4,
        prefer_nvenc=prefer_nvenc,
    )
    job.output_mp4 = str(final_mp4)
    return final_mp4


def _build_narration_wav(segments: list[SegmentItem], out_wav: Path, total_dur: float, sr: int = 44100) -> None:
    """Place each segment's 44.1kHz stereo WAV onto an exact sample-accurate timeline buffer."""
    total_samples = int(math.ceil(total_dur * sr)) + sr // 4
    timeline_buf = np.zeros((total_samples, 2), dtype=np.int32)

    for seg in segments:
        p = Path(seg.audio_wav_path)
        if not p.exists():
            continue
        with wave.open(str(p), "rb") as wf:
            raw = wf.readframes(wf.getnframes())
            ch = wf.getnchannels()
        arr = np.frombuffer(raw, dtype=np.int16)
        if ch == 2:
            arr = arr.reshape(-1, 2)
        else:
            arr = np.column_stack([arr, arr])

        start_idx = int(round(seg.start_sec * sr))
        end_idx = min(total_samples, start_idx + arr.shape[0])
        copy_len = max(0, end_idx - start_idx)
        if copy_len > 0:
            timeline_buf[start_idx:end_idx] += arr[:copy_len]

    clipped = np.clip(timeline_buf[: int(round(total_dur * sr))], -32768, 32767).astype(np.int16)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(clipped.tobytes())


def _mix_master_audio(
    *,
    narration_wav: Path,
    segments: list[SegmentItem],
    media: MediaConfig,
    total_dur: float,
    out_wav: Path,
) -> tuple[str, list[str]]:
    """Mix narration (100%) + looped BGM (bgm_volume_pct) + transition SFX (sfx_volume_pct)."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    bgm_files = list_media_files(media.bgm_dir, AUDIO_EXTS) if media.bgm_enabled else []
    sfx_files = list_media_files(media.sfx_dir, AUDIO_EXTS) if media.sfx_enabled else []

    chosen_bgm = str(random.choice(bgm_files)) if bgm_files else ""
    chosen_sfx: list[str] = []

    inputs: list[str] = ["-i", str(narration_wav)]
    filter_parts: list[str] = ["[0:a]volume=1.0[narr]"]
    mix_labels: list[str] = ["[narr]"]
    input_idx = 1

    if chosen_bgm and media.bgm_volume_pct > 0:
        inputs += ["-stream_loop", "-1", "-i", chosen_bgm]
        vol = media.bgm_volume_pct / 100.0
        filter_parts.append(f"[{input_idx}:a]atrim=0:{total_dur:.3f},asetpts=PTS-STARTPTS,volume={vol:.3f}[bgm]")
        mix_labels.append("[bgm]")
        input_idx += 1

    if sfx_files and media.sfx_volume_pct > 0 and len(segments) > 1:
        vol_sfx = media.sfx_volume_pct / 100.0
        for t_idx, seg in enumerate(segments[1:], start=1):
            sfx_p = random.choice(sfx_files)
            chosen_sfx.append(str(sfx_p))
            delay_ms = max(0, int(round(seg.start_sec * 1000)) - 60)
            inputs += ["-i", str(sfx_p)]
            lbl = f"sfx{t_idx}"
            filter_parts.append(f"[{input_idx}:a]volume={vol_sfx:.3f},adelay={delay_ms}|{delay_ms}[{lbl}]")
            mix_labels.append(f"[{lbl}]")
            input_idx += 1

    if len(mix_labels) == 1:
        cmd = ["ffmpeg", "-y", *inputs, "-c:a", "pcm_s16le", "-t", f"{total_dur:.3f}", str(out_wav)]
    else:
        n_in = len(mix_labels)
        filter_parts.append(
            f"{''.join(mix_labels)}amix=inputs={n_in}:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", ";".join(filter_parts),
            "-map", "[aout]",
            "-ar", "44100", "-ac", "2",
            "-c:a", "pcm_s16le",
            "-t", f"{total_dur:.3f}",
            str(out_wav),
        ]

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg audio mix failed: {res.stderr[-350:]}")
    return chosen_bgm, chosen_sfx


def _build_sub_video_reel(
    candidates: list[Path],
    out_mp4: Path,
    *,
    target_dur: float,
    layout: LayoutConfig,
) -> tuple[Path, list[str]]:
    """Stitch random muted B-roll clips scaled/cropped to exact sub-video box dimensions."""
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    target_w = max(64, int(round(CANVAS_W * layout.sub_w_pct / 100.0)) & ~1)
    target_h = max(64, int(round(CANVAS_H * layout.sub_h_pct / 100.0)) & ~1)

    picked: list[Path] = []
    acc = 0.0
    pool = list(candidates)
    while acc < target_dur + 1.0:
        random.shuffle(pool)
        for clip in pool:
            picked.append(clip)
            acc += max(1.5, min(8.0, probe_duration(clip)))
            if acc >= target_dur + 1.0:
                break

    inputs: list[str] = []
    norm_chains: list[str] = []
    concat_ins: list[str] = []

    for i, clip in enumerate(picked):
        inputs += ["-i", str(clip)]
        if layout.sub_fit == "contain":
            scale_expr = (
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black"
            )
        else:
            scale_expr = (
                f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                f"crop={target_w}:{target_h}"
            )
        norm_chains.append(f"[{i}:v]fps=24,{scale_expr},setsar=1,format=yuv420p[sv{i}]")
        concat_ins.append(f"[sv{i}]")

    filter_str = ";".join(norm_chains) + f";{''.join(concat_ins)}concat=n={len(picked)}:v=1:a=0[vsub]"
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_str,
        "-map", "[vsub]",
        "-t", f"{target_dur:.3f}",
        "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
        str(out_mp4),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg sub-video stitch failed: {res.stderr[-350:]}")
    return out_mp4, [str(p) for p in picked]


def _run_ffmpeg_compositor(
    *,
    bg_video: Path,
    sub_reel_mp4: Path | None,
    master_audio_wav: Path,
    segments: list[SegmentItem],
    layout: LayoutConfig,
    total_dur: float,
    final_mp4: Path,
    prefer_nvenc: bool,
) -> None:
    inputs: list[str] = [
        "-stream_loop", "-1", "-t", f"{total_dur:.3f}", "-i", str(bg_video),
        "-i", str(master_audio_wav),
    ]
    next_idx = 2
    sub_idx: int | None = None
    if sub_reel_mp4 is not None and sub_reel_mp4.exists():
        inputs += ["-i", str(sub_reel_mp4)]
        sub_idx = next_idx
        next_idx += 1

    card_indices: list[tuple[int, SegmentItem]] = []
    for seg in segments:
        inputs += ["-loop", "1", "-t", f"{total_dur:.3f}", "-i", str(seg.card_png_path)]
        card_indices.append((next_idx, seg))
        next_idx += 1

    chains: list[str] = [
        f"[0:v]fps=24,scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS_W}:{CANVAS_H},setsar=1,format=yuv420p[bg]"
    ]
    curr_v = "bg"

    # Overlay secondary B-roll video
    if sub_idx is not None:
        sub_cx = CANVAS_W * (layout.sub_x_pct / 100.0)
        sub_cy = CANVAS_H * (layout.sub_y_pct / 100.0)
        chains.append(
            f"[{curr_v}][{sub_idx}:v]overlay=x={sub_cx:.1f}-w/2:y={sub_cy:.1f}-h/2:shortest=1[v_with_sub]"
        )
        curr_v = "v_with_sub"

    # Overlay each Threads comment card on its exact [start_sec, end_sec] window
    target_card_w = max(240, min(CANVAS_W - 24, int(round(880 * (layout.card_scale_pct / 100.0)))))
    card_cx = CANVAS_W * (layout.card_x_pct / 100.0)
    card_cy = CANVAS_H * (layout.card_y_pct / 100.0)

    for c_i, (inp_i, seg) in enumerate(card_indices):
        scaled_lbl = f"card_s_{c_i}"
        out_lbl = f"v_step_{c_i}"
        chains.append(f"[{inp_i}:v]scale={target_card_w}:-1:flags=lanczos[{scaled_lbl}]")
        enable_expr = f"between(t,{seg.start_sec:.3f},{seg.end_sec:.3f})"
        chains.append(
            f"[{curr_v}][{scaled_lbl}]overlay="
            f"x={card_cx:.1f}-w/2:y={card_cy:.1f}-h/2:"
            f"eof_action=pass:enable='{enable_expr}'[{out_lbl}]"
        )
        curr_v = out_lbl

    chains.append(f"[{curr_v}]format=yuv420p[vfinal]")

    use_gpu = prefer_nvenc and nvenc_available()
    vcodec = (
        ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "21", "-b:v", "0"]
        if use_gpu
        else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    )

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(chains),
        "-map", "[vfinal]",
        "-map", "1:a",
        *vcodec,
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-t", f"{total_dur:.3f}",
        str(final_mp4),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0 or not final_mp4.exists():
        raise RuntimeError(f"FFmpeg final render failed: {res.stderr[-450:]}")
