"""Automated unit & concurrent batch integration tests for Threads Studio Pro."""
from __future__ import annotations

import os
from pathlib import Path
import time

os.environ["FLUENT_BLUE_QT_BINDING"] = "PyQt6"

from fluent_blue.compatibility.qt import QApplication

from threadstudio.ai.text_normalizer import normalize_segments_batch, normalize_teencode_offline
from threadstudio.assets_gen import ensure_sample_assets
from threadstudio.core.config import AppSettings
from threadstudio.core.models import JobStage, LayoutConfig, VideoJob
from threadstudio.core.pipeline import BatchPipelineManager
from threadstudio.render.card_renderer import render_threads_card_qimage
from threadstudio.scrapers.threads_scraper import is_spam_comment, parse_manual_script, sample_video1_script
from threadstudio.tts.router import TTSRouter


def _ensure_qt() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


def test_spam_filter_and_script_parser() -> None:
    assert is_spam_comment("Mua ngay tại https://s.shopee.vn/abc123")
    assert is_spam_comment("chấm")
    assert is_spam_comment(".")
    assert not is_spam_comment('Từ tiếng Việt mình thích nhất là chữ "thương".')

    slug, segs = parse_manual_script(sample_video1_script())
    assert slug
    assert len(segs) >= 6
    assert segs[0].is_root_post is True
    assert segs[0].username == "yumemikawa"


def test_offline_teencode_normalizer() -> None:
    expanded = normalize_teencode_offline("mình ko tìm đc từ nào đb với mn hết")
    assert "không" in expanded
    assert "được" in expanded
    assert "mọi người" in expanded

    _, segs = parse_manual_script(sample_video1_script())
    normalize_segments_batch(segs[:2])
    assert "không tìm được" in segs[0].spoken_text.lower()


def test_card_renderer_light_dark_and_privacy() -> None:
    _ensure_qt()
    _, segs = parse_manual_script(sample_video1_script())

    light_cfg = LayoutConfig(card_theme="light", hide_username=False)
    img_light = render_threads_card_qimage(segs[0], light_cfg, base_width=880)
    assert img_light.width() == 880
    assert img_light.height() > 200

    dark_priv_cfg = LayoutConfig(card_theme="dark", hide_username=True, card_radius_px=24)
    img_dark = render_threads_card_qimage(segs[1], dark_priv_cfg, base_width=880)
    assert img_dark.width() == 880
    assert img_dark.height() > 180


def test_concurrent_multi_job_batch_pipeline(tmp_path: Path) -> None:
    """Verify BatchPipelineManager processes 2 jobs concurrently in Full-Auto mode end-to-end."""
    app = _ensure_qt()
    ensure_sample_assets()

    settings = AppSettings.load()
    settings.output_root = str(tmp_path / "batch_out")
    settings.pause_for_review = False
    settings.max_tts_workers = 2
    settings.max_render_workers = 2

    pipeline = BatchPipelineManager(settings)
    job_a = VideoJob(
        title_slug="batch_a",
        manual_script=(
            "@user_a | 520: Có ai thích mùi mưa đầu mùa ko mn.\n"
            "@reply_1 | 120: Mùi đất ẩm sau mưa nghe bình yên thật sự."
        ),
        pause_for_review=False,
        selected_voices=["capcut:BV421_vivn_streaming", "capcut:BV075_streaming"],
    )
    job_b = VideoJob(
        title_slug="batch_b",
        manual_script=(
            "@user_b | 890: Một thói quen nhỏ giúp mn ngủ ngon hơn mỗi tối.\n"
            "@reply_2 | 310: Tắt điện thoại trước 30 phút và đọc vài trang sách."
        ),
        pause_for_review=False,
        selected_voices=["capcut:BV074_streaming", "capcut:multi_male_felipe_uranus_bigtts"],
    )

    pipeline.enqueue_jobs([job_a, job_b])

    deadline = time.time() + 120.0
    while time.time() < deadline:
        app.processEvents()
        done = [
            j.stage in (JobStage.COMPLETED.value, JobStage.FAILED.value)
            for j in (job_a, job_b)
        ]
        if all(done):
            break
        time.sleep(0.25)

    assert job_a.stage == JobStage.COMPLETED.value, f"Job A failed: {job_a.error_message}"
    assert job_b.stage == JobStage.COMPLETED.value, f"Job B failed: {job_b.error_message}"
    assert Path(job_a.output_mp4).exists() and Path(job_a.output_mp4).stat().st_size > 50_000
    assert Path(job_b.output_mp4).exists() and Path(job_b.output_mp4).stat().st_size > 50_000

    router = TTSRouter()
    voices = router.list_all_voices()
    assert any(v.id.startswith("capcut:") for v in voices)
