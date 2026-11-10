"""Main Desktop Studio Window built on Fluent Blue (PyQt6) & Impeccable Operate principles."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import logging
import os
from pathlib import Path
import subprocess
import sys

from fluent_blue import (
    Badge,
    Button,
    Checkbox,
    Column,
    Command,
    CommandPalette,
    DataTable,
    Divider,
    FilePicker,
    FluentWindow,
    FormField,
    IconButton,
    Input,
    LogViewer,
    MultiSelect,
    NumberInput,
    Panel,
    PasswordInput,
    ProgressBar,
    ScrollArea,
    SegmentedControl,
    Select,
    Sidebar,
    Slider,
    Splitter,
    Stack,
    Switch,
    Tabs,
    Text,
    TextArea,
    ThemeManager,
    Toast,
    VideoPlayer,
)
from fluent_blue.compatibility.qt import (
    QApplication,
    QAudioOutput,
    QDesktopServices,
    QHBoxLayout,
    QMediaPlayer,
    QStackedWidget,
    Qt,
    QtCore,
    QUrl,
    QVBoxLayout,
    QWidget,
)

from ..core.config import AppSettings
from ..core.models import (
    CommentMode,
    JobStage,
    LayoutConfig,
    PresetProfile,
    STAGE_LABELS_VI,
    SegmentItem,
    VideoJob,
    VoiceAssignMode,
)
from ..core.pipeline import BatchPipelineManager
from ..render.ffmpeg_compositor import nvenc_available
from ..scrapers.threads_scraper import open_threads_login_session, sample_video1_script
from ..tts.router import TTSRouter, VoiceOption
from .widgets.stage_canvas import InteractiveStageCanvas

Signal = getattr(QtCore, "pyqtSignal", None) or getattr(QtCore, "Signal")
logger = logging.getLogger("threadstudio")
logger.setLevel(logging.INFO)


class _AsyncUiBridge(QObject := QtCore.QObject):
    voice_preview_ready = Signal(str)
    voice_preview_failed = Signal(str)
    login_finished = Signal(str)


class ThreadsStudioWindow(FluentWindow):
    """Three-zone production shell: Sidebar Rail | Task Workspace | 9:16 Interactive Stage."""

    def __init__(self, settings: AppSettings) -> None:
        super().__init__(title="Threads Studio Pro — Tạo Video Đọc Bình Luận Đa Luồng", icon_name="layers")
        self.settings = settings
        self.pipeline = BatchPipelineManager(settings, self)
        self._ui_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ts-ui-helper")
        self._bridge = _AsyncUiBridge(self)

        self._voices: list[VoiceOption] = TTSRouter.available_voices()
        self._voice_label_by_id: dict[str, str] = {v.full_id: f"[{v.badge}] {v.display_name}" for v in self._voices}
        self._current_inspect_job_id: str | None = None
        self._syncing_layout_controls = False
        self._syncing_segment_editor = False

        # Audio preview player
        self._audio_player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._audio_player.setAudioOutput(self._audio_out)
        self._audio_out.setVolume(0.95)

        self.resize(1460, 900)
        self._build_shell()
        self._wire_signals()
        self._install_command_palette()
        self._refresh_queue_table()
        self._sync_layout_spinners_from_cfg()

        gpu_str = "GPU NVIDIA NVENC Sẵn sàng" if nvenc_available() else "CPU libx264 (Không phát hiện NVENC)"
        logger.info("Threads Studio Pro khởi động thành công · %d giọng AI · %s", len(self._voices), gpu_str)

    # ------------------------------------------------------------------
    # Shell Construction
    # ------------------------------------------------------------------

    def _build_shell(self) -> None:
        root = QWidget()
        root_col = QVBoxLayout(root)
        pad = ThemeManager.spacing(2)
        root_col.setContentsMargins(pad, pad, pad, pad)
        root_col.setSpacing(ThemeManager.spacing(2))

        # 1. Top Operational Action Bar
        root_col.addWidget(self._build_top_bar())

        # 2. Main Body + Collapsible Log Splitter (Vertical)
        self._body_log_splitter = Splitter(orientation="vertical", collapsible=True)

        # Horizontal 3-Zone Workspace: Sidebar | Center Pages | Right 9:16 Stage
        workspace_holder = QWidget()
        ws_row = QHBoxLayout(workspace_holder)
        ws_row.setContentsMargins(0, 0, 0, 0)
        ws_row.setSpacing(ThemeManager.spacing(2))

        self._sidebar = Sidebar(width=216, collapsed_width=56)
        self._sidebar.add_section("Sản Xuất")
        self._nav_queue = self._sidebar.add_item("Tạo Lô · Hàng Đợi", key="queue", icon="layers")
        self._nav_inspector = self._sidebar.add_item("Duyệt Kịch Bản", key="inspector", icon="edit")
        self._sidebar.add_section("Cấu Hình Studio")
        self._nav_voices = self._sidebar.add_item("Giọng Đọc AI", key="voices", icon="volume")
        self._nav_media = self._sidebar.add_item("Nguồn Video · Nhạc", key="media", icon="folder")
        self._nav_settings = self._sidebar.add_item("Cài Đặt · Đa Luồng", key="settings", icon="settings")
        ws_row.addWidget(self._sidebar)

        self._pages = QStackedWidget()
        self._page_keys = ["queue", "inspector", "voices", "media", "settings"]
        self._pages.addWidget(self._build_queue_page())
        self._pages.addWidget(self._build_inspector_page())
        self._pages.addWidget(self._build_voices_page())
        self._pages.addWidget(self._build_media_page())
        self._pages.addWidget(self._build_settings_page())

        self._center_stage_splitter = Splitter(
            widgets=[self._pages, self._build_right_stage_panel()],
            orientation="horizontal",
            sizes=[850, 390],
            collapsible=False,
        )
        ws_row.addWidget(self._center_stage_splitter, 1)

        # Bottom Multi-Threaded Log Panel
        log_panel = self._build_log_panel()
        self._body_log_splitter.addWidget(workspace_holder)
        self._body_log_splitter.addWidget(log_panel)
        self._body_log_splitter.setSizes([665, 195])

        root_col.addWidget(self._body_log_splitter, 1)
        self.set_central_widget(root)

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(ThemeManager.spacing(1), 0, ThemeManager.spacing(1), 0)
        row.setSpacing(ThemeManager.spacing(2))

        self._btn_collapse_nav = IconButton(icon="sidebar", tooltip="Thu gọn / mở rộng thanh điều hướng")
        row.addWidget(self._btn_collapse_nav)

        title_lbl = Text("Threads Studio Pro", style="heading-4")
        row.addWidget(title_lbl)

        has_gpu = nvenc_available() and self.settings.prefer_nvenc
        self._encoder_badge = Badge(
            "NVENC 1080p" if has_gpu else "CPU x264 1080p",
            variant="success" if has_gpu else "info",
        )
        row.addWidget(self._encoder_badge)

        row.addStretch(1)

        preset_opts = [(p.name, i) for i, p in enumerate(self.settings.presets)]
        self._preset_select = Select(
            options=preset_opts,
            value=0 if preset_opts else None,
            placeholder="Chọn Preset chủ đề…",
            size="small",
        )
        self._preset_select.setMinimumWidth(250)
        row.addWidget(self._preset_select)

        self._btn_apply_preset = Button("Nạp Preset", size="small", variant="secondary", icon="wand")
        row.addWidget(self._btn_apply_preset)

        self._btn_cmd_palette = Button("Lệnh nhanh (Ctrl+K)", size="small", variant="ghost", icon="search")
        row.addWidget(self._btn_cmd_palette)

        self._switch_dark = Switch("Giao diện tối", checked=(ThemeManager.current() == "dark"))
        row.addWidget(self._switch_dark)

        self._btn_open_outputs = Button("Kết quả", size="small", variant="outline", icon="folder-open")
        row.addWidget(self._btn_open_outputs)
        return bar

    # ------------------------------------------------------------------
    # Page 1: Batch Ingestion & Multi-Threaded Queue
    # ------------------------------------------------------------------

    def _build_queue_page(self) -> QWidget:
        scroll = ScrollArea()
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, ThemeManager.spacing(2), 0)
        col.setSpacing(ThemeManager.spacing(3))

        # Section A: Batch Input Panel
        btn_demo = Button("Bài Mẫu Video 1", size="small", variant="secondary", icon="zap")
        btn_paste = Button("Dán Link", size="small", variant="ghost", icon="copy")
        btn_login_quick = Button("Đăng nhập Threads", size="small", variant="outline", icon="user")
        ingest_panel = Panel(
            title="Nhập Bài Viết Threads (Đơn Lẻ Hoặc Chạy Lô)",
            actions=[btn_login_quick, btn_demo, btn_paste],
            padding=3,
        )

        self._input_mode_seg = SegmentedControl(
            options=[
                ("Dán Lô Link Threads", "urls"),
                ("Tự Nhập Kịch Bản", "manual"),
            ],
            value="urls",
        )
        ingest_panel.add(self._input_mode_seg)

        self._batch_input_area = TextArea(
            placeholder=(
                "Dán 1 hoặc 50 đường link bài viết Threads (mỗi dòng 1 link)...\n"
                "Ví dụ:\nhttps://www.threads.com/@user/post/ABC123\nhttps://www.threads.com/share/XYZ789"
            ),
            rows=4,
        )
        ingest_panel.add(self._batch_input_area)

        # Quick row: Comment mode, comment count, speed
        opts_row = Stack(direction="horizontal", spacing=3, align="center")

        self._cmt_mode_select = Select(
            options=[
                ("Nhiều tim · Gồm cả tác giả", CommentMode.TOP_LIKES_WITH_AUTHOR.value),
                ("Nhiều tim · Không lấy tác giả", CommentMode.TOP_LIKES_NO_AUTHOR.value),
                ("Chỉ bình luận của tác giả (Kể chuyện)", CommentMode.AUTHOR_ONLY.value),
            ],
            value=self.settings.comment_mode,
        )
        opts_row.add(FormField("Chế độ lấy bình luận", self._cmt_mode_select), 3)

        self._cmt_count_spin = NumberInput(
            value=self.settings.max_comments,
            minimum=1,
            maximum=25,
            step=1,
            decimals=0,
        )
        opts_row.add(FormField("Số bình luận", self._cmt_count_spin), 1)

        self._speed_quick_spin = NumberInput(
            value=self.settings.voice_speed,
            minimum=0.75,
            maximum=1.80,
            step=0.05,
            decimals=2,
        )
        opts_row.add(FormField("Tốc độ đọc", self._speed_quick_spin), 1)
        ingest_panel.add(opts_row)

        flags_cta_row = Stack(direction="horizontal", spacing=3, align="center")
        self._chk_hide_name = Checkbox("Che tên tài khoản", checked=self.settings.layout.hide_username)
        self._chk_require_review = Checkbox(
            "Duyệt và sửa từ viết tắt trước khi xuất",
            checked=self.settings.require_review_default,
        )
        flags_cta_row.add(self._chk_hide_name)
        flags_cta_row.add(self._chk_require_review)
        flags_cta_row.add_stretch(1)

        self._btn_enqueue = Button(
            "Tạo Video Đa Luồng",
            variant="primary",
            size="medium",
            icon="play",
        )
        flags_cta_row.add(self._btn_enqueue)
        ingest_panel.add(flags_cta_row)
        col.addWidget(ingest_panel)

        # Section B: Batch Queue DataTable
        self._btn_approve_all = Button("Duyệt Tất Cả Chờ", size="small", variant="primary", icon="check-circle")
        self._btn_inspect_selected = Button("Sửa Kịch Bản", size="small", variant="secondary", icon="edit")
        self._btn_watch_selected = Button("Xem MP4", size="small", variant="outline", icon="play")
        self._btn_stop_all = Button("Dừng Lô", size="small", variant="danger", icon="stop")

        queue_panel = Panel(
            title="Hàng Đợi Xử Lý Đa Luồng",
            actions=[
                self._btn_inspect_selected,
                self._btn_approve_all,
                self._btn_watch_selected,
                self._btn_stop_all,
            ],
            padding=3,
        )

        self._batch_progress = ProgressBar(value=0, show_label=True, thickness=6)
        queue_panel.add(self._batch_progress)

        self._queue_table = DataTable(
            columns=[
                Column("job_id", "Mã", width=82),
                Column("slug", "Bài viết / Chủ đề", width=185),
                Column("segments", "Số đoạn", width=80, align="center"),
                Column("stage_label", "Khâu hiện tại", width=155),
                Column("progress", "Tiến độ", width=85, align="right", formatter=lambda v: f"{int(v)}%"),
                Column("detail", "Chi tiết trạng thái", width=260),
            ],
            rows=[],
            page_size=None,
            selection="single",
            searchable=True,
            exportable=False,
            empty_title="Chưa có video nào trong hàng đợi",
            empty_description="Dán danh sách link Threads hoặc bấm 'Nạp Bài Mẫu Video 1' để chạy thử ngay.",
        )
        self._queue_table.setMinimumHeight(290)
        queue_panel.add(self._queue_table)
        col.addWidget(queue_panel, 1)

        # Wire local buttons
        btn_demo.clicked.connect(self._load_demo_script)
        btn_paste.clicked.connect(self._paste_links_from_clipboard)
        btn_login_quick.clicked.connect(self._start_threads_login)

        scroll.setWidget(container)
        return scroll

    # ------------------------------------------------------------------
    # Page 2: Inline Script & Teencode Inspector (No Blocking Modal!)
    # ------------------------------------------------------------------

    def _build_inspector_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, ThemeManager.spacing(2), 0)
        col.setSpacing(ThemeManager.spacing(2))

        self._btn_re_normalize = Button("Bung Lại Từ Viết Tắt", size="small", variant="secondary", icon="wand")
        self._btn_approve_current = Button(
            "Duyệt Xong · Xuất Video Ngay",
            size="medium",
            variant="primary",
            icon="check",
        )
        header_panel = Panel(
            title="Duyệt · Chỉnh Sửa Kịch Bản Trước Khi Xuất",
            actions=[self._btn_re_normalize, self._btn_approve_current],
            padding=3,
        )

        job_pick_row = Stack(direction="horizontal", spacing=2, align="center")
        job_pick_row.add(Text("Đang kiểm tra Job:", style="label", color="text-secondary"))
        self._inspect_job_select = Select(options=[], placeholder="Chọn một Job trong hàng đợi…")
        job_pick_row.add(self._inspect_job_select, 1)
        header_panel.add(job_pick_row)
        col.addWidget(header_panel)

        # Split left segment table vs right text & voice editor
        split = Splitter(orientation="horizontal", collapsible=False)

        left_box = Panel(title="Thứ Tự Xuất Hiện", padding=3)
        self._seg_table = DataTable(
            columns=[
                Column("idx_label", "#", width=54, sortable=False),
                Column("meta", "Tác giả / Tim", width=110, sortable=False),
                Column("snippet", "Nội dung", width=170, sortable=False),
            ],
            rows=[],
            page_size=None,
            selection="single",
            searchable=False,
            column_menu=False,
            exportable=False,
            empty_title="Chưa chọn Job",
            empty_description="Chọn 1 bài trong Hàng Đợi để sửa lời đọc.",
            row_height=34,
        )
        left_box.add(self._seg_table, 1)

        order_btns = Stack(direction="horizontal", spacing=2)
        self._btn_seg_up = Button("Lên", size="small", icon="arrow-up", full_width=True)
        self._btn_seg_down = Button("Xuống", size="small", icon="arrow-down", full_width=True)
        order_btns.add(self._btn_seg_up, 1)
        order_btns.add(self._btn_seg_down, 1)
        left_box.add(order_btns)

        right_box = Panel(title="Chi Tiết Đoạn · Lời Đọc TTS", padding=3)
        top_seg_flags = Stack(direction="horizontal", spacing=2, align="center")
        self._chk_seg_enabled = Checkbox("Đọc và hiển thị đoạn này trong video", checked=True)
        top_seg_flags.add(self._chk_seg_enabled)
        top_seg_flags.add_stretch(1)
        self._btn_preview_seg_voice = Button("Nghe thử đoạn này", size="small", variant="outline", icon="volume")
        top_seg_flags.add(self._btn_preview_seg_voice)
        right_box.add(top_seg_flags)

        voice_opts = [(lbl, vid) for vid, lbl in self._voice_label_by_id.items()]
        self._seg_voice_select = Select(options=voice_opts, placeholder="Chọn giọng đọc riêng cho đoạn này…")
        right_box.add(FormField("Giọng đọc của đoạn này", self._seg_voice_select))

        self._seg_spoken_area = TextArea(
            placeholder="Lời đọc TTS (đã tự sửa từ viết tắt ko -> không, đc -> được)...",
            rows=4,
        )
        right_box.add(
            FormField(
                "Lời đọc AI (Có thể chỉnh sửa trực tiếp để máy đọc chuẩn 100%)",
                self._seg_spoken_area,
            )
        )

        self._seg_raw_area = TextArea(
            placeholder="Chữ hiển thị trên thẻ bình luận trắng/tối...",
            rows=4,
        )
        right_box.add(FormField("Chữ hiển thị trên Thẻ Threads (Xem trực tiếp bên phải)", self._seg_raw_area))

        nav_seg_btns = Stack(direction="horizontal", spacing=2)
        self._btn_prev_seg = Button("Đoạn trước", size="small", icon="chevron-left")
        self._btn_next_seg = Button("Đoạn tiếp", size="small", icon="chevron-right", icon_position="right")
        nav_seg_btns.add(self._btn_prev_seg)
        nav_seg_btns.add(self._btn_next_seg)
        nav_seg_btns.add_stretch(1)
        right_box.add(nav_seg_btns)

        split.addWidget(left_box)
        split.addWidget(right_box)
        split.setSizes([335, 495])
        col.addWidget(split, 1)
        return page

    # ------------------------------------------------------------------
    # Page 3: Multi-Engine AI Voice Studio (CapCut + Edge + VieNeu)
    # ------------------------------------------------------------------

    def _build_voices_page(self) -> QWidget:
        scroll = ScrollArea()
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, ThemeManager.spacing(2), 0)
        col.setSpacing(ThemeManager.spacing(3))

        # Voice Audition Panel
        audition_panel = Panel(title="Nghe Thử Giọng Đọc AI (CapCut · Edge · VieNeu-TTS)", padding=4)
        voice_opts = [(f"[{v.badge}] {v.display_name}", v.full_id) for v in self._voices]
        valid_preview = (
            self.settings.preview_voice_id
            if self.settings.preview_voice_id in self._voice_label_by_id
            else (voice_opts[0][1] if voice_opts else None)
        )
        self._audition_voice_select = Select(options=voice_opts, value=valid_preview)
        audition_panel.add(FormField("Chọn giọng nghe thử", self._audition_voice_select))

        sample_row = Stack(direction="horizontal", spacing=2, align="center")
        self._audition_input = Input(
            text="Có ai thích từ nức nở trong tiếng Việt giống mình không, nghe vừa sâu lắng vừa da diết.",
            clearable=True,
        )
        sample_row.add(self._audition_input, 1)
        self._btn_audition_play = Button("Nghe thử", variant="primary", icon="volume")
        sample_row.add(self._btn_audition_play)
        audition_panel.add(sample_row)
        col.addWidget(audition_panel)

        # Multi-Voice Pool Selection for Video Conversations
        pool_panel = Panel(title="Bộ Giọng Phối Hợp Trong Video (Đọc Đa Giọng Tự Nhiên)", padding=4)
        valid_selected = [v for v in self.settings.selected_voices if v in self._voice_label_by_id]
        if not valid_selected and voice_opts:
            valid_selected = [voice_opts[0][1]]

        self._voice_pool_multiselect = MultiSelect(
            options=voice_opts,
            values=valid_selected,
            placeholder="Tích chọn các giọng muốn phối hợp trong video…",
            max_labels=3,
        )
        pool_panel.add(
            FormField(
                "Tích chọn bao nhiêu giọng tuỳ thích (CapCut TTS + Edge + VieNeu có thể trộn chung)",
                self._voice_pool_multiselect,
            )
        )

        mix_row = Stack(direction="horizontal", spacing=3)
        self._voice_assign_select = Select(
            options=[
                ("Ngẫu nhiên đổi giọng mỗi bình luận", VoiceAssignMode.RANDOM.value),
                ("Luân phiên theo vòng lặp", VoiceAssignMode.ROUND_ROBIN.value),
                ("Giữ 1 giọng duy nhất (Hợp kể chuyện)", VoiceAssignMode.SINGLE.value),
            ],
            value=self.settings.voice_assign_mode,
        )
        mix_row.add(FormField("Cách phối giọng giữa các thẻ", self._voice_assign_select), 2)
        pool_panel.add(mix_row)
        col.addWidget(pool_panel)

        # VieNeu-TTS v3 Turbo Instant Voice Cloning Panel
        vieneu_status = "Đã cài SDK vieneu" if TTSRouter.vieneu_installed() else "Chạy qua Local Endpoint hoặc cài `pip install vieneu`"
        clone_panel = Panel(
            title="Nhân Bản Giọng Đọc Riêng (VieNeu-TTS Instant Clone 3–8s)",
            actions=[Badge(vieneu_status, variant="info")],
            padding=4,
        )
        self._clone_audio_picker = FilePicker(
            mode="open",
            filter="Audio (*.wav *.mp3 *.flac *.m4a)",
            placeholder="Chọn mẫu ghi âm 3–8 giây giọng gốc sạch nhiễu…",
            button_text="Chọn file mẫu",
        )
        if self.settings.vieneu_clone_ref_audio:
            self._clone_audio_picker.set_paths([self.settings.vieneu_clone_ref_audio])
        clone_panel.add(FormField("File âm thanh mẫu (Reference Audio)", self._clone_audio_picker))

        self._clone_ref_text = Input(
            text=self.settings.vieneu_clone_ref_text,
            placeholder="Nhập đúng câu thoại nói trong file mẫu 3–8s ở trên…",
            clearable=True,
        )
        clone_panel.add(FormField("Nội dung chữ của file mẫu (Reference Transcript)", self._clone_ref_text))

        self._vieneu_endpoint_input = Input(
            text=self.settings.vieneu_endpoint,
            placeholder="Để trống nếu dùng SDK Python `vieneu` trực tiếp, hoặc nhập http://127.0.0.1:8000",
            clearable=True,
        )
        clone_panel.add(FormField("VieNeu HTTP Server (Tuỳ chọn)", self._vieneu_endpoint_input))
        col.addWidget(clone_panel)
        col.addStretch(1)

        scroll.setWidget(container)
        return scroll

    # ------------------------------------------------------------------
    # Page 4: Media Folders & Audio Mixing Presets
    # ------------------------------------------------------------------

    def _build_media_page(self) -> QWidget:
        scroll = ScrollArea()
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, ThemeManager.spacing(2), 0)
        col.setSpacing(ThemeManager.spacing(3))

        btn_save_preset = Button("Lưu Thành Preset Mới", size="small", variant="primary", icon="save")
        folders_panel = Panel(
            title="Nguồn Video & Âm Thanh (Tự Chọn Ngẫu Nhiên & Tự Lặp Đủ Thời Lượng)",
            actions=[btn_save_preset],
            padding=4,
        )

        self._picker_bg = FilePicker(mode="directory", button_text="Chọn thư mục")
        self._picker_bg.set_paths([self.settings.media.bg_video_dir])
        folders_panel.add(FormField("Thư mục Video Nền 9:16 (Ví dụ: nền lưới 3D ô vuông)", self._picker_bg))

        self._chk_sub_video = Checkbox(
            "Bật Video Phụ (Meme / Anime / Gameplay tắt tiếng gốc)",
            checked=self.settings.layout.sub_enabled,
        )
        folders_panel.add(self._chk_sub_video)

        self._picker_sub = FilePicker(mode="directory", button_text="Chọn thư mục")
        self._picker_sub.set_paths([self.settings.media.sub_video_dir])
        folders_panel.add(FormField("Thư mục Video Phụ (Tự cắt ghép ngẫu nhiên)", self._picker_sub))

        folders_panel.add(Divider())

        self._picker_bgm = FilePicker(mode="directory", button_text="Chọn thư mục")
        self._picker_bgm.set_paths([self.settings.media.bgm_dir])
        folders_panel.add(FormField("Thư mục Nhạc Nền (Kể chuyện / Hài hước)", self._picker_bgm))

        bgm_mix_row = Stack(direction="horizontal", spacing=3, align="center")
        self._chk_bgm = Checkbox("Bật Nhạc Nền", checked=self.settings.media.bgm_enabled)
        self._slider_bgm = Slider(value=self.settings.media.bgm_volume_pct, minimum=0, maximum=100)
        self._spin_bgm = NumberInput(value=self.settings.media.bgm_volume_pct, minimum=0, maximum=100, step=5)
        self._spin_bgm.setMaximumWidth(95)
        bgm_mix_row.add(self._chk_bgm)
        bgm_mix_row.add(self._slider_bgm, 1)
        bgm_mix_row.add(self._spin_bgm)
        bgm_mix_row.add(Text("%", color="text-secondary"))
        folders_panel.add(bgm_mix_row)

        self._picker_sfx = FilePicker(mode="directory", button_text="Chọn thư mục")
        self._picker_sfx.set_paths([self.settings.media.sfx_dir])
        folders_panel.add(
            FormField(
                "Thư mục Hiệu Ứng Chuyển Cảnh SFX (Tắt mục này nếu làm video kể chuyện trầm lắng)",
                self._picker_sfx,
            )
        )

        sfx_mix_row = Stack(direction="horizontal", spacing=3, align="center")
        self._chk_sfx = Checkbox("Bật Hiệu Ứng Chuyển Cảnh", checked=self.settings.media.sfx_enabled)
        self._slider_sfx = Slider(value=self.settings.media.sfx_volume_pct, minimum=0, maximum=100)
        self._spin_sfx = NumberInput(value=self.settings.media.sfx_volume_pct, minimum=0, maximum=100, step=5)
        self._spin_sfx.setMaximumWidth(95)
        sfx_mix_row.add(self._chk_sfx)
        sfx_mix_row.add(self._slider_sfx, 1)
        sfx_mix_row.add(self._spin_sfx)
        sfx_mix_row.add(Text("%", color="text-secondary"))
        folders_panel.add(sfx_mix_row)

        preset_save_row = Stack(direction="horizontal", spacing=2, align="center")
        self._new_preset_name = Input(placeholder="Đặt tên Preset mới (VD: Kênh Tâm Sự Đêm Khuya)…", clearable=True)
        preset_save_row.add(self._new_preset_name, 1)
        folders_panel.add(preset_save_row)

        col.addWidget(folders_panel)
        col.addStretch(1)

        btn_save_preset.clicked.connect(self._save_current_as_preset)
        scroll.setWidget(container)
        return scroll

    # ------------------------------------------------------------------
    # Page 5: Credentials, Threads Session & Multi-Thread Worker Pool
    # ------------------------------------------------------------------

    def _build_settings_page(self) -> QWidget:
        scroll = ScrollArea()
        container = QWidget()
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, ThemeManager.spacing(2), 0)
        col.setSpacing(ThemeManager.spacing(3))

        auth_panel = Panel(title="Tài Khoản Threads & AI Chuẩn Hóa Teencode", padding=4)
        login_row = Stack(direction="horizontal", spacing=3, align="center")
        self._btn_login_threads = Button("Mở Trình Duyệt Đăng Nhập Threads", variant="primary", icon="globe")
        login_row.add(self._btn_login_threads)
        login_row.add(
            Text(
                "Đăng nhập 1 lần để lưu cookie vào user_data/threads_profile — cào được toàn bộ bình luận ẩn.",
                color="text-secondary",
                wrap=True,
            ),
            1,
        )
        auth_panel.add(login_row)

        self._gemini_key_input = PasswordInput(
            text=self.settings.gemini_api_key,
            placeholder="AIzaSy... (Để trống vẫn chạy tốt bằng bộ từ điển Teencode nội bộ)",
            clearable=True,
        )
        auth_panel.add(
            FormField(
                "Gemini API Key (Nâng độ chính xác sửa viết tắt lên 98% + Đọc chữ trong ảnh meme chat)",
                self._gemini_key_input,
            )
        )

        self._capcut_dev_picker = FilePicker(
            mode="open",
            filter="JSON (*.json)",
            placeholder="Mặc định tự cấp thiết bị ảo CapCut (chỉ chọn nếu muốn dùng device.json riêng)",
            button_text="Chọn JSON",
        )
        if self.settings.capcut_device_json:
            self._capcut_dev_picker.set_paths([self.settings.capcut_device_json])
        auth_panel.add(FormField("CapCut Device Config (Tuỳ chọn)", self._capcut_dev_picker))
        col.addWidget(auth_panel)

        # Concurrency & Hardware Acceleration Panel
        perf_panel = Panel(title="Cấu Hình Đa Luồng & Tăng Tốc Phần Cứng GPU", padding=4)
        workers_row = Stack(direction="horizontal", spacing=3)

        self._spin_w_scrape = NumberInput(value=self.settings.max_scrape_workers, minimum=1, maximum=6, step=1)
        self._spin_w_ai = NumberInput(value=self.settings.max_ai_workers, minimum=1, maximum=12, step=1)
        self._spin_w_tts = NumberInput(value=self.settings.max_tts_workers, minimum=1, maximum=12, step=1)
        self._spin_w_render = NumberInput(value=self.settings.max_render_workers, minimum=1, maximum=6, step=1)

        workers_row.add(FormField("Luồng Cào Threads", self._spin_w_scrape), 1)
        workers_row.add(FormField("Luồng Gemini AI", self._spin_w_ai), 1)
        workers_row.add(FormField("Luồng Tạo Voice TTS", self._spin_w_tts), 1)
        workers_row.add(FormField("Luồng Ghép FFmpeg", self._spin_w_render), 1)
        perf_panel.add(workers_row)

        self._switch_nvenc = Switch(
            "Ưu tiên mã hóa phần cứng NVIDIA NVENC (h264_nvenc) khi xuất hàng loạt",
            checked=self.settings.prefer_nvenc,
        )
        perf_panel.add(self._switch_nvenc)

        self._picker_out_root = FilePicker(mode="directory", button_text="Đổi thư mục")
        self._picker_out_root.set_paths([str(self.settings.resolved_output_root())])
        perf_panel.add(FormField("Thư mục lưu Video xuất ra", self._picker_out_root))

        self._btn_save_settings = Button("Lưu Toàn Bộ Cài Đặt", variant="primary", icon="save")
        perf_panel.add(self._btn_save_settings)
        col.addWidget(perf_panel)
        col.addStretch(1)

        scroll.setWidget(container)
        return scroll

    # ------------------------------------------------------------------
    # Persistent Right 9:16 Stage & Layout Inspector
    # ------------------------------------------------------------------

    def _build_right_stage_panel(self) -> QWidget:
        btn_snap = Button("Chụp thử ảnh", size="small", variant="ghost", icon="image")
        btn_reset_layout = Button("Đặt lại", size="small", variant="ghost", icon="refresh")
        stage_panel = Panel(
            title="Sân Khấu 9:16 · 1080p",
            actions=[btn_snap, btn_reset_layout],
            padding=3,
        )

        self._stage_tabs = Tabs()
        # Tab 1: Interactive 9:16 Canvas
        self._canvas = InteractiveStageCanvas(self.settings.layout)
        self._stage_tabs.add_tab(self._canvas, "Bố Cục Kéo-Thả", icon="layout")

        # Tab 2: Built-in VideoPlayer for watching rendered MP4s
        self._video_player = VideoPlayer()
        self._stage_tabs.add_tab(self._video_player, "Xem Thành Phẩm", icon="play")
        stage_panel.add(self._stage_tabs, 1)

        # Compact coordinate & style tabs below canvas (matching & upgrading Video 2)
        self._geom_tabs = Tabs()

        # Geometry Tab A: Comment Card
        card_tab = QWidget()
        c_col = QVBoxLayout(card_tab)
        c_col.setContentsMargins(4, 6, 4, 4)
        c_col.setSpacing(ThemeManager.spacing(2))

        c_row1 = Stack(direction="horizontal", spacing=2)
        self._spin_card_x = NumberInput(value=self.settings.layout.card_x_pct, minimum=5, maximum=95, step=1, decimals=1)
        self._spin_card_y = NumberInput(value=self.settings.layout.card_y_pct, minimum=5, maximum=95, step=1, decimals=1)
        self._spin_card_scale = NumberInput(
            value=self.settings.layout.card_scale_pct, minimum=50, maximum=122, step=2, decimals=1
        )
        c_row1.add(FormField("X %", self._spin_card_x), 1)
        c_row1.add(FormField("Y %", self._spin_card_y), 1)
        c_row1.add(FormField("Cỡ %", self._spin_card_scale), 1)
        c_col.addWidget(c_row1)

        c_row2 = Stack(direction="horizontal", spacing=2)
        self._spin_card_rad = NumberInput(value=self.settings.layout.card_radius_px, minimum=0, maximum=48, step=2)
        self._sel_card_theme = Select(
            options=[("Trắng (Chuẩn Threads)", "light"), ("Tối (Dark Card)", "dark")],
            value=self.settings.layout.card_theme,
        )
        c_row2.add(FormField("Bo góc (px)", self._spin_card_rad), 1)
        c_row2.add(FormField("Nền thẻ", self._sel_card_theme), 2)
        c_col.addWidget(c_row2)
        self._geom_tabs.add_tab(card_tab, "Thẻ bình luận")

        # Geometry Tab B: Sub-video
        sub_tab = QWidget()
        s_col = QVBoxLayout(sub_tab)
        s_col.setContentsMargins(4, 6, 4, 4)
        s_col.setSpacing(ThemeManager.spacing(2))

        s_row1 = Stack(direction="horizontal", spacing=2)
        self._spin_sub_x = NumberInput(value=self.settings.layout.sub_x_pct, minimum=5, maximum=95, step=1, decimals=1)
        self._spin_sub_y = NumberInput(value=self.settings.layout.sub_y_pct, minimum=5, maximum=95, step=1, decimals=1)
        self._spin_sub_w = NumberInput(value=self.settings.layout.sub_w_pct, minimum=25, maximum=100, step=2, decimals=1)
        self._spin_sub_h = NumberInput(value=self.settings.layout.sub_h_pct, minimum=12, maximum=65, step=2, decimals=1)
        s_row1.add(FormField("X %", self._spin_sub_x), 1)
        s_row1.add(FormField("Y %", self._spin_sub_y), 1)
        s_row1.add(FormField("Rộng %", self._spin_sub_w), 1)
        s_row1.add(FormField("Cao %", self._spin_sub_h), 1)
        s_col.addWidget(s_row1)

        s_row2 = Stack(direction="horizontal", spacing=2)
        self._spin_sub_rad = NumberInput(value=self.settings.layout.sub_radius_px, minimum=0, maximum=48, step=2)
        self._sel_sub_fit = Select(
            options=[("Lấp đầy khung (Cover)", "cover"), ("Vừa khít khung (Contain)", "contain")],
            value=self.settings.layout.sub_fit,
        )
        s_row2.add(FormField("Bo góc (px)", self._spin_sub_rad), 1)
        s_row2.add(FormField("Tỉ lệ khung", self._sel_sub_fit), 2)
        s_col.addWidget(s_row2)
        self._geom_tabs.add_tab(sub_tab, "Video phụ")

        stage_panel.add(self._geom_tabs)

        btn_snap.clicked.connect(self._export_stage_snapshot)
        btn_reset_layout.clicked.connect(self._reset_layout_defaults)
        return stage_panel

    def _build_log_panel(self) -> QWidget:
        btn_clear_log = Button("Xoá nhật ký", size="small", variant="ghost", icon="eraser")
        panel = Panel(title="Nhật Ký Xử Lý Đa Luồng", actions=[btn_clear_log], padding=2)
        self._log_viewer = LogViewer(max_entries=10000, timestamps=True)
        self._log_viewer.setMinimumHeight(110)
        self._log_handler = self._log_viewer.attach_logger(logger, level=logging.INFO)
        panel.add(self._log_viewer, 1)
        btn_clear_log.clicked.connect(self._log_viewer.clear)
        return panel

    # ------------------------------------------------------------------
    # Signal Wiring & Live Synchronization
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        self._btn_collapse_nav.clicked.connect(
            lambda: self._sidebar.set_collapsed(not self._sidebar.is_collapsed())
        )
        self._sidebar.current_changed.connect(self._on_nav_changed)
        self._switch_dark.toggled.connect(self._on_theme_toggled)
        self._btn_apply_preset.clicked.connect(self._apply_selected_preset)
        self._btn_open_outputs.clicked.connect(self._open_output_root)

        # Queue & Batch actions
        self._btn_enqueue.clicked.connect(self._on_enqueue_clicked)
        self._btn_approve_all.clicked.connect(self._on_approve_all_clicked)
        self._btn_inspect_selected.clicked.connect(self._open_selected_job_in_inspector)
        self._btn_watch_selected.clicked.connect(self._watch_selected_job_video)
        self._btn_stop_all.clicked.connect(self._on_stop_all_clicked)
        self._queue_table.row_activated.connect(self._on_queue_row_activated)

        # Pipeline signals
        self.pipeline.job_changed.connect(self._on_pipeline_job_changed)
        self.pipeline.review_ready.connect(self._on_pipeline_review_ready)
        self.pipeline.job_finished.connect(self._on_pipeline_job_finished)
        self.pipeline.counts_changed.connect(self._update_badges_and_progress)

        # Inspector signals
        self._current_seg_idx = 0
        self._inspect_job_select.value_changed.connect(self._load_job_into_inspector)
        self._seg_table.selection_changed.connect(self._on_inspector_table_selected)
        self._chk_seg_enabled.toggled.connect(self._on_segment_fields_edited)
        self._seg_voice_select.value_changed.connect(self._on_segment_fields_edited)
        self._seg_spoken_area.text_changed.connect(self._on_segment_fields_edited)
        self._seg_raw_area.text_changed.connect(self._on_segment_fields_edited)
        self._btn_seg_up.clicked.connect(lambda: self._move_current_segment(-1))
        self._btn_seg_down.clicked.connect(lambda: self._move_current_segment(1))
        self._btn_prev_seg.clicked.connect(lambda: self._step_segment_selection(-1))
        self._btn_next_seg.clicked.connect(lambda: self._step_segment_selection(1))
        self._btn_re_normalize.clicked.connect(self._renormalize_current_job)
        self._btn_approve_current.clicked.connect(self._approve_current_inspected_job)
        self._btn_preview_seg_voice.clicked.connect(self._preview_current_segment_audio)

        # Voice studio signals
        self._btn_audition_play.clicked.connect(self._play_voice_audition)
        self._bridge.voice_preview_ready.connect(self._on_voice_preview_ready)
        self._bridge.voice_preview_failed.connect(self._on_voice_preview_failed)
        self._bridge.login_finished.connect(lambda msg: Toast.show(msg, parent=self, variant="success"))

        # Layout & Canvas live sync
        self._canvas.layout_edited.connect(self._sync_layout_spinners_from_cfg)
        self._canvas.layer_selected.connect(
            lambda layer: self._geom_tabs.setCurrentIndex(0 if layer == "card" else 1)
        )
        self._chk_hide_name.toggled.connect(self._on_layout_controls_changed)
        self._chk_sub_video.toggled.connect(self._on_layout_controls_changed)
        for sp in (
            self._spin_card_x,
            self._spin_card_y,
            self._spin_card_scale,
            self._spin_card_rad,
            self._spin_sub_x,
            self._spin_sub_y,
            self._spin_sub_w,
            self._spin_sub_h,
            self._spin_sub_rad,
        ):
            sp.value_changed.connect(self._on_layout_controls_changed)
        self._sel_card_theme.value_changed.connect(self._on_layout_controls_changed)
        self._sel_sub_fit.value_changed.connect(self._on_layout_controls_changed)

        # Audio sliders <-> spinners sync
        self._slider_bgm.valueChanged.connect(lambda v: self._spin_bgm.set_value(float(v)))
        self._spin_bgm.value_changed.connect(lambda v: self._slider_bgm.setValue(int(v)))
        self._slider_sfx.valueChanged.connect(lambda v: self._spin_sfx.set_value(float(v)))
        self._spin_sfx.value_changed.connect(lambda v: self._slider_sfx.setValue(int(v)))

        # Settings actions
        self._btn_login_threads.clicked.connect(self._start_threads_login)
        self._btn_save_settings.clicked.connect(self._persist_all_settings)

    def _install_command_palette(self) -> None:
        commands = [
            Command("demo", "Nạp bài mẫu Video 1 (“nức nở”, “thương”…)", icon="zap", action=self._load_demo_script),
            Command("enqueue", "Chạy tạo video đa luồng ngay", icon="play", action=self._on_enqueue_clicked),
            Command("approve_all", "Duyệt tất cả bài đang chờ", icon="check-circle", action=self._on_approve_all_clicked),
            Command("open_out", "Mở thư mục Kết quả xuất MP4", icon="folder-open", action=self._open_output_root),
            Command("theme", "Chuyển đổi giao diện Sáng / Tối", icon="moon", action=ThemeManager.toggle),
            Command("save", "Lưu toàn bộ cài đặt hiện tại", icon="save", action=self._persist_all_settings),
        ]
        self._palette = CommandPalette(
            parent=self,
            commands=commands,
            shortcut="Ctrl+K",
            placeholder="Gõ lệnh nhanh (VD: nạp mẫu, duyệt tất cả, mở thư mục…)",
        )
        self._btn_cmd_palette.clicked.connect(self._palette.open_palette)

    # ------------------------------------------------------------------
    # Actions & Event Handlers
    # ------------------------------------------------------------------

    def _on_nav_changed(self, key: str) -> None:
        if key in self._page_keys:
            self._pages.setCurrentIndex(self._page_keys.index(key))

    def _on_theme_toggled(self, is_dark: bool) -> None:
        target = "dark" if is_dark else "light"
        ThemeManager.set_theme(target)
        self.settings.theme = target

    def _collect_live_settings(self) -> None:
        self.settings.comment_mode = str(self._cmt_mode_select.value() or CommentMode.TOP_LIKES_WITH_AUTHOR.value)
        self.settings.max_comments = int(self._cmt_count_spin.value())
        self.settings.voice_speed = float(self._speed_quick_spin.value())
        self.settings.require_review_default = self._chk_require_review.isChecked()
        self.settings.voice_assign_mode = str(self._voice_assign_select.value() or VoiceAssignMode.RANDOM.value)
        chosen_voices = [str(v) for v in self._voice_pool_multiselect.values()]
        if chosen_voices:
            self.settings.selected_voices = chosen_voices

        self.settings.media.bg_video_dir = self._picker_bg.path()
        self.settings.media.sub_video_dir = self._picker_sub.path()
        self.settings.media.bgm_dir = self._picker_bgm.path()
        self.settings.media.sfx_dir = self._picker_sfx.path()
        self.settings.media.bgm_enabled = self._chk_bgm.isChecked()
        self.settings.media.bgm_volume_pct = int(self._spin_bgm.value())
        self.settings.media.sfx_enabled = self._chk_sfx.isChecked()
        self.settings.media.sfx_volume_pct = int(self._spin_sfx.value())

        self.settings.gemini_api_key = self._gemini_key_input.text().strip()
        self.settings.capcut_device_json = self._capcut_dev_picker.path()
        self.settings.vieneu_clone_ref_audio = self._clone_audio_picker.path()
        self.settings.vieneu_clone_ref_text = self._clone_ref_text.text().strip()
        self.settings.vieneu_endpoint = self._vieneu_endpoint_input.text().strip()
        self.settings.max_scrape_workers = int(self._spin_w_scrape.value())
        self.settings.max_ai_workers = int(self._spin_w_ai.value())
        self.settings.max_tts_workers = int(self._spin_w_tts.value())
        self.settings.max_render_workers = int(self._spin_w_render.value())
        self.settings.prefer_nvenc = self._switch_nvenc.isChecked()
        if self._picker_out_root.path():
            self.settings.output_root = self._picker_out_root.path()

    def _persist_all_settings(self) -> None:
        self._collect_live_settings()
        self.pipeline._rebuild_semaphores()
        self.settings.save()
        has_gpu = nvenc_available() and self.settings.prefer_nvenc
        self._encoder_badge.setText("NVENC 1080p" if has_gpu else "CPU x264 1080p")
        self._encoder_badge.set_variant("success" if has_gpu else "info")
        Toast.show("Đã lưu toàn bộ cấu hình Threads Studio Pro.", parent=self, variant="success")

    def _load_demo_script(self) -> None:
        self._input_mode_seg.set_value("manual")
        self._batch_input_area.set_text(sample_video1_script())
        self._sidebar.set_current("queue")
        Toast.show("Đã nạp kịch bản mẫu từ Video 1 — bấm Tạo Video Đa Luồng để chạy!", parent=self, variant="info")

    def _paste_links_from_clipboard(self) -> None:
        cb = QApplication.clipboard().text().strip()
        if not cb:
            Toast.show("Clipboard hiện đang trống.", parent=self, variant="warning")
            return
        cur = self._batch_input_area.text().strip()
        merged = f"{cur}\n{cb}".strip() if cur else cb
        self._batch_input_area.set_text(merged)

    def _on_enqueue_clicked(self) -> None:
        self._collect_live_settings()
        raw = self._batch_input_area.text().strip()
        if not raw:
            Toast.show("Hãy dán ít nhất 1 link Threads hoặc bấm 'Nạp Bài Mẫu Video 1'.", parent=self, variant="warning")
            return

        mode = self._input_mode_seg.value()
        new_jobs: list[VideoJob] = []

        if mode == "manual":
            # Support batching multiple manual scripts separated by `===`
            scripts = [s.strip() for s in raw.split("===") if s.strip()]
            for sc in scripts:
                new_jobs.append(self._snapshot_new_job(manual_script=sc))
        else:
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            urls = [ln for ln in lines if ln.startswith("http")]
            if not urls:
                # User pasted plain story text while in URL mode — handle gracefully!
                new_jobs.append(self._snapshot_new_job(manual_script=raw))
            else:
                for u in urls:
                    new_jobs.append(self._snapshot_new_job(source_url=u))

        self.pipeline.enqueue_jobs(new_jobs)
        self._refresh_queue_table()
        Toast.show(f"Đã đẩy {len(new_jobs)} tác vụ vào bể xử lý đa luồng!", parent=self, variant="info")

    def _snapshot_new_job(self, *, source_url: str = "", manual_script: str = "") -> VideoJob:
        return VideoJob(
            source_url=source_url,
            manual_script=manual_script,
            require_review=self.settings.require_review_default,
            comment_mode=self.settings.comment_mode,
            max_comments=self.settings.max_comments,
            voice_speed=self.settings.voice_speed,
            voice_assign_mode=self.settings.voice_assign_mode,
            selected_voices=list(self.settings.selected_voices),
            layout=copy.deepcopy(self.settings.layout),
            media=copy.deepcopy(self.settings.media),
        )

    # ------------------------------------------------------------------
    # Pipeline Callbacks & Queue Table Updates
    # ------------------------------------------------------------------

    def _on_pipeline_job_changed(self, job_id: str) -> None:
        self._refresh_queue_table()
        self._refresh_inspector_job_dropdown()
        if self._current_inspect_job_id == job_id:
            job = self.pipeline.jobs.get(job_id)
            if job and job.stage == JobStage.AWAITING_REVIEW.value and not self._seg_table.rows():
                self._populate_inspector_segments(job)

    def _on_pipeline_review_ready(self, job_id: str) -> None:
        self._refresh_inspector_job_dropdown()
        # Automatically load the first ready job into the inspector if none open yet
        if self._current_inspect_job_id is None:
            self._inspect_job_select.set_value(job_id)
            self._load_job_into_inspector(job_id)
            if len(self.pipeline.job_order) == 1:
                self._sidebar.set_current("inspector")
        Toast.show(f"Job #{job_id} đã chuẩn hóa xong — sẵn sàng duyệt kịch bản!", parent=self, variant="info")

    def _on_pipeline_job_finished(self, job_id: str, mp4_path: str) -> None:
        Toast.show(f"Xuất xong MP4 cho Job #{job_id}!", parent=self, variant="success")
        if Path(mp4_path).exists():
            self._video_player.set_source(mp4_path)
            self._stage_tabs.setCurrentIndex(1)

    def _update_badges_and_progress(self) -> None:
        active = self.pipeline.active_count()
        review = self.pipeline.review_count()
        self._nav_queue.set_badge(active if active > 0 else None)
        self._nav_inspector.set_badge(review if review > 0 else None)

        if not self.pipeline.jobs:
            self._batch_progress.set_value(0)
            return
        avg = sum(j.progress_pct for j in self.pipeline.jobs.values()) / len(self.pipeline.jobs)
        self._batch_progress.set_value(avg)

    def _refresh_queue_table(self) -> None:
        rows = []
        for jid in reversed(self.pipeline.job_order):
            j = self.pipeline.jobs[jid]
            rows.append(
                {
                    "job_id": j.job_id,
                    "slug": j.title_slug,
                    "segments": len(j.enabled_segments) if j.segments else j.max_comments + 1,
                    "stage_label": STAGE_LABELS_VI.get(j.stage, j.stage),
                    "progress": j.progress_pct,
                    "detail": j.status_detail,
                }
            )
        self._queue_table.set_rows(rows)

    def _selected_queue_job_id(self) -> str | None:
        sel = self._queue_table.selected_rows()
        if sel:
            return str(sel[0]["job_id"])
        if self.pipeline.job_order:
            return self.pipeline.job_order[-1]
        return None

    def _on_queue_row_activated(self, row: dict) -> None:
        jid = str(row.get("job_id", ""))
        job = self.pipeline.jobs.get(jid)
        if not job:
            return
        if job.stage == JobStage.COMPLETED.value and job.output_mp4 and Path(job.output_mp4).exists():
            self._video_player.set_source(job.output_mp4)
            self._stage_tabs.setCurrentIndex(1)
            self._video_player.play()
        elif job.segments:
            self._open_job_in_inspector(jid)

    def _open_selected_job_in_inspector(self) -> None:
        jid = self._selected_queue_job_id()
        if jid:
            self._open_job_in_inspector(jid)

    def _open_job_in_inspector(self, job_id: str) -> None:
        self._refresh_inspector_job_dropdown()
        job = self.pipeline.jobs.get(job_id)
        if not job or not job.segments:
            Toast.show("Job này đang cào dữ liệu, chờ vài giây để tải kịch bản.", parent=self, variant="info")
            return
        self._inspect_job_select.set_value(job_id)
        self._load_job_into_inspector(job_id)
        self._sidebar.set_current("inspector")

    def _watch_selected_job_video(self) -> None:
        jid = self._selected_queue_job_id()
        job = self.pipeline.jobs.get(jid or "")
        if job and job.output_mp4 and Path(job.output_mp4).exists():
            self._video_player.set_source(job.output_mp4)
            self._stage_tabs.setCurrentIndex(1)
            self._video_player.play()
        else:
            Toast.show("Job này chưa xuất file MP4.", parent=self, variant="warning")

    def _on_approve_all_clicked(self) -> None:
        count = self.pipeline.approve_all_waiting()
        if count:
            Toast.show(f"Đã duyệt & đẩy {count} video vào lò xuất MP4!", parent=self, variant="success")
        else:
            Toast.show("Không có bài nào đang ở trạng thái Chờ duyệt.", parent=self, variant="info")

    def _on_stop_all_clicked(self) -> None:
        self.pipeline.cancel_all_active()
        Toast.show("Đã dừng toàn bộ tác vụ đang chạy.", parent=self, variant="warning")

    # ------------------------------------------------------------------
    # Inspector Editing Logic
    # ------------------------------------------------------------------

    def _refresh_inspector_job_dropdown(self) -> None:
        opts = [
            (f"#{jid} · {self.pipeline.jobs[jid].title_slug} ({STAGE_LABELS_VI.get(self.pipeline.jobs[jid].stage, '')})", jid)
            for jid in reversed(self.pipeline.job_order)
            if self.pipeline.jobs[jid].segments
        ]
        self._inspect_job_select.set_options(opts)
        if self._current_inspect_job_id and any(v == self._current_inspect_job_id for _, v in opts):
            self._inspect_job_select.set_value(self._current_inspect_job_id)

    def _load_job_into_inspector(self, job_id: object) -> None:
        jid = str(job_id or "")
        job = self.pipeline.jobs.get(jid)
        if not job:
            return
        self._current_inspect_job_id = jid
        self._populate_inspector_segments(job)

    def _populate_inspector_segments(self, job: VideoJob, select_idx: int = 0) -> None:
        rows = []
        for idx, seg in enumerate(job.segments):
            mark = "✓" if seg.enabled else "✕"
            idx_lbl = f"{mark} Gốc" if seg.is_root_post else f"{mark} #{idx}"
            meta = f"@{seg.username}" if seg.is_root_post else f"♥ {seg.likes}"
            rows.append(
                {
                    "row_idx": idx,
                    "idx_label": idx_lbl,
                    "meta": meta,
                    "snippet": seg.raw_text.replace("\n", " ")[:48],
                }
            )
        self._seg_table.set_rows(rows)
        if rows:
            clamped = max(0, min(len(rows) - 1, select_idx))
            self._seg_table.select_rows([clamped])
            self._on_inspector_row_selected(clamped)

    def _on_inspector_table_selected(self, selected_rows: list[dict]) -> None:
        if not selected_rows:
            return
        self._on_inspector_row_selected(int(selected_rows[0]["row_idx"]))

    def _on_inspector_row_selected(self, row_idx: int) -> None:
        job = self.pipeline.jobs.get(self._current_inspect_job_id or "")
        if not job or not (0 <= row_idx < len(job.segments)):
            return
        self._current_seg_idx = row_idx
        seg = job.segments[row_idx]
        self._syncing_segment_editor = True
        try:
            self._chk_seg_enabled.setChecked(seg.enabled)
            if seg.voice_id in self._voice_label_by_id:
                self._seg_voice_select.set_value(seg.voice_id)
            self._seg_spoken_area.set_text(seg.spoken_text)
            self._seg_raw_area.set_text(seg.raw_text)
        finally:
            self._syncing_segment_editor = False

        self._stage_tabs.setCurrentIndex(0)
        self._canvas.set_preview_segment(seg)

    def _on_segment_fields_edited(self, *_args) -> None:
        if self._syncing_segment_editor:
            return
        job = self.pipeline.jobs.get(self._current_inspect_job_id or "")
        row = self._current_seg_idx
        if not job or not (0 <= row < len(job.segments)):
            return
        seg = job.segments[row]
        seg.enabled = self._chk_seg_enabled.isChecked()
        if self._seg_voice_select.value():
            seg.voice_id = str(self._seg_voice_select.value())
        seg.spoken_text = self._seg_spoken_area.text()
        seg.raw_text = self._seg_raw_area.text()
        self._canvas.set_preview_segment(seg)

    def _move_current_segment(self, delta: int) -> None:
        job = self.pipeline.jobs.get(self._current_inspect_job_id or "")
        row = self._current_seg_idx
        if not job or (row <= 0 and delta < 0):
            return
        if row == 0:
            Toast.show("Bài viết chính luôn nằm ở đầu video.", parent=self, variant="info")
            return
        target = row + delta
        if target < 1 or target >= len(job.segments):
            return
        job.segments[row], job.segments[target] = job.segments[target], job.segments[row]
        self._populate_inspector_segments(job, select_idx=target)

    def _step_segment_selection(self, delta: int) -> None:
        job = self.pipeline.jobs.get(self._current_inspect_job_id or "")
        if not job or not job.segments:
            return
        nxt = max(0, min(len(job.segments) - 1, self._current_seg_idx + delta))
        self._seg_table.select_rows([nxt])
        self._on_inspector_row_selected(nxt)

    def _renormalize_current_job(self) -> None:
        from ..ai.text_normalizer import normalize_segments_batch

        job = self.pipeline.jobs.get(self._current_inspect_job_id or "")
        if not job:
            return
        normalize_segments_batch(
            job.segments,
            gemini_api_key=self._gemini_key_input.text().strip(),
            gemini_model=self.settings.gemini_model,
            include_emotion_cues=any(v.startswith("vieneu:") for v in job.selected_voices),
        )
        self._on_inspector_row_selected(self._current_seg_idx)
        Toast.show("Đã chuẩn hóa lại từ viết tắt cho toàn bộ các đoạn.", parent=self, variant="success")

    def _approve_current_inspected_job(self) -> None:
        jid = self._current_inspect_job_id
        if not jid or jid not in self.pipeline.jobs:
            return
        job = self.pipeline.jobs[jid]
        job.layout = copy.deepcopy(self.settings.layout)
        self.pipeline.approve_and_render(jid)
        self._sidebar.set_current("queue")
        Toast.show(f"Job #{jid} đã chuyển sang tạo giọng đọc & ghép MP4!", parent=self, variant="success")

    # ------------------------------------------------------------------
    # Voice Audition & Preview
    # ------------------------------------------------------------------

    def _play_voice_audition(self) -> None:
        vid = str(self._audition_voice_select.value() or "edge:vi-VN-HoaiMyNeural")
        txt = self._audition_input.text().strip() or "Xin chào từ Threads Studio Pro."
        self.settings.preview_voice_id = vid
        self._synth_and_play_preview(txt, vid, self._btn_audition_play)

    def _preview_current_segment_audio(self) -> None:
        txt = self._seg_spoken_area.text().strip() or self._seg_raw_area.text().strip()
        vid = str(self._seg_voice_select.value() or "edge:vi-VN-HoaiMyNeural")
        if txt:
            self._synth_and_play_preview(txt, vid, self._btn_preview_seg_voice)

    def _synth_and_play_preview(self, text: str, voice_id: str, btn: Button) -> None:
        self._collect_live_settings()
        btn.set_loading(True)
        preview_wav = self.settings.user_data_dir() / "preview_voice.wav"

        def _worker() -> None:
            try:
                router = self.pipeline._make_tts_router()
                router.synthesize_single(text, voice_id, preview_wav, speed=self.settings.voice_speed)
                self._bridge.voice_preview_ready.emit(str(preview_wav))
            except Exception as exc:
                self._bridge.voice_preview_failed.emit(str(exc))

        self._ui_pool.submit(_worker)

    def _on_voice_preview_ready(self, wav_path: str) -> None:
        self._btn_audition_play.set_loading(False)
        self._btn_preview_seg_voice.set_loading(False)
        self._audio_player.stop()
        self._audio_player.setSource(QUrl.fromLocalFile(wav_path))
        self._audio_player.play()

    def _on_voice_preview_failed(self, err: str) -> None:
        self._btn_audition_play.set_loading(False)
        self._btn_preview_seg_voice.set_loading(False)
        Toast.show(f"Không phát được giọng thử: {err}", parent=self, variant="error")

    # ------------------------------------------------------------------
    # Stage Layout Sync & Presets
    # ------------------------------------------------------------------

    def _sync_layout_spinners_from_cfg(self) -> None:
        self._syncing_layout_controls = True
        try:
            cfg = self.settings.layout
            self._spin_card_x.set_value(cfg.card_x_pct)
            self._spin_card_y.set_value(cfg.card_y_pct)
            self._spin_card_scale.set_value(cfg.card_scale_pct)
            self._spin_card_rad.set_value(cfg.card_radius_px)
            self._sel_card_theme.set_value(cfg.card_theme)
            self._chk_hide_name.setChecked(cfg.hide_username)

            self._chk_sub_video.setChecked(cfg.sub_enabled)
            self._spin_sub_x.set_value(cfg.sub_x_pct)
            self._spin_sub_y.set_value(cfg.sub_y_pct)
            self._spin_sub_w.set_value(cfg.sub_w_pct)
            self._spin_sub_h.set_value(cfg.sub_h_pct)
            self._spin_sub_rad.set_value(cfg.sub_radius_px)
            self._sel_sub_fit.set_value(cfg.sub_fit)
        finally:
            self._syncing_layout_controls = False

    def _on_layout_controls_changed(self, *_args) -> None:
        if self._syncing_layout_controls:
            return
        cfg = self.settings.layout
        cfg.card_x_pct = float(self._spin_card_x.value())
        cfg.card_y_pct = float(self._spin_card_y.value())
        cfg.card_scale_pct = float(self._spin_card_scale.value())
        cfg.card_radius_px = int(self._spin_card_rad.value())
        cfg.card_theme = str(self._sel_card_theme.value() or "light")
        cfg.hide_username = self._chk_hide_name.isChecked()

        cfg.sub_enabled = self._chk_sub_video.isChecked()
        cfg.sub_x_pct = float(self._spin_sub_x.value())
        cfg.sub_y_pct = float(self._spin_sub_y.value())
        cfg.sub_w_pct = float(self._spin_sub_w.value())
        cfg.sub_h_pct = float(self._spin_sub_h.value())
        cfg.sub_radius_px = int(self._spin_sub_rad.value())
        cfg.sub_fit = str(self._sel_sub_fit.value() or "cover")

        self._canvas.refresh_card()

    def _reset_layout_defaults(self) -> None:
        self.settings.layout = LayoutConfig()
        self._canvas.set_layout_config(self.settings.layout)
        self._sync_layout_spinners_from_cfg()

    def _export_stage_snapshot(self) -> None:
        out_png = self.settings.resolved_output_root() / "preview_snapshot_9x16.png"
        self._canvas.export_snapshot(str(out_png))
        Toast.show(f"Đã lưu ảnh chụp bố cục: {out_png.name}", parent=self, variant="success")

    def _apply_selected_preset(self) -> None:
        idx = self._preset_select.value()
        if idx is None or not (0 <= int(idx) < len(self.settings.presets)):
            return
        p = self.settings.presets[int(idx)]
        self._cmt_mode_select.set_value(p.comment_mode)
        self._cmt_count_spin.set_value(p.max_comments)
        self._speed_quick_spin.set_value(p.voice_speed)
        self._voice_assign_select.set_value(p.voice_assign_mode)
        valid_v = [v for v in p.selected_voices if v in self._voice_label_by_id]
        if valid_v:
            self._voice_pool_multiselect.set_values(valid_v)

        self.settings.media = copy.deepcopy(p.media)
        self._picker_bg.set_paths([p.media.bg_video_dir])
        self._picker_sub.set_paths([p.media.sub_video_dir])
        self._picker_bgm.set_paths([p.media.bgm_dir])
        self._picker_sfx.set_paths([p.media.sfx_dir])
        self._chk_bgm.setChecked(p.media.bgm_enabled)
        self._spin_bgm.set_value(p.media.bgm_volume_pct)
        self._chk_sfx.setChecked(p.media.sfx_enabled)
        self._spin_sfx.set_value(p.media.sfx_volume_pct)

        self.settings.layout = copy.deepcopy(p.layout)
        self._canvas.set_layout_config(self.settings.layout)
        self._sync_layout_spinners_from_cfg()
        Toast.show(f"Đã nạp Preset: {p.name}", parent=self, variant="success")

    def _save_current_as_preset(self) -> None:
        name = self._new_preset_name.text().strip()
        if not name:
            Toast.show("Hãy nhập tên cho Preset mới trước khi lưu.", parent=self, variant="warning")
            return
        self._collect_live_settings()
        new_p = PresetProfile(
            name=name,
            comment_mode=self.settings.comment_mode,
            max_comments=self.settings.max_comments,
            voice_speed=self.settings.voice_speed,
            voice_assign_mode=self.settings.voice_assign_mode,
            selected_voices=list(self.settings.selected_voices),
            media=copy.deepcopy(self.settings.media),
            layout=copy.deepcopy(self.settings.layout),
        )
        self.settings.presets.append(new_p)
        self.settings.save()
        opts = [(p.name, i) for i, p in enumerate(self.settings.presets)]
        self._preset_select.set_options(opts)
        self._preset_select.set_value(len(opts) - 1)
        self._new_preset_name.clear_text()
        Toast.show(f"Đã lưu Preset '{name}'!", parent=self, variant="success")

    def _start_threads_login(self) -> None:
        profile_dir = self.settings.user_data_dir() / "threads_profile"
        Toast.show(
            "Đang mở trình duyệt đăng nhập Threads. Đăng nhập xong hãy đóng cửa sổ trình duyệt để lưu phiên.",
            parent=self,
            variant="info",
            duration=5500,
        )

        def _run() -> None:
            try:
                open_threads_login_session(profile_dir)
                self._bridge.login_finished.emit("Đã lưu phiên đăng nhập Threads thành công!")
            except Exception as exc:
                logger.error("Lỗi mở trình duyệt đăng nhập: %s", exc)

        self._ui_pool.submit(_run)

    def _open_output_root(self) -> None:
        out = self.settings.resolved_output_root()
        if sys.platform == "win32":
            os.startfile(str(out))  # noqa: S606
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))
