"""One-click online starter media pack downloader (Meme B-Roll, Royalty-Free BGM, SFX, 9:16 Backgrounds).

Usage:
    python scripts/download_media_pack.py
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "assets" / "sample_media"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _download_file(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 2048:
        return True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://mixkit.co/"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        if len(data) < 2048:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except Exception:
        return False


def download_starter_media_pack(media_root: Path | None = None) -> dict[str, int]:
    """Download curated SFX, BGM, viral Meme B-rolls, and 9:16 vertical backgrounds."""
    target = media_root or BASE
    counts = {"sfx": 0, "bgm": 0, "sub_videos": 0, "bg_grid": 0}

    # 1. Mixkit Pop & Whoosh SFX
    for cat, prefix in (("pop", "sfx_pop"), ("whoosh", "sfx_whoosh")):
        try:
            html = _fetch_html(f"https://mixkit.co/free-sound-effects/{cat}/")
            urls = list(dict.fromkeys(re.findall(r"https://assets\.mixkit\.co/[^\"'\s>]*\.(?:mp3|wav)", html)))
            for idx, u in enumerate(urls[:2], start=1):
                ext = Path(u.split("?")[0]).suffix or ".mp3"
                if _download_file(u, target / "sfx" / f"{prefix}_{idx:02d}{ext}"):
                    counts["sfx"] += 1
        except Exception:
            pass

    # 2. Viral Meme SFX
    for q, name in (
        ("vine boom", "meme_vine_boom"),
        ("bruh", "meme_bruh"),
        ("anime wow", "meme_anime_wow"),
        ("rizz sound effect", "meme_rizz"),
        ("mouse click", "ui_click"),
    ):
        try:
            html = _fetch_html(f"https://www.myinstants.com/en/search/?name={urllib.parse.quote(q)}")
            m = re.search(r"play\('(/media/sounds/[^']+\.mp3)'", html)
            if m and _download_file("https://www.myinstants.com" + m.group(1), target / "sfx" / f"{name}.mp3"):
                counts["sfx"] += 1
        except Exception:
            pass

    # 3. Royalty-free BGM (Lofi / Night Storytelling / Upbeat)
    for genre, prefix, lim in (("chill", "bgm_chill_lofi", 3), ("ambient", "bgm_story_night", 2), ("hip-hop", "bgm_upbeat_funny", 2)):
        try:
            html = _fetch_html(f"https://mixkit.co/free-stock-music/{genre}/")
            urls = list(dict.fromkeys(re.findall(r"https://assets\.mixkit\.co/[^\"'\s>]*\.mp3", html)))
            for idx, u in enumerate(urls[:lim], start=1):
                if _download_file(u, target / "bgm" / f"{prefix}_{idx:02d}.mp3"):
                    counts["bgm"] += 1
        except Exception:
            pass

    # 4. Viral Meme / Cat / Reaction / Parkour Sub-Videos
    meme_queries = [
        ("huh-cat-meme", "meme_huh_cat"),
        ("cat-vibing-headbang", "meme_cat_vibing"),
        ("shocked-black-guy-meme", "meme_shocked_guy"),
        ("crying-cat-thumbs-up", "meme_crying_cat"),
        ("happy-happy-happy-cat", "meme_happy_cat"),
        ("side-eye-dog-meme", "meme_side_eye_dog"),
        ("anime-funny-reaction", "meme_anime_reaction"),
        ("minecraft-parkour-gameplay", "gameplay_parkour"),
    ]
    for q, name in meme_queries:
        final_mp4 = target / "sub_videos" / f"{name}.mp4"
        if final_mp4.exists() and final_mp4.stat().st_size > 10_000:
            counts["sub_videos"] += 1
            continue
        try:
            html = _fetch_html(f"https://tenor.com/search/{q}-gifs")
            mp4s = list(dict.fromkeys(re.findall(r"https://media\.tenor\.com/[^\"'\s>]+?\.mp4", html)))
            if mp4s:
                tmp_mp4 = target / "sub_videos" / f"_{name}_raw.mp4"
                if _download_file(mp4s[0], tmp_mp4):
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-stream_loop", "3", "-i", str(tmp_mp4),
                            "-t", "5.5", "-vf", "fps=24,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
                            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                            str(final_mp4),
                        ],
                        capture_output=True,
                    )
                    tmp_mp4.unlink(missing_ok=True)
                    if final_mp4.exists():
                        counts["sub_videos"] += 1
        except Exception:
            pass

    # 5. 9:16 Vertical Background Loops
    for slug, prefix, lim in (("tunnel", "bg_cyber_tunnel", 2), ("abstract", "bg_dark_abstract", 2), ("rain", "bg_night_rain", 1)):
        try:
            html = _fetch_html(f"https://mixkit.co/free-stock-video/{slug}/")
            urls = list(dict.fromkeys(re.findall(r"https://assets\.mixkit\.co/videos/\d+/\d+-720\.mp4", html)))
            for idx, u in enumerate(urls[:lim], start=1):
                dest = target / "bg_grid" / f"{prefix}_{idx:02d}.mp4"
                if dest.exists() and dest.stat().st_size > 50_000:
                    counts["bg_grid"] += 1
                    continue
                raw_tmp = target / "bg_grid" / f"_{prefix}_{idx:02d}_raw.mp4"
                if _download_file(u, raw_tmp):
                    subprocess.run(
                        [
                            "ffmpeg", "-y", "-i", str(raw_tmp),
                            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
                            "-an", "-t", "12", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                            str(dest),
                        ],
                        capture_output=True,
                    )
                    raw_tmp.unlink(missing_ok=True)
                    if dest.exists():
                        counts["bg_grid"] += 1
        except Exception:
            pass

    return counts


if __name__ == "__main__":
    print("Downloading Threads Studio Pro Starter Media Pack...")
    summary = download_starter_media_pack()
    print("Done:", summary)
