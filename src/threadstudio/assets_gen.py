"""Procedural sample media generator (3D perspective grid tunnel, sample B-rolls, BGM, SFX).

Creates ready-to-render 1080x1920 sample assets matching Video 1's 3D black-and-white grid
background so the studio works out of the box before custom folders are configured.
"""
from __future__ import annotations

import math
from pathlib import Path
import subprocess
import wave

import numpy as np
from PIL import Image, ImageDraw

from .core.config import project_root


def ensure_sample_assets(root: Path | None = None) -> Path:
    """Ensure sample background grid video, sub-videos, BGM, and SFX exist."""
    media_root = root or (project_root() / "assets" / "sample_media")
    bg_dir = media_root / "bg_grid"
    sub_dir = media_root / "sub_videos"
    bgm_dir = media_root / "bgm"
    sfx_dir = media_root / "sfx"
    for d in (bg_dir, sub_dir, bgm_dir, sfx_dir):
        d.mkdir(parents=True, exist_ok=True)

    bg_mp4 = bg_dir / "grid_tunnel_3d.mp4"
    if not any(bg_dir.glob("*.mp4")):
        _generate_3d_grid_tunnel_mp4(bg_mp4, width=720, height=1280, fps=24, seconds=4)

    if not any(sub_dir.glob("*.mp4")):
        _generate_broll_wave_mp4(sub_dir / "broll_neon_wave.mp4", width=640, height=360, fps=24, seconds=4)
        _generate_broll_starfield_mp4(sub_dir / "broll_starfield.mp4", width=640, height=360, fps=24, seconds=4)

    if not any(bgm_dir.glob("*.*")):
        _generate_warm_bgm_wav(bgm_dir / "lofi_story_warm.wav", duration_sec=16.0)

    if not any(sfx_dir.glob("*.*")):
        _generate_pop_sfx_wav(sfx_dir / "card_pop.wav")

    return media_root


def _project_point(x: float, y: float, z: float, w: int, h: int, fov: float) -> tuple[float, float] | None:
    if z <= 0.15:
        return None
    px = w * 0.5 + (x / z) * fov
    py = h * 0.5 + (y / z) * fov
    return (px, py)


