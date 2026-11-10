"""Persistent configuration, API keys, concurrency limits, and presets."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from .models import CommentMode, LayoutConfig, MediaConfig, PresetProfile, VoiceAssignMode


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass
class AppSettings:
    theme: str = "dark"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    vieneu_endpoint: str = ""  # Optional local VieNeu HTTP server or empty for local SDK
    vieneu_clone_ref_audio: str = ""
    vieneu_clone_ref_text: str = ""
    capcut_device_json: str = ""

    # Concurrency limits for Stage-Pipelined Batch Orchestrator
    max_scrape_workers: int = 2
    max_ai_workers: int = 4
    max_tts_workers: int = 4
    max_render_workers: int = 2
    prefer_nvenc: bool = True

    # Active workspace defaults
    require_review_default: bool = True
    comment_mode: str = CommentMode.TOP_LIKES_WITH_AUTHOR.value
    max_comments: int = 6
    voice_speed: float = 1.20
    preview_voice_id: str = "capcut:BV421_vivn_streaming"
    voice_assign_mode: str = VoiceAssignMode.RANDOM.value
    selected_voices: list[str] = field(
        default_factory=lambda: [
            "capcut:BV421_vivn_streaming",
            "capcut:BV075_streaming",
            "capcut:BV074_streaming",
            "capcut:multi_male_felipe_uranus_bigtts",
        ]
    )
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    presets: list[PresetProfile] = field(default_factory=list)
    output_root: str = ""

    def resolved_output_root(self) -> Path:
        p = Path(self.output_root) if self.output_root else (project_root() / "output")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def user_data_dir(self) -> Path:
        p = project_root() / "user_data"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def to_dict(self) -> dict[str, Any]:
        return {
            "theme": self.theme,
            "gemini_api_key": self.gemini_api_key,
            "gemini_model": self.gemini_model,
            "vieneu_endpoint": self.vieneu_endpoint,
            "vieneu_clone_ref_audio": self.vieneu_clone_ref_audio,
            "vieneu_clone_ref_text": self.vieneu_clone_ref_text,
            "capcut_device_json": self.capcut_device_json,
            "max_scrape_workers": self.max_scrape_workers,
            "max_ai_workers": self.max_ai_workers,
            "max_tts_workers": self.max_tts_workers,
            "max_render_workers": self.max_render_workers,
            "prefer_nvenc": self.prefer_nvenc,
            "require_review_default": self.require_review_default,
            "comment_mode": self.comment_mode,
            "max_comments": self.max_comments,
            "voice_speed": self.voice_speed,
            "preview_voice_id": self.preview_voice_id,
            "voice_assign_mode": self.voice_assign_mode,
            "selected_voices": list(self.selected_voices),
            "layout": self.layout.to_dict(),
            "media": self.media.to_dict(),
            "presets": [p.to_dict() for p in self.presets],
            "output_root": self.output_root,
        }

    @classmethod
    def load(cls, path: Path | None = None) -> AppSettings:
        cfg_path = path or (project_root() / "user_data" / "settings.json")
        sample_root = project_root() / "assets" / "sample_media"
        default_media = MediaConfig(
            bg_video_dir=str(sample_root / "bg_grid"),
            sub_video_dir=str(sample_root / "sub_videos"),
            bgm_dir=str(sample_root / "bgm"),
            sfx_dir=str(sample_root / "sfx"),
            bgm_enabled=True,
            bgm_volume_pct=20,
            sfx_enabled=False,
            sfx_volume_pct=40,
        )
        if not cfg_path.exists():
            inst = cls(media=default_media, presets=_default_presets(sample_root))
            inst.save(cfg_path)
            return inst
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return cls(media=default_media, presets=_default_presets(sample_root))

        media = MediaConfig.from_dict(raw.get("media", {}))
        if not media.bg_video_dir:
            media.bg_video_dir = default_media.bg_video_dir
        if not media.sub_video_dir:
            media.sub_video_dir = default_media.sub_video_dir
        if not media.bgm_dir:
            media.bgm_dir = default_media.bgm_dir
        if not media.sfx_dir:
            media.sfx_dir = default_media.sfx_dir

        presets_raw = raw.get("presets", [])
        presets = [PresetProfile.from_dict(p) for p in presets_raw] if presets_raw else _default_presets(sample_root)

        return cls(
            theme=raw.get("theme", "dark"),
            gemini_api_key=raw.get("gemini_api_key", ""),
            gemini_model=raw.get("gemini_model", "gemini-2.5-flash"),
            vieneu_endpoint=raw.get("vieneu_endpoint", ""),
            vieneu_clone_ref_audio=raw.get("vieneu_clone_ref_audio", ""),
            vieneu_clone_ref_text=raw.get("vieneu_clone_ref_text", ""),
            capcut_device_json=raw.get("capcut_device_json", ""),
            max_scrape_workers=int(raw.get("max_scrape_workers", 2)),
            max_ai_workers=int(raw.get("max_ai_workers", 4)),
            max_tts_workers=int(raw.get("max_tts_workers", 4)),
            max_render_workers=int(raw.get("max_render_workers", 2)),
            prefer_nvenc=bool(raw.get("prefer_nvenc", True)),
            require_review_default=bool(raw.get("require_review_default", True)),
            comment_mode=raw.get("comment_mode", CommentMode.TOP_LIKES_WITH_AUTHOR.value),
            max_comments=int(raw.get("max_comments", 6)),
            voice_speed=float(raw.get("voice_speed", 1.20)),
            preview_voice_id=raw.get("preview_voice_id", "capcut:BV421_vivn_streaming"),
            voice_assign_mode=raw.get("voice_assign_mode", VoiceAssignMode.RANDOM.value),
            selected_voices=raw.get(
                "selected_voices",
                [
                    "capcut:BV421_vivn_streaming",
                    "capcut:BV075_streaming",
                    "capcut:BV074_streaming",
                    "capcut:multi_male_felipe_uranus_bigtts",
                ],
            ),
            layout=LayoutConfig.from_dict(raw.get("layout", {})),
            media=media,
            presets=presets,
            output_root=raw.get("output_root", ""),
        )

    def save(self, path: Path | None = None) -> None:
        cfg_path = path or (project_root() / "user_data" / "settings.json")
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _default_presets(sample_root: Path) -> list[PresetProfile]:
    return [
        PresetProfile(
            name="Bình Luận Cộng Đồng (Giống Video Mẫu 1)",
            comment_mode=CommentMode.TOP_LIKES_WITH_AUTHOR.value,
            max_comments=6,
            voice_speed=1.20,
            voice_assign_mode=VoiceAssignMode.RANDOM.value,
            selected_voices=[
                "capcut:BV421_vivn_streaming",
                "capcut:BV075_streaming",
                "capcut:BV074_streaming",
                "capcut:multi_male_felipe_uranus_bigtts",
            ],
            media=MediaConfig(
                bg_video_dir=str(sample_root / "bg_grid"),
                sub_video_dir=str(sample_root / "sub_videos"),
                bgm_dir=str(sample_root / "bgm"),
                sfx_dir=str(sample_root / "sfx"),
                bgm_enabled=True,
                bgm_volume_pct=20,
                sfx_enabled=False,
                sfx_volume_pct=40,
            ),
            layout=LayoutConfig(
                card_x_pct=50.0,
                card_y_pct=28.0,
                card_scale_pct=108.0,
                card_radius_px=0,
                card_theme="light",
                sub_enabled=True,
                sub_x_pct=50.0,
                sub_y_pct=64.0,
                sub_w_pct=86.0,
                sub_h_pct=26.0,
            ),
        ),
        PresetProfile(
            name="Kể Chuyện Dài Tác Giả (Storytelling)",
            comment_mode=CommentMode.AUTHOR_ONLY.value,
            max_comments=10,
            voice_speed=1.15,
            voice_assign_mode=VoiceAssignMode.SINGLE.value,
            selected_voices=["capcut:multi_male_felipe_uranus_bigtts"],
            media=MediaConfig(
                bg_video_dir=str(sample_root / "bg_grid"),
                sub_video_dir=str(sample_root / "sub_videos"),
                bgm_dir=str(sample_root / "bgm"),
                sfx_dir=str(sample_root / "sfx"),
                bgm_enabled=True,
                bgm_volume_pct=18,
                sfx_enabled=False,
                sfx_volume_pct=0,
            ),
            layout=LayoutConfig(
                card_x_pct=50.0,
                card_y_pct=34.0,
                card_scale_pct=110.0,
                card_radius_px=12,
                card_theme="dark",
                sub_enabled=True,
                sub_x_pct=50.0,
                sub_y_pct=68.0,
                sub_w_pct=88.0,
                sub_h_pct=24.0,
                sub_radius_px=12,
            ),
        ),
        PresetProfile(
            name="Bình Luận Hài Meme (Có SFX Chuyển Cảnh)",
            comment_mode=CommentMode.TOP_LIKES_NO_AUTHOR.value,
            max_comments=8,
            voice_speed=1.25,
            voice_assign_mode=VoiceAssignMode.RANDOM.value,
            selected_voices=[
                "capcut:BV421_vivn_streaming",
                "capcut:BV074_streaming",
                "capcut:BV075_streaming_vibrato_dsp",
                "capcut:BV560_streaming",
            ],
            media=MediaConfig(
                bg_video_dir=str(sample_root / "bg_grid"),
                sub_video_dir=str(sample_root / "sub_videos"),
                bgm_dir=str(sample_root / "bgm"),
                sfx_dir=str(sample_root / "sfx"),
                bgm_enabled=True,
                bgm_volume_pct=20,
                sfx_enabled=True,
                sfx_volume_pct=40,
            ),
            layout=LayoutConfig(),
        ),
    ]
