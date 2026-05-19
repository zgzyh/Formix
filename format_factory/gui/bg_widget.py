# format_factory/gui/bg_widget.py
"""
背景 Widget 与图片色彩分析。

- analyze_image_colors: 纯 Qt 图片平均颜色分析（无 PIL 依赖）
- BackgroundWidget: 三层离屏绘制（清晰图 → 高斯模糊 → 遮罩）
"""
import os
import colorsys

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QPainter, QColor, QImage


# ══════════════════════════════════════════════════════════════════
#  Average-color analyser  (pure Qt, no PIL)
# ══════════════════════════════════════════════════════════════════
def analyze_image_colors(path: str) -> dict:
    """
    计算图片的平均颜色（取代原来的主色调计算）。
    返回平均颜色的色相、RGB，以及其互补色。
    """
    empty = {"is_dark": False, "complement_hex": "",
             "accent_hex": "", "avg_hue": -1.0,
             "avg_r": 128, "avg_g": 128, "avg_b": 128}
    if not path or not os.path.isfile(path):
        return empty

    img = QImage(path)
    if img.isNull():
        return empty

    small = img.scaled(60, 60,
                       Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)

    rt = gt = bt = n = 0

    for y in range(small.height()):
        for x in range(small.width()):
            c = QColor(small.pixel(x, y))
            rt += c.red(); gt += c.green(); bt += c.blue()
            n += 1

    if n == 0:
        return empty

    ar, ag, ab_ = rt//n, gt//n, bt//n
    bright   = (ar*299 + ag*587 + ab_*114) / 1000
    is_dark  = bright < 128

    # 计算平均颜色的 HSV
    h, s, _ = colorsys.rgb_to_hsv(ar/255, ag/255, ab_/255)

    # 如果饱和度极低（黑白灰图片），色相无效
    avg_hue = h if s > 0.05 else -1.0

    # 计算互补色（色相相差 180 度即 0.5）
    comp_hue = (h + 0.5) % 1.0

    if is_dark:
        cc = QColor.fromHsvF(comp_hue, 0.55, 0.94)
        ac = QColor.fromHsvF((comp_hue+0.08)%1.0, 0.75, 0.98)
    else:
        cc = QColor.fromHsvF(comp_hue, 0.65, 0.38)
        ac = QColor.fromHsvF((comp_hue+0.08)%1.0, 0.80, 0.50)

    return {"is_dark": is_dark,
            "complement_hex": cc.name(),
            "accent_hex": ac.name(),
            "avg_hue": avg_hue,
            "avg_bright": bright,
            "avg_r": ar, "avg_g": ag, "avg_b": ab_}