def _generate_3d_grid_tunnel_mp4(out_path: Path, width: int = 720, height: int = 1280, fps: int = 24, seconds: int = 4) -> None:
    """Render the exact curved 3D white-node wireframe floor & ceiling tunnel from Video 1."""
    total_frames = fps * seconds
    fov = width * 0.78
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "21",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None

    x_cols = np.linspace(-4.2, 4.2, 13)
    z_steps = 22
    z_spacing = 0.55

    for frame_idx in range(total_frames):
        phase = (frame_idx / total_frames) * z_spacing
        sway = math.sin(2.0 * math.pi * frame_idx / total_frames) * 0.12
        img = Image.new("RGB", (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(img)

        for plane_sign in (-1.0, 1.0):  # Ceiling (-1) and Floor (+1)
            grid_pts: list[list[tuple[float, float] | None]] = []
            for zi in range(z_steps):
                z = 0.7 + zi * z_spacing - phase
                row_pts: list[tuple[float, float] | None] = []
                for x in x_cols:
                    # Subtle saddle curvature like Video 1's spider-grid
                    curve = 0.14 * (x * x) * (-plane_sign)
                    y = plane_sign * (1.85 + curve)
                    pt = _project_point(x + sway, y, z, width, height, fov)
                    row_pts.append(pt)
                grid_pts.append(row_pts)

            # Draw depth and transverse lines with distance-based brightness
            for zi in range(z_steps):
                z_val = 0.7 + zi * z_spacing - phase
                shade = int(max(35, min(235, 255 * (1.0 - z_val / 13.5))))
                color = (shade, shade, shade)
                row = grid_pts[zi]
                for xi in range(len(x_cols) - 1):
                    p1, p2 = row[xi], row[xi + 1]
                    if p1 and p2:
                        draw.line([p1, p2], fill=color, width=2)
                if zi + 1 < z_steps:
                    next_row = grid_pts[zi + 1]
                    for xi in range(len(x_cols)):
                        p1, p2 = row[xi], next_row[xi]
                        if p1 and p2:
                            draw.line([p1, p2], fill=color, width=2)

                # Crisp white intersection dots (signature look of Video 1)
                dot_r = max(1.5, 4.2 - z_val * 0.25)
                if shade > 65:
                    for p in row:
                        if p and -20 <= p[0] <= width + 20 and -20 <= p[1] <= height + 20:
                            draw.ellipse(
                                (p[0] - dot_r, p[1] - dot_r, p[0] + dot_r, p[1] + dot_r),
                                fill=(min(255, shade + 30),) * 3,
                            )

        proc.stdin.write(img.tobytes())

    proc.stdin.close()
    proc.wait(timeout=30)


def _generate_broll_wave_mp4(out_path: Path, width: int = 640, height: int = 360, fps: int = 24, seconds: int = 4) -> None:
    total_frames = fps * seconds
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "22",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    xs = np.linspace(0, 4 * math.pi, 160)
    for f in range(total_frames):
        t = 2 * math.pi * f / total_frames
        img = Image.new("RGB", (width, height), (14, 20, 34))
        draw = ImageDraw.Draw(img)
        for layer, col in enumerate([(56, 189, 248), (99, 102, 241), (244, 114, 182)]):
            pts = []
            for x_val in xs:
                px = (x_val / (4 * math.pi)) * width
                py = height * (0.38 + layer * 0.14) + math.sin(x_val + t + layer) * 32
                pts.append((px, py))
            draw.line(pts, fill=col, width=4)
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    proc.wait(timeout=20)


def _generate_broll_starfield_mp4(out_path: Path, width: int = 640, height: int = 360, fps: int = 24, seconds: int = 4) -> None:
    rng = np.random.default_rng(42)
    stars = rng.uniform(-1.0, 1.0, size=(140, 3))
    stars[:, 2] = rng.uniform(0.1, 1.0, size=140)
    total_frames = fps * seconds
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "22",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    for f in range(total_frames):
        img = Image.new("RGB", (width, height), (10, 12, 22))
        draw = ImageDraw.Draw(img)
        dz = (f / total_frames)
        for sx, sy, sz in stars:
            z = (sz - dz) % 1.0
            if z < 0.06:
                continue
            px = width * 0.5 + (sx / z) * (width * 0.28)
            py = height * 0.5 + (sy / z) * (height * 0.28)
            if 0 <= px < width and 0 <= py < height:
                r = max(1.2, (1.0 - z) * 3.8)
                b = int(120 + (1.0 - z) * 135)
                draw.ellipse((px - r, py - r, px + r, py + r), fill=(b, b, 255))
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    proc.wait(timeout=20)


def _generate_warm_bgm_wav(out_path: Path, duration_sec: float = 16.0, sr: int = 44100) -> None:
    """Synthesize a gentle, copyright-free warm electric-piano chord progression loop."""
    n = int(duration_sec * sr)
    t = np.linspace(0, duration_sec, n, endpoint=False)
    chords = [
        (261.63, 329.63, 392.00, 493.88),  # Cmaj7
        (220.00, 261.63, 329.63, 392.00),  # Am7
        (174.61, 220.00, 261.63, 349.23),  # Fmaj7
        (196.00, 246.94, 293.66, 392.00),  # G6
    ]
    audio = np.zeros(n, dtype=np.float64)
    seg_len = n // len(chords)
    for idx, freqs in enumerate(chords):
        s0, s1 = idx * seg_len, (idx + 1) * seg_len
        local_t = np.linspace(0, duration_sec / len(chords), s1 - s0, endpoint=False)
        env = np.minimum(1.0, local_t / 0.12) * np.exp(-local_t * 0.55)
        chord_sig = sum(np.sin(2 * np.pi * f * local_t) + 0.25 * np.sin(4 * np.pi * f * local_t) for f in freqs)
        audio[s0:s1] = chord_sig * env * 0.12

    pcm = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
    stereo = np.column_stack([pcm, pcm]).ravel()
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo.tobytes())


def _generate_pop_sfx_wav(out_path: Path, sr: int = 44100) -> None:
    """Crisp soft UI pop transition sound."""
    dur = 0.16
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    freq = np.linspace(520.0, 220.0, t.size)
    phase = 2 * np.pi * np.cumsum(freq) / sr
    sig = np.sin(phase) * np.exp(-t * 26.0) * 0.45
    pcm = np.clip(sig * 32767, -32768, 32767).astype(np.int16)
    stereo = np.column_stack([pcm, pcm]).ravel()
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo.tobytes())

