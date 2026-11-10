"""Interactive 9:16 Stage Canvas built with Fluent Blue tokens and ThemedMixin.

Supports direct mouse dragging and wheel-scaling of both the Threads Comment Card
and the Secondary B-roll Video region, with zero hardcoded stylesheets.
"""
from __future__ import annotations

import math

from fluent_blue import ThemeManager
from fluent_blue.compatibility.qt import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPointF,
    QRectF,
    QSizePolicy,
    Qt,
    QtCore,
    QWheelEvent,
    QWidget,
)
from fluent_blue.components.base import ThemedMixin

from ...core.models import LayoutConfig, SegmentItem
from ...render.card_renderer import render_threads_card_qimage

Signal = getattr(QtCore, "pyqtSignal", None) or getattr(QtCore, "Signal")


class InteractiveStageCanvas(ThemedMixin, QWidget):
    """Live 9:16 WYSIWYG preview stage with mouse drag & wheel scale."""

    layout_edited = Signal()
    layer_selected = Signal(str)  # "card" | "sub"

    def __init__(self, layout_cfg: LayoutConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout_cfg = layout_cfg
        self._segment = SegmentItem(
            seg_id="preview",
            is_root_post=True,
            username="yumemikawa",
            raw_text=(
                'Có ai thích từ “nức nở” trong tiếng Việt giống mình không. '
                "Đối với mình đây là từ mình thích nhất."
            ),
            likes=7800,
            replies=223,
            reposts=866,
        )
        self._card_cache: QImage | None = None
        self._selected_layer: str = "card"
        self._dragging_layer: str | None = None
        self._drag_offset = QPointF(0.0, 0.0)

        self.setMinimumSize(250, 410)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.bind_theme()
        self._rebuild_card_image()

    def refresh_theme(self) -> None:
        self.update()

    def set_layout_config(self, cfg: LayoutConfig) -> None:
        self._layout_cfg = cfg
        self._rebuild_card_image()
        self.update()

    def set_preview_segment(self, seg: SegmentItem) -> None:
        self._segment = seg
        self._rebuild_card_image()
        self.update()

    def refresh_card(self) -> None:
        self._rebuild_card_image()
        self.update()

    def export_snapshot(self, out_png: str) -> None:
        stage_img = QImage(540, 960, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(stage_img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self._paint_stage_contents(painter, QRectF(0, 0, 540, 960), draw_handles=False)
        painter.end()
        stage_img.save(out_png, "PNG")

    def _rebuild_card_image(self) -> None:
        self._card_cache = render_threads_card_qimage(self._segment, self._layout_cfg, base_width=920)

    def _stage_rect(self) -> QRectF:
        pad = float(ThemeManager.spacing(2))
        avail_w = max(80.0, self.width() - pad * 2)
        avail_h = max(140.0, self.height() - pad * 2)
        if avail_w / avail_h > 9.0 / 16.0:
            sh = avail_h
            sw = sh * (9.0 / 16.0)
        else:
            sw = avail_w
            sh = sw * (16.0 / 9.0)
        sx = (self.width() - sw) * 0.5
        sy = (self.height() - sh) * 0.5
        return QRectF(sx, sy, sw, sh)

    def _card_rect_in_stage(self, stage: QRectF) -> QRectF:
        if self._card_cache is None or self._card_cache.width() == 0:
            return QRectF()
        rel_w = (880.0 / 1080.0) * (self._layout_cfg.card_scale_pct / 100.0)
        draw_w = stage.width() * rel_w
        aspect = self._card_cache.height() / float(self._card_cache.width())
        draw_h = draw_w * aspect
        cx = stage.left() + stage.width() * (self._layout_cfg.card_x_pct / 100.0)
        cy = stage.top() + stage.height() * (self._layout_cfg.card_y_pct / 100.0)
        return QRectF(cx - draw_w * 0.5, cy - draw_h * 0.5, draw_w, draw_h)

    def _sub_rect_in_stage(self, stage: QRectF) -> QRectF:
        draw_w = stage.width() * (self._layout_cfg.sub_w_pct / 100.0)
        draw_h = stage.height() * (self._layout_cfg.sub_h_pct / 100.0)
        cx = stage.left() + stage.width() * (self._layout_cfg.sub_x_pct / 100.0)
        cy = stage.top() + stage.height() * (self._layout_cfg.sub_y_pct / 100.0)
        return QRectF(cx - draw_w * 0.5, cy - draw_h * 0.5, draw_w, draw_h)

    # ---- Painting ----

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # Outer studio backdrop using semantic token
        well_rect = QRectF(0, 0, self.width(), self.height())
        well_path = QPainterPath()
        well_path.addRoundedRect(well_rect, float(ThemeManager.radius("lg")), float(ThemeManager.radius("lg")))
        painter.fillPath(well_path, ThemeManager.qcolor("surface-secondary"))

        stage = self._stage_rect()
        self._paint_stage_contents(painter, stage, draw_handles=True)
        painter.end()

    def _paint_stage_contents(self, painter: QPainter, stage: QRectF, *, draw_handles: bool) -> None:
        r = float(ThemeManager.radius("md"))
        clip = QPainterPath()
        clip.addRoundedRect(stage, r, r)

        painter.save()
        painter.setClipPath(clip)

        # 1. 3D perspective wireframe background matching Video 1
        painter.fillRect(stage, QColor("#050507"))
        self._draw_perspective_grid(painter, stage)

        # 2. Secondary B-roll video placeholder
        if self._layout_cfg.sub_enabled:
            sub_r = self._sub_rect_in_stage(stage)
            sub_rad = (self._layout_cfg.sub_radius_px / 1080.0) * stage.width()
            sub_path = QPainterPath()
            sub_path.addRoundedRect(sub_r, sub_rad, sub_rad)
            grad = QLinearGradient(sub_r.topLeft(), sub_r.bottomRight())
            grad.setColorAt(0.0, QColor("#172554"))
            grad.setColorAt(1.0, QColor("#1E1B4B"))
            painter.fillPath(sub_path, QBrush(grad))

            painter.setFont(ThemeManager.font("caption"))
            painter.setPen(QColor("#E2E8F0"))
            painter.drawText(
                sub_r.adjusted(8, 4, -8, -4),
                int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                "VIDEO PHỤ (MEME / ANIME)\nKéo thả · Lăn chuột đổi cỡ",
            )

        # 3. Rendered Threads Card (smooth-scaled to exact physical target pixels for crisp text)
        if self._card_cache is not None:
            card_r = self._card_rect_in_stage(stage)
            dpr = max(1.0, self.devicePixelRatioF())
            tw = max(1, int(round(card_r.width() * dpr)))
            th = max(1, int(round(card_r.height() * dpr)))
            sharp = self._card_cache.scaled(
                tw,
                th,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawImage(card_r, sharp)

        # 4. Interactive selection handles
        if draw_handles:
            accent = ThemeManager.qcolor("primary")
            pen = QPen(accent, 1.8, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            if self._selected_layer == "card" and self._card_cache is not None:
                painter.drawRect(self._card_rect_in_stage(stage))
            elif self._selected_layer == "sub" and self._layout_cfg.sub_enabled:
                painter.drawRect(self._sub_rect_in_stage(stage))

        painter.restore()

        # Crisp stage frame border
        painter.setPen(QPen(ThemeManager.qcolor("border"), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(clip)

    def _draw_perspective_grid(self, painter: QPainter, stage: QRectF) -> None:
        """Fast vector rendition of Video 1's 3D black-and-white grid tunnel."""
        w, h = stage.width(), stage.height()
        ox, oy = stage.left(), stage.top()
        fov = w * 0.78
        cols = [-3.6, -2.4, -1.2, 0.0, 1.2, 2.4, 3.6]
        zs = [0.85 + i * 0.72 for i in range(12)]

        for sign in (-1.0, 1.0):
            pts: list[list[QPointF]] = []
            for z in zs:
                row: list[QPointF] = []
                for x in cols:
                    curve = 0.12 * (x * x) * (-sign)
                    y = sign * (1.85 + curve)
                    px = ox + w * 0.5 + (x / z) * fov
                    py = oy + h * 0.5 + (y / z) * fov
                    row.append(QPointF(px, py))
                pts.append(row)

            for zi, row in enumerate(pts):
                alpha = max(35, int(210 * (1.0 - zi / 12.5)))
                c = QColor(245, 245, 250, alpha)
                painter.setPen(QPen(c, 1.0))
                for xi in range(len(cols) - 1):
                    painter.drawLine(row[xi], row[xi + 1])
                if zi + 1 < len(pts):
                    for xi in range(len(cols)):
                        painter.drawLine(row[xi], pts[zi + 1][xi])
                painter.setBrush(QBrush(c))
                for p in row:
                    painter.drawEllipse(p, 1.7, 1.7)

    # ---- Mouse direct manipulation ----

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        stage = self._stage_rect()
        card_r = self._card_rect_in_stage(stage)
        sub_r = self._sub_rect_in_stage(stage) if self._layout_cfg.sub_enabled else QRectF()

        if card_r.contains(pos):
            self._selected_layer = "card"
            self._dragging_layer = "card"
            self._drag_offset = pos - card_r.center()
            self.layer_selected.emit("card")
            self.update()
        elif self._layout_cfg.sub_enabled and sub_r.contains(pos):
            self._selected_layer = "sub"
            self._dragging_layer = "sub"
            self._drag_offset = pos - sub_r.center()
            self.layer_selected.emit("sub")
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position()
        stage = self._stage_rect()
        if self._dragging_layer and stage.width() > 10 and stage.height() > 10:
            new_center = pos - self._drag_offset
            nx = max(5.0, min(95.0, ((new_center.x() - stage.left()) / stage.width()) * 100.0))
            ny = max(5.0, min(95.0, ((new_center.y() - stage.top()) / stage.height()) * 100.0))
            if self._dragging_layer == "card":
                self._layout_cfg.card_x_pct = round(nx, 1)
                self._layout_cfg.card_y_pct = round(ny, 1)
            elif self._dragging_layer == "sub":
                self._layout_cfg.sub_x_pct = round(nx, 1)
                self._layout_cfg.sub_y_pct = round(ny, 1)
            self.layout_edited.emit()
            self.update()
            return

        card_r = self._card_rect_in_stage(stage)
        sub_r = self._sub_rect_in_stage(stage) if self._layout_cfg.sub_enabled else QRectF()
        if card_r.contains(pos) or (self._layout_cfg.sub_enabled and sub_r.contains(pos)):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:  # noqa: N802
        self._dragging_layer = None

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            return
        step = 3.0 if delta > 0 else -3.0
        pos = event.position()
        stage = self._stage_rect()
        if self._layout_cfg.sub_enabled and self._sub_rect_in_stage(stage).contains(pos):
            self._selected_layer = "sub"
            self._layout_cfg.sub_w_pct = round(max(25.0, min(100.0, self._layout_cfg.sub_w_pct + step)), 1)
            self._layout_cfg.sub_h_pct = round(max(12.0, min(65.0, self._layout_cfg.sub_h_pct + step * 0.5)), 1)
            self.layer_selected.emit("sub")
        else:
            self._selected_layer = "card"
            self._layout_cfg.card_scale_pct = round(
                max(55.0, min(122.0, self._layout_cfg.card_scale_pct + step)), 1
            )
            self.layer_selected.emit("card")
        self.layout_edited.emit()
        self.update()
