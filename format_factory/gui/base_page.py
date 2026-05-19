# format_factory/gui/base_page.py
import os
import html as _html_mod
import shlex
import re as _re
import colorsys
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QComboBox, QLabel, QFileDialog, QTextEdit, QProgressBar,
    QListWidget, QAbstractItemView, QFrame, QSizePolicy, QApplication
)
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QMimeData
from PyQt6.QtGui import QFont, QDragEnterEvent, QDropEvent, QColor, QBrush

from ..config import (
    VIDEO_FORMATS, AUDIO_FORMATS, IMAGE_FORMATS, M3U8_OUTPUT_FORMATS,
    VIDEO_INPUT_EXTENSIONS, AUDIO_INPUT_EXTENSIONS, IMAGE_INPUT_EXTENSIONS,
    DEFAULT_FFMPEG_ARGS,
)
from ..i18n import tr, resolve_language, translate_preset_label

# ── Size constants (match reference screenshot) ───────────────────
_BTN_H    = 32
_BTN_W    = 110
_BTN_W_SM = 80
_BTN_W_PRI= 130
_CTRL_H   = 32
_COMBO_W  = 140


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

from .presets import CUSTOM_ONLY, PRESETS_BY_FMT, DEFAULT_FMT
class SectionLabel(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("section_title")


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()


class CardWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

    def layout(self):      # convenience – returns inner QVBoxLayout
        return super().layout()


class AnimatedProgressBar(QProgressBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(10)
        self.setValue(0)
        self.setTextVisible(True)
        self.setFormat("总进度: %p%")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class DropFileList(QListWidget):
    """File list that also accepts drag-and-drop."""
    files_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)


# ── Custom args panel ─────────────────────────────────────────────
class ArgsPanel(QWidget):
    preset_changed = pyqtSignal()


    def __init__(self, media_type, parent=None):
        super().__init__(parent)
        self.media_type   = media_type
        self._language    = "auto"  # 由 set_language() 更新
        self._gpu_vendor  = "none"   # 由 MainWindow 通过 set_gpu_vendor() 更新
        # 优先用该媒体类型默认格式的专属预设，回退才用媒体类型级别预设
        _default_fmt = DEFAULT_FMT.get(media_type, "")
        self._cur_presets = (
            PRESETS_BY_FMT.get(_default_fmt)
            or self.PRESETS.get(media_type, self.PRESETS["video"])
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        row = QHBoxLayout(); row.setSpacing(8)
        self._preset_section_label = SectionLabel(tr(self._language, "preset_label"))
        row.addWidget(self._preset_section_label)
        self.preset_combo = NoWheelComboBox()
        self.preset_combo.setMinimumSize(_COMBO_W, _CTRL_H)
        self._fill_combo()
        self.preset_combo.currentIndexChanged.connect(self._on_preset)
        row.addWidget(self.preset_combo)
        row.addStretch()
        lay.addLayout(row)

        self.extra_edit = QLineEdit()
        self.extra_edit.setPlaceholderText(tr(self._language, "preset_extra_placeholder"))
        self.extra_edit.setMinimumHeight(_CTRL_H)
        self.extra_edit.setVisible(False)
        lay.addWidget(self.extra_edit)

    # GPU vendor → 支持的编码角色（与 settings_page.py 保持同步）
    _GPU_ROLES = {
        "nvidia": {"h264", "hevc"},
        "amd":    {"h264", "hevc"},
        "intel":  {"h264", "hevc"},
        "none":   set(),
    }
    # 预设名称 → 对应的编码角色（None = 纯 CPU / 流复制 / 音频/图片预设）
    _PRESET_ROLE = {
        # H.264 系列
        "默认 (H.264 CRF23)":       "h264",
        "高质量 (H.264 CRF18)":      "h264",
        "快速压缩 (H.264 CRF28)":    "h264",
        "平衡压缩 (H.265 CRF26)":    "hevc",
        "极速编码 (ultrafast)":       "h264",
        "H.264 + MP3":               "h264",
        "默认 (MPEG-4)":             None,    # mpeg4 编码器 GPU 不支持
        "高质量 (Q2)":               None,
        "小体积 (MPEG-4 Q7)":        None,
        # H.265/HEVC 系列
        "H.265 / HEVC (CRF28)":     "hevc",
        "H.265 高质量 (CRF22)":     "hevc",
        # AV1 — 目前主流 GPU 不支持 libaom-av1
        "AV1 (libaom, 慢速高压缩)": None,
        "极致压缩 (VP9 CRF48)":      None,
        # ProRes — GPU 不支持
        "ProRes 422 (专业剪辑)":    None,
        "ProRes 4444 (最高质量)":   None,
        # 无损 lossless — 硬件编码器不支持 CRF 0
        "无损 (H.264 lossless)":    None,
        "无损存档 (FFV1 + FLAC)":    None,
        # 复制流 / 去音频 — 不需要编码
        "仅视频流 (去除音频)":       None,
        "仅复制流 (超快无重编码)":   None,
        # VP9/WebM — GPU 不支持 libvpx-vp9
        "默认 (VP9 CRF30)":         None,
        "VP9 高质量 (CRF20)":       None,
        "VP9 快速压缩 (CRF40)":     None,
    }

    def set_gpu_vendor(self, vendor: str):
        """由 MainWindow 在 GPU 设置变更时调用，静默更新内部状态。"""
        self._gpu_vendor = vendor

    def _preset_label(self, name: str) -> str:
        return translate_preset_label(self._language, name)

    def _fill_combo(self):
        """清空并重新填充预设下拉，保持当前选中位置。"""
        cur_idx = self.preset_combo.currentIndex() \
            if hasattr(self, "preset_combo") else 0
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for name, _ in self._cur_presets:
            self.preset_combo.addItem(self._preset_label(name))
        self.preset_combo.setCurrentIndex(
            max(0, min(cur_idx, self.preset_combo.count() - 1)))
        self.preset_combo.blockSignals(False)
        self.extra_edit.setVisible(self.is_custom_override()) \
            if hasattr(self, "extra_edit") else None

    def set_preset_states(self, states: list[dict]):
        self.preset_combo.blockSignals(True)
        current_idx = self.preset_combo.currentIndex()
        self.preset_combo.clear()

        selected_idx = 0
        first_enabled = 0
        enabled_found = False

        for i, ((name, _), state) in enumerate(zip(self._cur_presets, states)):
            display_name = self._preset_label(name)
            label = f"{display_name}" + tr(self._language, "preset_recommended") if state.get("recommended") else display_name
            self.preset_combo.addItem(label)
            item = self.preset_combo.model().item(i)
            enabled = state.get("enabled", True)
            item.setEnabled(enabled)
            item.setToolTip(state.get("reason", ""))
            if not enabled:
                item.setForeground(QBrush(QColor(150, 150, 158)))
            elif state.get("recommended"):
                item.setForeground(QBrush(QColor(22, 163, 74)))
            if enabled and not enabled_found:
                first_enabled = i
                enabled_found = True

        if enabled_found:
            selected_idx = current_idx if 0 <= current_idx < len(states) and states[current_idx].get("enabled", True) else first_enabled
        elif states:
            selected_idx = min(max(current_idx, 0), len(states) - 1)
        self.preset_combo.setCurrentIndex(selected_idx)
        self.preset_combo.blockSignals(False)
        self.extra_edit.setVisible(self.is_custom_override()) \
            if hasattr(self, "extra_edit") else None

    def set_output_fmt(self, fmt: str):
        """根据输出格式切换预设列表，重置为第 0 项。"""
        new_presets = PRESETS_BY_FMT.get(fmt)
        if new_presets is None:
            new_presets = self.PRESETS.get(self.media_type, CUSTOM_ONLY)
        if new_presets is self._cur_presets:
            return
        self._cur_presets = new_presets
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for name, _ in self._cur_presets:
            self.preset_combo.addItem(self._preset_label(name))
        self.preset_combo.setCurrentIndex(0)
        self.preset_combo.blockSignals(False)
        self.extra_edit.setVisible(False)
        self.preset_changed.emit()

    def _on_preset(self, _):
        self.extra_edit.setVisible(self.is_custom_override())
        self.preset_changed.emit()

    def set_language(self, language: str):
        self._language = resolve_language(language or "auto")
        self._retranslate_ui()
        if hasattr(self, "preset_combo"):
            self._fill_combo()

    def _retranslate_ui(self):
        if hasattr(self, "_preset_section_label"):
            self._preset_section_label.setText(tr(self._language, "preset_label"))
        if hasattr(self, "extra_edit"):
            self.extra_edit.setPlaceholderText(tr(self._language, "preset_extra_placeholder"))

    def is_custom_override(self):
        idx = self.preset_combo.currentIndex()
        if idx < 0 or idx >= len(self._cur_presets):
            return False
        return self._cur_presets[idx][1] == "__custom__"

    def get_extra_args(self):
        if self.is_custom_override():
            raw = self.extra_edit.text().strip()
            try:
                return shlex.split(raw, posix=False) if raw else []
            except ValueError:
                return raw.split() if raw else []
        idx = self.preset_combo.currentIndex()
        if 0 <= idx < len(self._cur_presets):
            v = self._cur_presets[idx][1]
            return [] if v == "__custom__" else (v or [])
        return []


# ═══════════════════════════════════════════════════════════════════
#  Base converter page
# ═══════════════════════════════════════════════════════════════════
class BaseConverterPage(QWidget):
    conversion_requested     = pyqtSignal(int, str, list, str)
    cancel_conversion_signal = pyqtSignal()
    ffmpeg_download_prompt_requested = pyqtSignal()

    def __init__(self, media_type, ffmpeg_handler, parent=None):
        super().__init__(parent)
        self.media_type     = media_type
        self.ffmpeg_handler = ffmpeg_handler
        self.input_files    = []
        self.output_dir     = ""
        self._is_busy       = False
        self._replace_on_next_add = False
        self._is_dark       = False   # updated by MainWindow._apply_theme
        self.output_formats_available = self._get_fmts(media_type)
        self.file_media_info          = {}
        self._language = "auto"
        self.setAcceptDrops(True)
        self._init_ui()
        self._connect_internal_signals()
        self._update_preset_states()

    # ── helpers ──────────────────────────────
    def _get_fmts(self, mt):
        return {"video": VIDEO_FORMATS, "audio": AUDIO_FORMATS,
                "image": IMAGE_FORMATS, "m3u8": M3U8_OUTPUT_FORMATS}.get(mt, [])

    def _get_supported_input_exts(self) -> set[str]:
        return {
            "video": set(VIDEO_INPUT_EXTENSIONS),
            "audio": set(AUDIO_INPUT_EXTENSIONS),
            "image": set(IMAGE_INPUT_EXTENSIONS),
        }.get(self.media_type, set())

    def _media_label(self):
        return {
            "video": tr(self._language, "tab_video"),
            "audio": tr(self._language, "tab_audio"),
            "image": tr(self._language, "tab_image"),
            "m3u8": tr(self._language, "tab_m3u8"),
        }.get(self.media_type, tr(self._language, "tab_settings"))

    def _get_file_filter(self):
        return f"{tr(self._language, 'all_files')} (*.*)"

    def _is_supported_input_path(self, path: str) -> bool:
        if not path or not os.path.isfile(path):
            return False
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        return ext in self._get_supported_input_exts()

    def _collect_supported_drop_paths(self, paths: list[str]) -> list[str]:
        supported = []
        seen = set()
        for raw in paths or []:
            path = str(raw or "").strip()
            if not path:
                continue
            if os.path.isdir(path):
                for root, _dirs, files in os.walk(path):
                    for name in files:
                        fp = os.path.join(root, name)
                        if fp not in seen and self._is_supported_input_path(fp):
                            supported.append(fp)
                            seen.add(fp)
                continue
            if path not in seen and self._is_supported_input_path(path):
                supported.append(path)
                seen.add(path)
        return supported

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            return
        super().dragMoveEvent(e)

    def dropEvent(self, e: QDropEvent):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._on_files_dropped(paths)
            e.acceptProposedAction()
            return
        super().dropEvent(e)

    def _mk_btn(self, text, min_w=_BTN_W, obj_name=""):
        b = QPushButton(text)
        b.setMinimumSize(min_w, _BTN_H)
        b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if obj_name:
            b.setObjectName(obj_name)
        return b

    def _tr(self, key, **kwargs):
        return tr(self._language, key, **kwargs)

    def _has_ffmpeg_ready(self) -> bool:
        return bool(getattr(self.ffmpeg_handler, "ffmpeg_path", ""))

    def set_language(self, language: str):
        self._language = resolve_language(language or "auto")
        self._retranslate_ui()
        if hasattr(self, "args_panel"):
            self.args_panel.set_language(language)
        self._update_preset_states()

    def _retranslate_ui(self):
        if hasattr(self, "input_label"):
            self.input_label.setText(tr(self._language, "file_list"))
        if hasattr(self, "_file_count_lbl"):
            n = len(self.input_files)
            self._file_count_lbl.setText(
                tr(self._language, "file_count", count=n) if n else tr(self._language, "file_count_zero"))
        if hasattr(self, "select_input_files_button"):
            self.select_input_files_button.setText(tr(self._language, "add_files"))
            self.select_input_files_button.setToolTip(tr(self._language, "add_files_tip"))
        if hasattr(self, "select_input_folder_button"):
            self.select_input_folder_button.setText(tr(self._language, "add_folder"))
            self.select_input_folder_button.setToolTip(tr(self._language, "add_folder_tip"))
        if hasattr(self, "remove_selected_files_button"):
            self.remove_selected_files_button.setText(tr(self._language, "remove_selected"))
            self.remove_selected_files_button.setToolTip(tr(self._language, "remove_selected_tip"))
        if hasattr(self, "clear_file_list_button"):
            self.clear_file_list_button.setText(tr(self._language, "clear_list"))
            self.clear_file_list_button.setToolTip(tr(self._language, "clear_list_tip"))
        if hasattr(self, "output_options_label"):
            self.output_options_label.setText(tr(self._language, "output_options"))
        if hasattr(self, "format_label"):
            self.format_label.setText(tr(self._language, "format"))
        if hasattr(self, "resolution_label"):
            self.resolution_label.setText(tr(self._language, "resolution"))
        if hasattr(self, "directory_label"):
            self.directory_label.setText(tr(self._language, "directory"))
        if hasattr(self, "output_dir_edit"):
            self.output_dir_edit.setPlaceholderText(tr(self._language, "output_dir_placeholder"))
        if hasattr(self, "select_output_dir_button"):
            self.select_output_dir_button.setText(tr(self._language, "select"))
            self.select_output_dir_button.setToolTip(tr(self._language, "select_output_dir_tip"))
        if hasattr(self, "start_conversion_button"):
            self.start_conversion_button.setText(tr(self._language, "start_convert"))
        if hasattr(self, "cancel_conversion_button"):
            self.cancel_conversion_button.setText(tr(self._language, "cancel"))
        if hasattr(self, "log_section_label"):
            self.log_section_label.setText(tr(self._language, "log"))
        if hasattr(self, "_btn_copy_log"):
            self._btn_copy_log.setText(tr(self._language, "copy"))
            self._btn_copy_log.setToolTip(tr(self._language, "copy_tip"))
        if hasattr(self, "_btn_clear_log"):
            self._btn_clear_log.setText(tr(self._language, "clear"))
            self._btn_clear_log.setToolTip(tr(self._language, "clear_tip"))
        # 分辨率下拉刷新
        if hasattr(self, "resolution_combo") and self.resolution_combo is not None:
            self.resolution_combo.blockSignals(True)
            res_keys = [
                "resolution_original", "resolution_4k", "resolution_2k",
                "resolution_1080p", "resolution_720p", "resolution_480p",
                "resolution_360p", "resolution_240p",
            ]
            cur_idx = self.resolution_combo.currentIndex()
            self.resolution_combo.clear()
            for key in res_keys:
                self.resolution_combo.addItem(tr(self._language, key))
            self.resolution_combo.setCurrentIndex(min(cur_idx, len(res_keys) - 1))
            self.resolution_combo.blockSignals(False)
        # AnimatedProgressBar
        if hasattr(self, "overall_progress_bar") and self.overall_progress_bar is not None:
            self.overall_progress_bar.setFormat(tr(self._language, "progress_overall"))

    # ── UI ───────────────────────────────────
    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # ══ 上半区：左列（文件）+ 右列（选项+操作）横向分栏 ══
        body = QHBoxLayout()
        body.setSpacing(10)

        # ── 左列：文件列表卡片 ──────────────────────────────
        file_card = CardWidget()
        fl = file_card.layout()
        fl.setSpacing(8)

        file_hdr = QHBoxLayout()
        self.input_label = SectionLabel(tr(self._language, "file_list"))
        file_hdr.addWidget(self.input_label)
        file_hdr.addStretch()
        # 文件计数标签
        self._file_count_lbl = QLabel(tr(self._language, "file_count_zero"))
        self._file_count_lbl.setObjectName("section_title")
        file_hdr.addWidget(self._file_count_lbl)
        fl.addLayout(file_hdr)

        self.input_file_list_widget = DropFileList()
        self.input_file_list_widget.files_dropped.connect(self._on_files_dropped)
        self.input_file_list_widget.setMinimumHeight(120)
        fl.addWidget(self.input_file_list_widget, 1)

        br = QHBoxLayout(); br.setSpacing(6)
        self.select_input_files_button = self._mk_btn(tr(self._language, "add_files"))
        self.select_input_files_button.setToolTip(tr(self._language, "add_files_tip"))
        self.select_input_files_button.clicked.connect(self._select_files)
        self.select_input_folder_button = self._mk_btn(tr(self._language, "add_folder"))
        self.select_input_folder_button.setToolTip(tr(self._language, "add_folder_tip"))
        self.select_input_folder_button.clicked.connect(self._select_folder)
        self.remove_selected_files_button = self._mk_btn(
            tr(self._language, "remove_selected"), obj_name="danger")
        self.remove_selected_files_button.setToolTip(tr(self._language, "remove_selected_tip"))
        self.remove_selected_files_button.clicked.connect(self._remove_files)
        self.clear_file_list_button = self._mk_btn(tr(self._language, "clear_list"))
        self.clear_file_list_button.setToolTip(tr(self._language, "clear_list_tip"))
        self.clear_file_list_button.clicked.connect(self._clear_file_list)
        br.addWidget(self.select_input_files_button)
        br.addWidget(self.select_input_folder_button)
        br.addWidget(self.remove_selected_files_button)
        br.addWidget(self.clear_file_list_button)
        br.addStretch()
        fl.addLayout(br)

        body.addWidget(file_card, 5)

        # ── 右列：选项卡片 + 操作卡片（垂直堆叠）──────────────
        right_col = QVBoxLayout()
        right_col.setSpacing(10)

        # 输出选项卡片
        opt_card = CardWidget()
        ol = opt_card.layout()
        ol.setSpacing(10)
        self.output_options_label = SectionLabel(tr(self._language, "output_options"))
        ol.addWidget(self.output_options_label)

        # 格式行（视频时追加分辨率下拉）
        fmt_row = QHBoxLayout(); fmt_row.setSpacing(8)
        self.format_label = SectionLabel(tr(self._language, "format"))
        fmt_row.addWidget(self.format_label)
        self.output_format_combo = NoWheelComboBox()
        self.output_format_combo.setFixedHeight(_CTRL_H)
        self.output_format_combo.setMinimumWidth(_COMBO_W)
        self.output_format_combo.addItems(self.output_formats_available)
        self.output_format_combo.currentIndexChanged.connect(self._on_fmt_changed)
        fmt_row.addWidget(self.output_format_combo)

        if self.media_type == "video":
            self.resolution_label = SectionLabel(tr(self._language, "resolution"))
            fmt_row.addWidget(self.resolution_label)
            self.resolution_combo = NoWheelComboBox()
            self.resolution_combo.setFixedHeight(_CTRL_H)
            self.resolution_combo.setMinimumWidth(_COMBO_W)
            self.resolution_combo.addItems([
                tr(self._language, "resolution_original"),
                tr(self._language, "resolution_4k"),
                tr(self._language, "resolution_2k"),
                tr(self._language, "resolution_1080p"),
                tr(self._language, "resolution_720p"),
                tr(self._language, "resolution_480p"),
                tr(self._language, "resolution_360p"),
                tr(self._language, "resolution_240p"),
            ])
            fmt_row.addWidget(self.resolution_combo)
        else:
            self.resolution_combo = None

        fmt_row.addStretch()
        ol.addLayout(fmt_row)

        # 目录行
        dir_row = QHBoxLayout(); dir_row.setSpacing(6)
        self.directory_label = SectionLabel(tr(self._language, "directory"))
        dir_row.addWidget(self.directory_label)
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setPlaceholderText(tr(self._language, "output_dir_placeholder"))
        self.output_dir_edit.setReadOnly(True)
        self.output_dir_edit.setFixedHeight(_CTRL_H)
        self.output_dir_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        dir_row.addWidget(self.output_dir_edit, 1)
        self.select_output_dir_button = self._mk_btn(tr(self._language, "select"), _BTN_W_SM)
        self.select_output_dir_button.setToolTip(tr(self._language, "select_output_dir_tip"))
        self.select_output_dir_button.clicked.connect(self._select_dir)
        dir_row.addWidget(self.select_output_dir_button)
        ol.addLayout(dir_row)

        # 分隔线 + 参数面板
        self._hline(ol)
        self.args_panel = ArgsPanel(self.media_type)
        self.args_panel.preset_changed.connect(self._update_state)
        ol.addWidget(self.args_panel)

        right_col.addWidget(opt_card)

        # 操作卡片（开始/取消 + 进度条）
        ctrl_card = CardWidget()
        cl = ctrl_card.layout()
        cl.setSpacing(8)

        ar = QHBoxLayout(); ar.setSpacing(8)
        self.start_conversion_button = self._mk_btn(
            tr(self._language, "start_convert"), min_w=_BTN_W_PRI, obj_name="primary")
        self.start_conversion_button.setEnabled(False)
        self.start_conversion_button.clicked.connect(self._start_clicked)
        self.cancel_conversion_button = self._mk_btn(
            tr(self._language, "cancel"), min_w=_BTN_W_SM, obj_name="danger")
        self.cancel_conversion_button.setEnabled(False)
        self.cancel_conversion_button.clicked.connect(self._cancel_clicked)
        ar.addWidget(self.start_conversion_button)
        ar.addWidget(self.cancel_conversion_button)
        ar.addStretch()
        cl.addLayout(ar)

        self.overall_progress_bar = AnimatedProgressBar()
        cl.addWidget(self.overall_progress_bar)

        right_col.addWidget(ctrl_card)
        right_col.addStretch()

        body.addLayout(right_col, 4)
        root.addLayout(body, 3)

        # ══ 下半区：日志卡片（全宽）══
        log_card = CardWidget()
        ll = log_card.layout()
        ll.setSpacing(6)

        log_header = QHBoxLayout()
        self.log_section_label = SectionLabel(tr(self._language, "log"))
        log_header.addWidget(self.log_section_label)
        log_header.addStretch()

        self._btn_copy_log = QPushButton(tr(self._language, "copy"))
        self._btn_copy_log.setMinimumSize(64, 26)
        self._btn_copy_log.setMaximumHeight(26)
        self._btn_copy_log.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._btn_copy_log.setToolTip(tr(self._language, "copy_tip"))
        self._btn_copy_log.clicked.connect(self._copy_log)
        self._btn_clear_log = QPushButton(tr(self._language, "clear"))
        self._btn_clear_log.setMinimumSize(64, 26)
        self._btn_clear_log.setMaximumHeight(26)
        self._btn_clear_log.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._btn_clear_log.setToolTip(tr(self._language, "clear_tip"))
        self._btn_clear_log.clicked.connect(self._clear_log)

        log_header.addWidget(self._btn_copy_log)
        log_header.addWidget(self._btn_clear_log)
        ll.addLayout(log_header)

        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setMinimumHeight(100)
        self.log_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.log_display.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        mono = QFont("Consolas", 11)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.log_display.setFont(mono)
        ll.addWidget(self.log_display)

        self._progress_line = QLabel()
        self._progress_line.setObjectName("section_title")
        self._progress_line.setTextFormat(Qt.TextFormat.RichText)
        self._progress_line.setMinimumHeight(18)
        self._progress_line.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._progress_line.clear()
        ll.addWidget(self._progress_line)

        root.addWidget(log_card, 2)
        self._retranslate_ui()

    def _hline(self, layout):
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(f)

    # ── Signals ──────────────────────────────
    def _connect_internal_signals(self):
        if self.ffmpeg_handler:
            self.ffmpeg_handler.file_info_ready.connect(
                self.handle_file_info_ready)

    # ── File ops ─────────────────────────────
    def _select_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, tr(self._language, "dialog_select_files", type=self._media_label()), "",
            self._get_file_filter())
        if paths:
            self._clear_file_list()
            self._add_paths(paths)

    def _select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, tr(self._language, "dialog_select_folder", type=self._media_label()))
        if not folder:
            return
        # 从文件过滤器里提取支持的后缀
        exts = set()
        flt = self._get_file_filter()
        import re as _re2
        for m in _re2.finditer(r'\*\.(\w+)', flt):
            exts.add(m.group(1).lower())
        paths = []
        for fname in os.listdir(folder):
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            if ext in exts:
                paths.append(os.path.join(folder, fname))
        if paths:
            self._clear_file_list()
            self._add_paths(sorted(paths))
        else:
            self.log_message(tr(self._language, "log_no_supported_files", type=self._media_label()), "warning")

    def _clear_file_list(self):
        self.input_files.clear()
        self.file_media_info.clear()
        self.input_file_list_widget.clear()
        self.overall_progress_bar.setValue(0)
        self._update_state()
        self._update_combo()
        self._update_preset_states()
        self._update_file_count()

    def _on_files_dropped(self, paths):
        """拖入文件时，如上一轮已完成则替换当前列表，否则追加。"""
        supported = self._collect_supported_drop_paths(paths)
        if supported:
            if self._replace_on_next_add and not self._is_busy:
                self._clear_file_list()
                self._replace_on_next_add = False
            self._add_paths(supported)
        else:
            self.log_message(
                tr(self._language, "log_no_supported_files", type=self._media_label()),
                "warning",
            )

    def _update_file_count(self):
        n = len(self.input_files)
        if hasattr(self, "_file_count_lbl"):
            self._file_count_lbl.setText(
                tr(self._language, "file_count", count=n) if n else tr(self._language, "file_count_zero"))

    @staticmethod
    def _fmt_size(path):
        try:
            sz = os.path.getsize(path)
            if sz >= 1024 * 1024 * 1024:
                return f"{sz/1024/1024/1024:.2f} GB"
            if sz >= 1024 * 1024:
                return f"{sz/1024/1024:.1f} MB"
            if sz >= 1024:
                return f"{sz/1024:.0f} KB"
            return f"{sz} B"
        except OSError:
            return "?"

    def _add_paths(self, paths):
        if self._replace_on_next_add and not self._is_busy:
            self._clear_file_list()
            self._replace_on_next_add = False
        existing = set(self.input_files)
        added = 0
        for p in paths:
            if p not in existing:
                self.input_files.append(p)
                existing.add(p)
                sz = self._fmt_size(p)
                label = f"{os.path.basename(p)}  [{sz}]"
                self.input_file_list_widget.addItem(label)
                if self.ffmpeg_handler:
                    self.ffmpeg_handler.get_file_info_ffprobe(p)
                added += 1
        if added:
            self.log_message(tr(self._language, "log_added_files", count=added), "info")
            self._update_state()
            self._update_combo()
            self._update_preset_states()
            self._update_file_count()
        else:
            self.log_message(tr(self._language, "log_files_already_in_list"), "warning")

    def _remove_files(self):
        sel = self.input_file_list_widget.selectedItems()
        if not sel:
            self.log_message(tr(self._language, "log_select_files_to_remove"), "warning")
            return
        for item in sel:
            row = self.input_file_list_widget.row(item)
            if row < len(self.input_files):
                fp = self.input_files.pop(row)
                self.file_media_info.pop(fp, None)
            self.input_file_list_widget.takeItem(row)
        self.overall_progress_bar.setValue(0)
        self._update_state()
        self._update_combo()
        self._update_preset_states()
        self._update_file_count()

    def _select_dir(self):
        d = QFileDialog.getExistingDirectory(self, tr(self._language, "dialog_select_output_dir"))
        if d:
            self.output_dir = d
            self.output_dir_edit.setText(d)
            self._update_state()

    # ── Conversion ───────────────────────────
    def _on_fmt_changed(self, _):
        fmt = self.output_format_combo.currentText()
        self.args_panel.set_output_fmt(fmt)
        self._update_state()
        self._update_preset_states()

    def _start_clicked(self):
        if not self._has_ffmpeg_ready():
            self.ffmpeg_download_prompt_requested.emit()
            return
        if not self.input_files:
            self.log_message(tr(self._language, "log_add_input_files"), "error"); return
        invalid_paths = [p for p in self.input_files if not os.path.isfile(p)]
        if invalid_paths:
            self.log_message(
                tr(self._language, "log_invalid_input_paths", count=len(invalid_paths)),
                "error",
            )
            for bad_path in invalid_paths[:3]:
                self.log_message(tr(self._language, "log_invalid_input_path_item", path=bad_path), "warning")
            if len(invalid_paths) > 3:
                self.log_message(
                    tr(self._language, "log_invalid_input_paths_more", count=len(invalid_paths) - 3),
                    "warning",
                )
            self._clear_file_list()
            return
        if not self.output_dir:
            self.log_message(tr(self._language, "log_select_output_dir"), "error"); return
        self._clear_log()
        self._progress_line.clear()
        self.log_message(tr(self._language, "log_start_conversion", count=len(self.input_files)), "info")
        self.start_conversion_button.setEnabled(False)
        self.cancel_conversion_button.setEnabled(True)
        self.overall_progress_bar.setValue(0)
        self._start_conversion_process()

    def _cancel_clicked(self):
        self.log_message(tr(self._language, "log_requesting_cancel"), "warning")
        self.cancel_conversion_signal.emit()

    def _start_conversion_process(self):
        pass   # override in subclass

    def _build_args(self, fmt: str) -> list:
        default = DEFAULT_FFMPEG_ARGS.get(
            self.media_type, {}).get(fmt, [])
        extra = self.args_panel.get_extra_args()
        args = extra if self.args_panel.is_custom_override() \
               else default + extra

        # 分辨率注入（仅视频页有 resolution_combo）
        res_combo = getattr(self, "resolution_combo", None)
        if res_combo is not None:
            idx = res_combo.currentIndex()
            _RES_MAP = {
                1: "3840:2160",
                2: "2560:1440",
                3: "1920:1080",
                4: "1280:720",
                5: "854:480",
                6: "640:360",
                7: "426:240",
            }
            scale = _RES_MAP.get(idx)
            copy_video = False
            for pos, tok in enumerate(args):
                if tok == "-c" and pos + 1 < len(args) and args[pos + 1] == "copy":
                    copy_video = True
                    break
                if tok in ("-c:v", "-vcodec") and pos + 1 < len(args) and args[pos + 1] == "copy":
                    copy_video = True
                    break
            if scale and fmt != "m3u8" and not copy_video and "-vf" not in args:
                args = args + ["-vf", f"scale={scale}"]

        if self.media_type == "audio":
            args = self._sanitize_audio_args(fmt, args)

        return args

    @staticmethod
    def _extract_ico_scale_from_args(args: list) -> int | None:
        for idx, tok in enumerate(args[:-1]):
            if tok != "-vf":
                continue
            vf = str(args[idx + 1] or "")
            match = _re.search(r"scale=(\d+):(\d+)", vf)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    return None
        return None

    @staticmethod
    def _replace_or_append_arg(args: list, option: str, value: str) -> list:
        fixed = list(args)
        for i, tok in enumerate(fixed[:-1]):
            if tok == option:
                fixed[i + 1] = value
                return fixed
        fixed.extend([option, value])
        return fixed

    def _sanitize_audio_args(self, fmt: str, args: list) -> list:
        fixed = list(args)

        audio_codec = ""
        for i, tok in enumerate(fixed[:-1]):
            if tok == "-c:a":
                audio_codec = fixed[i + 1].lower()

        if fmt == "m4a":
            if audio_codec in {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"}:
                fixed = self._replace_or_append_arg(fixed, "-c:a", "aac")
                if "-b:a" not in fixed:
                    fixed.extend(["-b:a", "192k"])
            if "-vn" in fixed:
                fixed = [tok for tok in fixed if tok != "-vn"]
            if "-map" not in fixed:
                fixed = ["-map", "0:a", "-map", "0:v?"] + fixed
            has_video_codec = any(tok in {"-c:v", "-vcodec"} for tok in fixed)
            if not has_video_codec:
                fixed.extend(["-c:v", "copy"])
        elif fmt == "aac":
            if audio_codec in {"alac", "flac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"}:
                fixed = self._replace_or_append_arg(fixed, "-c:a", "aac")
            if "-vn" not in fixed:
                fixed.append("-vn")
        elif fmt == "wav":
            if audio_codec in {"aac", "alac", "flac", "libmp3lame", "libopus", "libvorbis"}:
                fixed = self._replace_or_append_arg(fixed, "-c:a", "pcm_s16le")
            if "-vn" not in fixed:
                fixed.append("-vn")

        return fixed

    def _collect_media_capabilities(self) -> dict:
        caps = {
            "has_video": False,
            "has_audio": False,
            "video_codecs": set(),
            "audio_codecs": set(),
            "widths": [],
            "heights": [],
            "sample_rates": [],
            "audio_bit_rates": [],
            "video_bit_rates": [],
            "extensions": set(),
            "analyzed_count": 0,
            "total_count": len(self.input_files),
        }
        for fp in self.input_files:
            caps["extensions"].add(os.path.splitext(fp)[1].lstrip(".").lower())
            info = self.file_media_info.get(fp, {})
            if info:
                caps["analyzed_count"] += 1
            for stream in info.get("streams", []):
                codec_type = stream.get("codec_type")
                codec_name = (stream.get("codec_name") or "").lower()
                if codec_type == "video":
                    caps["has_video"] = True
                    if codec_name:
                        caps["video_codecs"].add(codec_name)
                    width = stream.get("width")
                    if isinstance(width, int):
                        caps["widths"].append(width)
                    height = stream.get("height")
                    if isinstance(height, int):
                        caps["heights"].append(height)
                    bit_rate = stream.get("bit_rate")
                    if isinstance(bit_rate, (int, float)):
                        caps["video_bit_rates"].append(int(bit_rate))
                elif codec_type == "audio":
                    caps["has_audio"] = True
                    if codec_name:
                        caps["audio_codecs"].add(codec_name)
                    sample_rate = stream.get("sample_rate")
                    if isinstance(sample_rate, (int, float)):
                        caps["sample_rates"].append(int(sample_rate))
                    bit_rate = stream.get("bit_rate")
                    if isinstance(bit_rate, (int, float)):
                        caps["audio_bit_rates"].append(int(bit_rate))
        return caps

    def _preset_enabled(self, name: str, caps: dict = None, output_fmt: str = "") -> bool:
        if caps is None:
            return True
        role = ArgsPanel._PRESET_ROLE.get(name)
        if role is not None:
            return True
        has_video = caps.get("has_video", False)
        has_audio = caps.get("has_audio", False)
        video_codecs = caps.get("video_codecs", set())
        audio_codecs = caps.get("audio_codecs", set())
        if name == "仅视频流 (去除音频)":
            return has_video and not has_audio
        if name == "仅复制流 (超快无重编码)":
            return True
        if name == "H.264 + MP3":
            return True
        if name == "无损存档 (FFV1 + FLAC)":
            return has_video and has_audio
        if name == "AV1 (libaom, 慢速高压缩)":
            return has_video
        if name == "极致压缩 (VP9 CRF48)":
            return has_video
        if name == "ProRes 422 (专业剪辑)":
            return has_video
        if name == "ProRes 4444 (最高质量)":
            return has_video
        if name == "无损 (H.264 lossless)":
            return has_video
        if name.startswith("VP9"):
            return has_video
        if name.startswith("H.265"):
            return has_video
        return True

    def _update_preset_states(self):
        if not hasattr(self, "args_panel"):
            return
        caps = None
        try:
            caps = self._collect_media_capabilities()
        except Exception:
            pass
        fmt = ""
        try:
            fmt = self.output_format_combo.currentText()
        except Exception:
            pass
        gpu_vendor = getattr(self.args_panel, "_gpu_vendor", "none")
        gpu_roles = ArgsPanel._GPU_ROLES.get(gpu_vendor, set())
        states = []
        for name, _ in self.args_panel._cur_presets:
            enabled = self._preset_enabled(name, caps, fmt)
            reason = ""
            role = ArgsPanel._PRESET_ROLE.get(name)
            if not enabled:
                reason = "当前媒体文件不支持此预设"
            elif role is not None and role not in gpu_roles and gpu_vendor != "none":
                reason = f"当前 GPU ({gpu_vendor}) 不支持此编码，将使用 CPU 编码"
            states.append({
                "enabled": enabled,
                "reason": reason,
                "recommended": (name == "默认 (H.264 CRF23)" and role == "h264"),
            })
        self.args_panel.set_preset_states(states)

    def _update_combo(self):
        pass   # optional override

    def _update_state(self):
        fmt = self.output_format_combo.currentText()
        can_start = bool(self.input_files and self.output_dir) and not self._is_busy
        self.start_conversion_button.setEnabled(can_start)
        if self._has_ffmpeg_ready():
            self.start_conversion_button.setToolTip("")
        else:
            self.start_conversion_button.setToolTip(tr(self._language, "ffmpeg_not_found_download"))

    def log_message(self, message, level="info"):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "ℹ", "warning": "⚠", "error": "❌", "success": "✅",
                  "cmd": ">", "meta": "M", "encoder": "E"}.get(level, "ℹ")
        safe = _html_mod.escape(str(message or ""))
        bg_hue = self._bg_colors.get("avg_hue") if isinstance(self._bg_colors, dict) else None
        bg_level = float(self._bg_colors.get("dominant_bg_level", 0.0)) if isinstance(self._bg_colors, dict) else 0.0
        text_color = self._kind_color(level, self._is_dark, bg_hue, bg_level)
        ts_color = "#A1A1AA" if self._is_dark else "#6B7280"
        weight = "700" if level in {"error", "warning", "success", "cmd", "meta", "encoder"} else "600"
        row = (
            f'<p style="margin:1px 0;">'
            f'<span style="color:{ts_color};font-size:10px">[{ts}]</span>&nbsp;'
            f'<span style="color:{text_color};font-weight:{weight}">{prefix}&nbsp;{safe}</span>'
            f'</p>'
        )
        self.log_display.append(row)
        self.log_display.ensureCursorVisible()

    def _copy_log(self):
        text = self.log_display.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.log_message(tr(self._language, "copied"), "info")

    def _clear_log(self):
        self.log_display.clear()

    def log_ffmpeg_line(self, _idx: int, kind: str, text: str):
        if kind == "progress":
            self._update_progress_line(text, "info")
            return
        mapped = {
            "warn": "warning",
            "progress": "info",
        }.get(kind, kind)
        self.log_message(text, mapped)

    def update_overall_progress(self, idx: int, total: int, pct: int):
        if not hasattr(self, "overall_progress_bar") or self.overall_progress_bar is None:
            return
        if total <= 0:
            self.overall_progress_bar.setValue(max(0, min(100, int(pct))))
            self.overall_progress_bar.setFormat(tr(self._language, "progress_overall"))
            return
        per_file = 100 / total
        value = int(idx * per_file + max(0, min(100, int(pct))) / 100 * per_file)
        self.overall_progress_bar.setValue(max(0, min(100, value)))
        self.overall_progress_bar.setFormat(
            tr(self._language, "progress_batch_format", done=min(idx + 1, total), total=total, percent="%p")
        )

    def set_progress_state(self, text, level="warning"):
        if hasattr(self, "_progress_line"):
            color = {"warning": "#B45309", "error": "#DC2626",
                     "success": "#16A34A", "info": "#2563EB",
                     "cmd": "#0F766E", "meta": "#7C3AED"}.get(level, "#2563EB")
            self._progress_line.setText(
                f'<span style="color:{color}; font-weight:600;">{text}</span>')

    def handle_file_info_ready(self, file_path, info):
        self.file_media_info[file_path] = info

    def set_theme(self, mode: str, bg_colors: dict = None):
        self._is_dark = (mode == "dark")
        self._bg_colors = bg_colors or {}

    def _apply_theme(self, is_dark: bool):
        self._is_dark = is_dark

    def _update_progress_line(self, text: str, level="info"):
        self.set_progress_state(text, level)

    # ── Dynamic theme color helpers ──────────────────────────────────

    _KIND_OFFSETS = {
        "info":    (0.00, "ℹ"),   # 同 info
        "success": (0.33, "✅"),  # 绿色
        "warning": (0.16, "⚠"),  # 橙色
        "error":   (0.00, "❌"),  # 同 error
    }
    # Dark mode fallback: soft pastels, readable on dark background
    _KIND_DARK = {
        "info":    "#60A5FA",   # 蓝色 400
        "success": "#4ADE80",   # 绿色 400
        "warning": "#FBBF24",   # 琥珀 400
        "error":   "#F87171",   # 红色 400
        "cmd":     "#A78BFA",   # 紫色 400
        "meta":    "#F472B6",   # 粉 400
        "encoder": "#FDE68A",   # 黄 200
    }
    # Light mode: rich, muted tones readable on light background
    _KIND_LIGHT = {
        "info":    "#2563EB",   # 蓝色 600
        "success": "#16A34A",   # 绿色 600
        "warning": "#B45309",   # 琥珀 700
        "error":   "#DC2626",   # 红色 600
        "cmd":     "#0F766E",   # 青色 700
        "meta":    "#7C3AED",   # 紫色 600
        "encoder": "#D97706",   # 琥珀 600
    }

    @classmethod
    def _kind_color(cls, kind: str, is_dark: bool, bg_hue: float | None = None, dominant_bg_level: float = 0.0) -> str:
        """
        动态生成日志彩色标签颜色。

        列表有图片时按暗色/亮色逻辑计算：
          · 以图片平均色的互补色为基础，自动偏移色相，
          · 暗色模式用高饱和度中亮度，亮色模式用中饱和度高亮度。

        无背景图片时用预设固定色板（_KIND_LIGHT / _KIND_DARK）。
        """
        # 无背景图片时用预设固定色板
        if bg_hue is None or dominant_bg_level < 0.1:
            if is_dark:
                return cls._KIND_DARK.get(kind, "#94A3B8")
            return cls._KIND_LIGHT.get(kind, "#6B7280")

        # 列表有图片时按平均色动态计算互补色系
        # 互补色 hue = dom_hue + 0.5
        comp = (bg_hue + 0.5) % 1.0
        offset, icon = cls._KIND_OFFSETS.get(kind, (0.0, "ℹ"))
        hue = (comp + offset) % 1.0

        if is_dark:
            # 暗色模式：饱和度中、亮度高 → 醒目但不刺眼
            sat, val = 0.70, 0.95
        else:
            # 亮色模式：饱和度高、亮度中等 → 有质感不轻浮
            sat, val = 0.88, 0.72

        r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
        return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

    # ══════════════════════════════════════════════════════════════
    #  Presets (override in sub-pages or use these defaults)
    # ══════════════════════════════════════════════════════════════
    PRESETS = {
        "video": CUSTOM_ONLY,
        "audio": CUSTOM_ONLY,
        "image": CUSTOM_ONLY,
        "m3u8": CUSTOM_ONLY,
    }