# ══════════════════════════════════════════════════════════════════
#  Background widget
# ══════════════════════════════════════════════════════════════════
class BackgroundWidget(QWidget):
    """
    用 paintEvent 直接绘制三层，彻底解决 QLabel 叠加时 alpha 不穿透的问题。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap     = None
        self._bg_cache   = None
        self._blur_cache = None
        self._fill_mode  = "cover"
        self._dark          = False
        self._blur_r        = 0
        self._bg_opacity     = 50
        self._mask_alpha     = 26
        self._avg_bright    = 128
        self._avg_r = self._avg_g = self._avg_b = 128
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    def set_image(self, path: str):
        self._pixmap = QPixmap(path) if (path and os.path.isfile(path)) else None
        self._bg_cache = None
        self._blur_cache = None
        self.update()

    def set_fill_mode(self, mode: str):
        mode = mode if mode in {"stretch", "none", "fit", "cover"} else "cover"
        if mode != self._fill_mode:
            self._fill_mode = mode
            self._bg_cache = None
            self._blur_cache = None
        self.update()

    def set_blur(self, level: int):
        new_r = max(0, int(level))
        if new_r != self._blur_r:
            self._blur_r = new_r
            self._blur_cache = None
        self.update()

    def set_bg_opacity(self, pct: int):
        self._bg_opacity = max(0, min(100, int(pct)))
        self.update()

    def set_dark(self, dark: bool):
        self._dark = dark
        self.update()

    def set_mask_alpha(self, alpha: int):
        self._mask_alpha = max(0, min(255, int(alpha)))
        self.update()

    def set_mask_color(self, dark: bool):
        self._dark = dark
        self.update()

    def set_bg_colors(self, colors: dict):
        self._avg_bright = colors.get("avg_bright", 128)
        self._avg_r = colors.get("avg_r", 128)
        self._avg_g = colors.get("avg_g", 128)
        self._avg_b = colors.get("avg_b", 128)
        self.update()

    @staticmethod
    def _offscreen_blur(src: "QPixmap", sigma: float) -> "QPixmap":
        from PyQt6.QtWidgets import QGraphicsScene, QGraphicsPixmapItem, QGraphicsBlurEffect
        from PyQt6.QtCore import QRectF
        w, h = src.width(), src.height()
        pad = int(sigma * 3) + 2
        pw, ph = w + pad * 2, h + pad * 2
        scene = QGraphicsScene()
        scene.setSceneRect(QRectF(0, 0, pw, ph))
        item = QGraphicsPixmapItem(src)
        item.setPos(pad, pad)
        scene.addItem(item)
        fx = QGraphicsBlurEffect()
        fx.setBlurRadius(sigma)
        fx.setBlurHints(QGraphicsBlurEffect.BlurHint.QualityHint)
        item.setGraphicsEffect(fx)
        padded = QPixmap(pw, ph)
        padded.fill(Qt.GlobalColor.transparent)
        p = QPainter(padded)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        scene.render(p)
        p.end()
        return padded.copy(pad, pad, w, h)

    def _bg_alpha(self) -> float:
        return max(0.0, min(1.0, self._bg_opacity / 100.0))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._bg_cache = None
        self._blur_cache = None
        self.update()

    def _render_base_pixmap(self, w: int, h: int) -> QPixmap:
        if not self._pixmap:
            return QPixmap()
        pix = QPixmap(w, h)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._fill_mode == "stretch":
            scaled = self._pixmap.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap(0, 0, scaled)
        elif self._fill_mode == "none":
            x = (w - self._pixmap.width()) // 2
            y = (h - self._pixmap.height()) // 2
            p.drawPixmap(x, y, self._pixmap)
        elif self._fill_mode == "fit":
            scaled = self._pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            x = (w - scaled.width()) // 2
            y = (h - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)
        else:
            scaled = self._pixmap.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            x = (scaled.width() - w) // 2
            y = (scaled.height() - h) // 2
            p.drawPixmap(-x, -y, scaled)
        p.end()
        return pix

    def paintEvent(self, e):
        if not self._pixmap:
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bg_key = (w, h, self._fill_mode)
        if self._bg_cache is None or self._bg_cache[0] != bg_key:
            pix = self._render_base_pixmap(w, h)
            self._bg_cache = (bg_key, pix)
            self._blur_cache = None
        bg_alpha = self._bg_alpha()
        if bg_alpha > 0:
            painter.setOpacity(bg_alpha)
            painter.drawPixmap(0, 0, self._bg_cache[1])
            painter.setOpacity(1.0)
        if self._blur_r > 0 and bg_alpha > 0:
            blur_key = (w, h, self._blur_r, self._fill_mode)
            if self._blur_cache is None or self._blur_cache[0] != blur_key:
                blurred = self._offscreen_blur(self._bg_cache[1], sigma=float(self._blur_r))
                self._blur_cache = (blur_key, blurred)
            painter.setOpacity(bg_alpha)
            painter.drawPixmap(0, 0, self._blur_cache[1])
            painter.setOpacity(1.0)
        mask_color = QColor(0, 0, 0, self._mask_alpha) if self._dark else QColor(255, 255, 255, self._mask_alpha)
        painter.fillRect(0, 0, w, h, mask_color)
        painter.end()
