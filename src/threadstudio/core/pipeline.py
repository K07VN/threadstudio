"""Stage-Pipelined Multi-Threaded Batch Orchestrator for Threads Studio Pro."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import threading

from fluent_blue.compatibility.qt import QObject, QtCore

from ..ai.text_normalizer import normalize_segments_batch
from ..render.card_renderer import render_cards_for_job
from ..render.ffmpeg_compositor import compose_final_video
from ..scrapers.threads_scraper import parse_manual_script, scrape_threads_url
from ..tts.router import TTSRouter
from .config import AppSettings
from .models import JobStage, VideoJob

Signal = getattr(QtCore, "pyqtSignal", None) or getattr(QtCore, "Signal")
logger = logging.getLogger("threadstudio")


class BatchPipelineManager(QObject):
    """Coordinates concurrent multi-stage video jobs without blocking the Qt main thread."""

    job_changed = Signal(str)           # job_id
    review_ready = Signal(str)          # job_id
    job_finished = Signal(str, str)     # job_id, mp4_path
    counts_changed = Signal()

    def __init__(self, settings: AppSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.jobs: dict[str, VideoJob] = {}
        self.job_order: list[str] = []
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

        # Main task coordinator pool; stage semaphores prevent resource thrashing
        self._executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix="ts-worker")
        self._rebuild_semaphores()

    def _rebuild_semaphores(self) -> None:
        self._scrape_sem = threading.Semaphore(max(1, self.settings.max_scrape_workers))
        self._ai_sem = threading.Semaphore(max(1, self.settings.max_ai_workers))
        self._tts_sem = threading.Semaphore(max(1, self.settings.max_tts_workers))
        self._render_sem = threading.Semaphore(max(1, self.settings.max_render_workers))

    def enqueue_jobs(self, jobs: list[VideoJob]) -> None:
        with self._lock:
            for job in jobs:
                self.jobs[job.job_id] = job
                self.job_order.append(job.job_id)
        self.counts_changed.emit()
        for job in jobs:
            self.job_changed.emit(job.job_id)
            self._executor.submit(self._run_phase_ingest, job.job_id)

    def approve_and_render(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if not job or job_id in self._cancelled:
            return
        if job.stage not in (JobStage.AWAITING_REVIEW.value, JobStage.FAILED.value):
            return
        job.stage = JobStage.SYNTHESIZING.value
        job.progress_pct = 50.0
        job.status_detail = "Đã duyệt — đang xếp hàng tạo giọng đọc..."
        self.job_changed.emit(job_id)
        self.counts_changed.emit()
        self._executor.submit(self._run_phase_render, job_id)

    def approve_all_waiting(self) -> int:
        waiting = [
            jid for jid in self.job_order
            if self.jobs[jid].stage == JobStage.AWAITING_REVIEW.value
        ]
        for jid in waiting:
            self.approve_and_render(jid)
        return len(waiting)

    def cancel_job(self, job_id: str) -> None:
        self._cancelled.add(job_id)
        job = self.jobs.get(job_id)
        if job and job.stage not in (JobStage.COMPLETED.value, JobStage.FAILED.value):
            job.stage = JobStage.CANCELLED.value
            job.status_detail = "Đã dừng theo yêu cầu"
            self.job_changed.emit(job_id)
            self.counts_changed.emit()

    def cancel_all_active(self) -> None:
        for jid, job in list(self.jobs.items()):
            if job.stage not in (JobStage.COMPLETED.value, JobStage.FAILED.value, JobStage.CANCELLED.value):
                self.cancel_job(jid)

    def active_count(self) -> int:
        active_stages = {
            JobStage.QUEUED.value,
            JobStage.SCRAPING.value,
            JobStage.NORMALIZING.value,
            JobStage.SYNTHESIZING.value,
            JobStage.RENDERING.value,
        }
        return sum(1 for j in self.jobs.values() if j.stage in active_stages)

    def review_count(self) -> int:
        return sum(1 for j in self.jobs.values() if j.stage == JobStage.AWAITING_REVIEW.value)

    # ---- Worker Phases ----

    def _run_phase_ingest(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if not job or job_id in self._cancelled:
            return

        try:
            out_dir = job.ensure_output_dir(self.settings.resolved_output_root())

            # Stage 1: Scrape or parse manual script
            with self._scrape_sem:
                if job_id in self._cancelled:
                    return
                job.stage = JobStage.SCRAPING.value
                job.progress_pct = 12.0
                job.status_detail = "Đang lấy bài & bình luận..."
                self.job_changed.emit(job_id)
                self.counts_changed.emit()

                if job.manual_script.strip():
                    logger.info("[Job #%s] Đang phân tích kịch bản nhập tay...", job_id)
                    slug, segments = parse_manual_script(job.manual_script)
                else:
                    logger.info("[Job #%s] Đang cào Threads: %s", job_id, job.source_url)
                    slug, segments = scrape_threads_url(
                        job.source_url,
                        profile_dir=self.settings.user_data_dir() / "threads_profile",
                        media_cache_dir=out_dir / "media-cache",
                        comment_mode=job.comment_mode,
                        max_comments=job.max_comments,
                    )

                job.title_slug = slug
                job.segments = segments
                (out_dir / "content.json").write_text(
                    json.dumps([s.to_dict() for s in segments], ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info("[Job #%s] Thu thập xong %d đoạn (%s).", job_id, len(segments), slug)

            # Stage 2: Teencode & Vision OCR normalization + initial card preview render
            with self._ai_sem:
                if job_id in self._cancelled:
                    return
                job.stage = JobStage.NORMALIZING.value
                job.progress_pct = 35.0
                job.status_detail = "Đang bung từ viết tắt & dựng thẻ mẫu..."
                self.job_changed.emit(job_id)

                uses_vieneu = any(v.startswith("vieneu:") for v in job.selected_voices)
                trans_map = normalize_segments_batch(
                    job.segments,
                    gemini_api_key=self.settings.gemini_api_key,
                    gemini_model=self.settings.gemini_model,
                    include_emotion_cues=uses_vieneu,
                )
                (out_dir / "translation.json").write_text(
                    json.dumps(trans_map, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                router = self._make_tts_router()
                router.assign_voices(job.segments, job.selected_voices, job.voice_assign_mode)
                render_cards_for_job(job.segments, job.layout, out_dir / "cards")
                logger.info("[Job #%s] Đã chuẩn hóa lời đọc & tạo %d thẻ xem trước.", job_id, len(job.segments))

            if job_id in self._cancelled:
                return

            if job.require_review:
                job.stage = JobStage.AWAITING_REVIEW.value
                job.progress_pct = 45.0
                job.status_detail = f"Chờ duyệt ({len(job.enabled_segments)} đoạn)"
                logger.info("[Job #%s] Chờ bạn duyệt nội dung trước khi xuất MP4.", job_id)
                self.job_changed.emit(job_id)
                self.counts_changed.emit()
                self.review_ready.emit(job_id)
            else:
                self._run_phase_render(job_id)

        except Exception as exc:
            logger.error("[Job #%s] Lỗi khâu chuẩn bị: %s", job_id, exc)
            job.stage = JobStage.FAILED.value
            job.error_message = str(exc)
            job.status_detail = f"Lỗi: {exc}"
            self.job_changed.emit(job_id)
            self.counts_changed.emit()

    def _run_phase_render(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if not job or job_id in self._cancelled:
            return

        try:
            out_dir = job.ensure_output_dir(self.settings.resolved_output_root())
            (out_dir / "content-reviewed.json").write_text(
                json.dumps([s.to_dict() for s in job.segments], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (out_dir / "translation-reviewed.json").write_text(
                json.dumps({s.seg_id: s.spoken_text for s in job.segments}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # Stage 3: Final card render + multi-voice TTS synthesis
            with self._tts_sem:
                if job_id in self._cancelled:
                    return
                job.stage = JobStage.SYNTHESIZING.value
                job.progress_pct = 62.0
                job.status_detail = "Đang tạo giọng đọc AI đa luồng..."
                self.job_changed.emit(job_id)
                self.counts_changed.emit()

                router = self._make_tts_router()
                router.assign_voices(job.segments, job.selected_voices, job.voice_assign_mode)
                render_cards_for_job(job.segments, job.layout, out_dir / "styled-cards")

                total_dur = router.synthesize_segments_parallel(
                    job.segments,
                    out_dir / "audio",
                    speed=job.voice_speed,
                    max_workers=max(1, min(4, self.settings.max_tts_workers)),
                )
                logger.info(
                    "[Job #%s] Hoàn tất TTS %d đoạn (tổng %.1fs).",
                    job_id,
                    len(job.enabled_segments),
                    total_dur,
                )

            # Stage 4: FFmpeg GPU/CPU Video Composition
            with self._render_sem:
                if job_id in self._cancelled:
                    return
                job.stage = JobStage.RENDERING.value
                job.progress_pct = 84.0
                job.status_detail = "Đang ghép video 1080x1920 & trộn âm thanh..."
                self.job_changed.emit(job_id)

                final_mp4 = compose_final_video(
                    job,
                    out_dir,
                    prefer_nvenc=self.settings.prefer_nvenc,
                )

            job.stage = JobStage.COMPLETED.value
            job.progress_pct = 100.0
            job.status_detail = f"Hoàn tất ({job.total_duration_sec:.1f}s) → {final_mp4.name}"
            logger.info("[Job #%s] Xuất video thành công: %s", job_id, final_mp4)
            self.job_changed.emit(job_id)
            self.counts_changed.emit()
            self.job_finished.emit(job_id, str(final_mp4))

        except Exception as exc:
            logger.error("[Job #%s] Lỗi khâu xuất video: %s", job_id, exc)
            job.stage = JobStage.FAILED.value
            job.error_message = str(exc)
            job.status_detail = f"Lỗi: {exc}"
            self.job_changed.emit(job_id)
            self.counts_changed.emit()

    def _make_tts_router(self) -> TTSRouter:
        return TTSRouter(
            capcut_device_json=self.settings.capcut_device_json,
            vieneu_endpoint=self.settings.vieneu_endpoint,
            vieneu_clone_ref_audio=self.settings.vieneu_clone_ref_audio,
            vieneu_clone_ref_text=self.settings.vieneu_clone_ref_text,
        )
