# format_factory/gui/m3u8_downloader.py
import os
from PyQt6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QFrame, QComboBox, QSizePolicy, QApplication, QTextEdit
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont

from .base_page import (BaseConverterPage, CardWidget, SectionLabel,
                        DropFileList, AnimatedProgressBar, ArgsPanel,
                        _BTN_H, _BTN_W, _BTN_W_SM, _BTN_W_PRI,
                        _CTRL_H, _COMBO_W)
from ..i18n import LANG_AUTO, resolve_language, tr


def _m3u8_text(language: str, key: str) -> str:
    return tr(language, "m3u8_" + key)


class M3U8DownloaderPage(BaseConverterPage):
    def __init__(self, ffmpeg_handler, parent=None):
        super().__init__("m3u8", ffmpeg_handler, parent)
        self._src = ""
        self._update_state()

    def _get_file_filter(self):
        return tr(self._language, "m3u8_filter")

    def _select_files(self):
        pass  # M3U8 页面不使用文件列表

    def _select_folder(self):
        pass

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        body = QHBoxLayout()
        body.setSpacing(10)

        # ── 左列：来源卡片 ──────────────────────────
        in_card = CardWidget()
        il = in_card.layout()
        il.setSpacing(10)
        self.source_section_label = SectionLabel(tr(self._language, "m3u8_source"))
        il.addWidget(self.source_section_label)

        url_row = QHBoxLayout(); url_row.setSpacing(8)
        self.url_lbl = QLabel(tr(self._language, "m3u8_address"))
        self.url_lbl.setObjectName("row_label")
        self.url_lbl.setFixedWidth(72)
        self.m3u8_url_edit = QLineEdit()
        self.m3u8_url_edit.setMinimumHeight(_CTRL_H)
        self.m3u8_url_edit.setPlaceholderText(tr(self._language, "m3u8_placeholder"))
        self.m3u8_url_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.m3u8_url_edit.textChanged.connect(self._update_src)
        url_row.addWidget(self.url_lbl)
        url_row.addWidget(self.m3u8_url_edit, 1)
        il.addLayout(url_row)

        loc_row = QHBoxLayout(); loc_row.setSpacing(8)
        self.select_local_m3u8_button = QPushButton(tr(self._language, "m3u8_pick_local"))
        self.select_local_m3u8_button.setMinimumSize(190, _BTN_H)
        self.select_local_m3u8_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.select_local_m3u8_button.clicked.connect(self._pick_local)
        loc_row.addWidget(self.select_local_m3u8_button)
        loc_row.addStretch()
        il.addLayout(loc_row)
        il.addStretch()

        # Hidden stub widgets so base-class references don't crash
        self.input_label                  = QLabel()
        self.input_label.setVisible(False)
        self.input_file_list_widget       = DropFileList()
        self.input_file_list_widget.setVisible(False)
        self.select_input_files_button    = QPushButton()
        self.select_input_files_button.setVisible(False)
        self.remove_selected_files_button = QPushButton()
        self.remove_selected_files_button.setVisible(False)

        body.addWidget(in_card, 5)

        # ── 右列：选项 + 操作（垂直堆叠）──────────────
        right_col = QVBoxLayout()
        right_col.setSpacing(10)

        # 输出选项卡片
        opt_card = CardWidget()
        ol = opt_card.layout()
        ol.setSpacing(10)
        self.output_options_label = SectionLabel(tr(self._language, "output_options"))
        ol.addWidget(self.output_options_label)

        # 格式行
        fmt_row = QHBoxLayout(); fmt_row.setSpacing(8)
        self.format_label = SectionLabel(tr(self._language, "format"))
        fmt_row.addWidget(self.format_label)
        self.output_format_combo = QComboBox()
        self.output_format_combo.setMinimumSize(_COMBO_W, _CTRL_H)
        self.output_format_combo.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.output_format_combo.addItems(self.output_formats_available)
        self.output_format_combo.currentIndexChanged.connect(self._on_fmt_changed)
        fmt_row.addWidget(self.output_format_combo)
        fmt_row.addStretch()
        ol.addLayout(fmt_row)

        # 目录行
        dir_row = QHBoxLayout(); dir_row.setSpacing(6)
        self.directory_label = SectionLabel(tr(self._language, "directory"))
        dir_row.addWidget(self.directory_label)
        self.output_dir_edit = QLineEdit()
        self.output_dir_edit.setMinimumHeight(_CTRL_H)
        self.output_dir_edit.setPlaceholderText(tr(self._language, "output_dir_placeholder"))
        self.output_dir_edit.setReadOnly(True)
        self.output_dir_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.select_output_dir_button = QPushButton(tr(self._language, "select"))
        self.select_output_dir_button.setMinimumSize(_BTN_W_SM, _BTN_H)
        self.select_output_dir_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.select_output_dir_button.clicked.connect(self._select_dir)
        dir_row.addWidget(self.output_dir_edit, 1)
        dir_row.addWidget(self.select_output_dir_button)
        ol.addLayout(dir_row)

        # 参数面板
        f = QFrame(); f.setFrameShape(QFrame.Shape.HLine)
        ol.addWidget(f)
        self.args_panel = ArgsPanel("m3u8")
        self.args_panel.preset_changed.connect(self._update_state)
        ol.addWidget(self.args_panel)

        right_col.addWidget(opt_card)

        # 操作卡片
        ctrl_card = CardWidget()
        cl = ctrl_card.layout()
        cl.setSpacing(8)

        ar = QHBoxLayout(); ar.setSpacing(8)
        self.start_conversion_button = QPushButton(tr(self._language, "m3u8_start"))
        self.start_conversion_button.setObjectName("primary")
        self.start_conversion_button.setEnabled(False)
        self.start_conversion_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.start_conversion_button.clicked.connect(self._start_clicked)

        self.cancel_conversion_button = QPushButton(tr(self._language, "cancel"))
        self.cancel_conversion_button.setObjectName("danger")
        self.cancel_conversion_button.setEnabled(False)
        self.cancel_conversion_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
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

        # ══ 下半区：日志卡片 ══
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

    def _retranslate_ui(self):
        if hasattr(self, "source_section_label"):
            self.source_section_label.setText(tr(self._language, "m3u8_source"))
        if hasattr(self, "url_lbl"):
            self.url_lbl.setText(tr(self._language, "m3u8_address"))
        if hasattr(self, "m3u8_url_edit"):
            self.m3u8_url_edit.setPlaceholderText(tr(self._language, "m3u8_placeholder"))
        if hasattr(self, "select_local_m3u8_button"):
            self.select_local_m3u8_button.setText(tr(self._language, "m3u8_pick_local"))
        if hasattr(self, "output_options_label"):
            self.output_options_label.setText(tr(self._language, "output_options"))
        if hasattr(self, "format_label"):
            self.format_label.setText(tr(self._language, "format"))
        if hasattr(self, "directory_label"):
            self.directory_label.setText(tr(self._language, "directory"))
        if hasattr(self, "output_dir_edit"):
            self.output_dir_edit.setPlaceholderText(tr(self._language, "output_dir_placeholder"))
        if hasattr(self, "select_output_dir_button"):
            self.select_output_dir_button.setText(tr(self._language, "select"))
            self.select_output_dir_button.setToolTip(tr(self._language, "m3u8_select_tip"))
        if hasattr(self, "start_conversion_button"):
            self.start_conversion_button.setText(tr(self._language, "m3u8_start"))
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

    def _update_src(self, text=""):
        self._src = (text or self.m3u8_url_edit.text()).strip()
        self._update_state()

    def _pick_local(self):
        p, _ = QFileDialog.getOpenFileName(
            self,
            tr(self._language, "m3u8_pick_title"),
            "",
            tr(self._language, "m3u8_filter"))
        if p:
            self.m3u8_url_edit.setText(p)

    def _select_dir(self):
        d = QFileDialog.getExistingDirectory(
            self,
            tr(self._language, "m3u8_select_tip"))
        if d:
            self.output_dir = d
            self.output_dir_edit.setText(d)
            self._update_state()

    def _start_conversion_process(self):
        if not self.ffmpeg_handler:
            self.log_message(tr(self._language, "m3u8_missing_ffmpeg"), "error")
            return
        src = self._src
        if not src:
            self.log_message(tr(self._language, "m3u8_missing_source"), "error")
            return

        fmt = self.output_format_combo.currentText()
        args = self._build_args(fmt)
        raw_ext = os.path.splitext(src)[1].lower() if "." in src else ""
        is_online = src.startswith("http://") or src.startswith("https://")

        stem = os.path.splitext(os.path.basename(src))[0] if not is_online else "m3u8_output"

        if is_online or raw_ext == ".m3u8":
            protocol_args = ["-protocol_whitelist", "file,http,https,tcp,tls,crypto"]
            extra = self.args_panel.get_extra_args()
            if self.args_panel.is_custom_override():
                args = protocol_args + extra
            else:
                fmt_defaults = DEFAULT_FFMPEG_ARGS.get("video", {}).get(fmt, ["-c", "copy"])
                args = protocol_args + fmt_defaults + extra
            self.conversion_requested.emit(0, src, args, stem)
        else:
            extra = self.args_panel.get_extra_args()
            if self.args_panel.is_custom_override():
                args = extra
            else:
                fmt_defaults = DEFAULT_FFMPEG_ARGS.get("video", {}).get(fmt, ["-c", "copy"])
                args = fmt_defaults + extra
            self.conversion_requested.emit(0, src, args, stem)

    def _update_state(self):
        can_start = bool(self._src and self.output_dir) and not self._is_busy
        self.start_conversion_button.setEnabled(can_start)
        if self._has_ffmpeg_ready():
            self.start_conversion_button.setToolTip("")
        else:
            self.start_conversion_button.setToolTip(tr(self._language, "ffmpeg_not_found_download"))

    def _start_clicked(self):
        if not self._has_ffmpeg_ready():
            self.ffmpeg_download_prompt_requested.emit()
            return
        if not self._src:
            self.log_message(tr(self._language, "m3u8_missing_source"), "error")
            return
        if not self.output_dir:
            self.log_message(tr(self._language, "log_select_output_dir"), "error")
            return
        self._clear_log()
        self._progress_line.clear()
        self.log_message(tr(self._language, "log_start_conversion", count=1), "info")
        self.start_conversion_button.setEnabled(False)
        self.cancel_conversion_button.setEnabled(True)
        self.overall_progress_bar.setValue(0)
        self._start_conversion_process()

    def log_message(self, message, level="info"):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        import html as _html
        prefix = {"info": "ℹ", "warning": "⚠", "error": "❌", "success": "✅",
                  "cmd": ">", "meta": "M", "encoder": "E"}.get(level, "ℹ")
        safe = _html.escape(str(message or ""))
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

# 需要从 config 导入 DEFAULT_FFMPEG_ARGS
from ..config import DEFAULT_FFMPEG_ARGS
