# format_factory/main.py
import sys
import os
import json
import re as _re
import platform
import subprocess
from datetime import datetime, timedelta

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QTabWidget, QMessageBox, QLabel, QDialog, QDialogButtonBox,
    QTextBrowser, QCheckBox, QHBoxLayout
)
from PyQt6.QtCore import Qt, QTimer, QSettings

from format_factory.config import (
    APP_VERSION, UPDATE_CACHE_DIR, FFMPEG_CACHE_DIR, FFMPEG_DIR,
    has_ffmpeg, get_ffmpeg_download_spec,
)
from format_factory.i18n import resolve_language, tr, LANG_AUTO
from format_factory.ffmpeg_handler import FFmpegHandler
from format_factory.gui.settings_page import (
    SettingsPage, GPU_ENCODERS
)
from format_factory.gui.video_converter import VideoConverterPage
from format_factory.gui.audio_converter import AudioConverterPage
from format_factory.gui.image_converter import ImageConverterPage
from format_factory.gui.m3u8_downloader import M3U8DownloaderPage
from format_factory.gui.av_splitter_page import AVSplitterPage
from format_factory.gui.command_converter import CommandConverterPage
from format_factory.theme import build_stylesheet, LIGHT_THEME, DARK_THEME
from format_factory.daily_wallpaper import DailyWallpaperService
from format_factory.gui.bg_widget import BackgroundWidget, analyze_image_colors
from format_factory.gpu import apply_gpu_args
from format_factory.updater import (
    UpdaterService, UpdateDownloaderThread, FFmpegDownloadThread, _parse_version,
    replace_app_with_archive,
)

def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _is_update_required(info: dict) -> bool:
    if _as_bool(info.get("mandatory", False)):
        return True
    min_supported = str(info.get("min_supported_version", "")).strip()
    return bool(min_supported and _parse_version(APP_VERSION) < _parse_version(min_supported))


def _detect_system_theme() -> str:
    system = platform.system()
    try:
        if system == "Windows":
            import winreg

            for path, value_name in (
                (r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme"),
                (r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "SystemUsesLightTheme"),
            ):
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                        value, _ = winreg.QueryValueEx(key, value_name)
                        return "light" if int(value) else "dark"
                except OSError:
                    pass
        elif system == "Darwin":
            proc = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                check=False,
            )
            return "dark" if "Dark" in (proc.stdout or "") else "light"
        elif system == "Linux":
            proc = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True,
                text=True,
                check=False,
            )
            out = (proc.stdout or "").strip().lower()
            if "dark" in out:
                return "dark"
            if "light" in out:
                return "light"
    except Exception:
        pass
    return "light"


# ══════════════════════════════════════════════════════════════════
#  Main Window
# ══════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    _FFMPEG_UPDATE_AFTER_DAYS = 30
    _DAILY_REFRESH_OPTIONS = {1, 2, 3, 4, 5, 6, 7, "manual"}
    _UPDATE_CACHE_KEY = "update_cached_versions"
    _UPDATE_CACHE_TIME_KEY = "update_cached_at"

    def __init__(self):
        super().__init__()
        self._language_pref = "auto"
        self._language = resolve_language(self._language_pref)
        self.setWindowTitle(tr(self._language, "app_title"))
        self.setMinimumSize(1080, 720)
        self.resize(1080, 720)
        self._app_start_time = datetime.now()

        _icon_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "assets", "logo.ico")
        if os.path.isfile(_icon_path):
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(_icon_path))

        self._s              = QSettings("Formix", "App")
        self._theme_name     = self._s.value("theme",         "light")
        self._system_theme_name = _detect_system_theme()
        self._blur_level     = max(0, min(50, int(self._s.value("blur", 0))))
        self._bg_opacity     = int(self._s.value("mask_opacity", 50))
        self._bg_path        = self._s.value("bg_path",       "")
        self._user_bg_path   = self._s.value("user_bg_path",  "")  # 用户手动选择的背景
        self._bg_fill_mode   = self._s.value("bg_fill_mode",  "cover")
        self._command_line_enabled = self._s.value("command_line_enabled", False, type=bool)
        self._language_pref   = self._s.value("language", "auto")
        self._language        = resolve_language(self._language_pref)
        self._bg_colors      = {}
        self._gpu_vendor     = self._s.value("gpu_vendor",    "none")
        self._daily_enabled  = self._s.value("daily_wp",      False, type=bool)
        self._daily_wp_api_url = str(self._s.value("daily_wp_api_url", ""))
        self._daily_wp_refresh_days = self._normalize_daily_refresh_days(
            self._s.value("daily_wp_refresh_days", 1)
        )
        self.ffmpeg_handler = FFmpegHandler()
        self.cmd_ffmpeg_handler = FFmpegHandler()
        self._ffmpeg_ready = bool(getattr(self.ffmpeg_handler, "ffmpeg_path", ""))

        # Batch state
        self._batch_page    = None
        self._batch_files   = []
        self._batch_stems   = []
        self._batch_fmt     = ""
        self._batch_dir     = ""
        self._batch_args    = []
        self._batch_idx     = 0
        self._batch_done    = 0
        self._batch_total   = 0
        self._batch_success = 0
        self._batch_fail    = 0
        self._batch_queue   = []
        self.current_page   = None

        self._init_ui()
        self._connect_signals()
        self._update_ffmpeg_status_ui()
        self._init_ffmpeg_action_timer()

        # 启动时把已保存的 GPU vendor 同步到各页面预设标注
        if self._gpu_vendor != "none":
            for pg in (self.video_page, self.audio_page,
                       self.image_page, self.m3u8_page):
                if hasattr(pg, "args_panel"):
                    pg.args_panel.set_gpu_vendor(self._gpu_vendor)

        if self._bg_path:
            self._bg_colors = analyze_image_colors(self._bg_path)
            self._bg.set_bg_colors(self._bg_colors)
        self._bg.set_fill_mode(self._bg_fill_mode)
        self._bg.set_mask_color(self._effective_theme_name() == "dark")
        self._bg.set_mask_alpha(26)
        self._bg.set_bg_opacity(self._bg_opacity)
        self._apply_theme()
        self._init_system_theme_timer()

        # ── 每日壁纸服务 ──────────────────────────────────────────────
        self._wallpaper_svc = DailyWallpaperService(self)
        self._wallpaper_svc.load_preferences(self._daily_wp_api_url, self._daily_wp_refresh_days)
        self._wallpaper_svc.wallpaper_ready.connect(self._on_wallpaper_ready)
        self._wallpaper_svc.status_changed.connect(self._on_wallpaper_status)
        self._wallpaper_svc.error_occurred.connect(self._on_wallpaper_error)
        if self._daily_enabled:
            self._wallpaper_svc.start()

        # ── 自动更新服务 ──────────────────────────────────────────────
        self._updater_svc = UpdaterService(current_version=APP_VERSION, parent=self)
        self._updater_svc.update_available.connect(self._on_update_available)
        self._updater_svc.versions_loaded.connect(self._on_versions_loaded)
        self._updater_svc.check_failed.connect(self._on_update_check_failed)

        # 启动时清理缓存的安装包
        self._cleanup_update_cache()

        # 启动时后台静默检查
        self._updater_svc.check()

    @classmethod
    def _normalize_daily_refresh_days(cls, value):
        if isinstance(value, str):
            text = value.strip().lower()
            if text == "manual":
                return "manual"
            if text.isdigit():
                num = int(text)
                if num in cls._DAILY_REFRESH_OPTIONS:
                    return num
        elif isinstance(value, int) and value in cls._DAILY_REFRESH_OPTIONS:
            return value
        return 1

    def _init_ffmpeg_action_timer(self):
        self._ffmpeg_action_timer = QTimer(self)
        self._ffmpeg_action_timer.timeout.connect(self._refresh_ffmpeg_action_ui)
        self._ffmpeg_action_timer.start(60 * 1000)
        self._refresh_ffmpeg_action_ui()

    def _ffmpeg_should_show_update(self) -> bool:
        return datetime.now() - self._app_start_time >= timedelta(days=self._FFMPEG_UPDATE_AFTER_DAYS)

    def _ffmpeg_action_text(self) -> str:
        return "update" if self._ffmpeg_should_show_update() else "download"

    def _tr(self, key: str, **kwargs) -> str:
        return tr(self._language, key, **kwargs)

    def _localized_ffmpeg_action_label(self) -> str:
        return self._tr("ffmpeg_update") if self._ffmpeg_action_text() == "update" else self._tr("ffmpeg_download")

    def _refresh_days_label(self, value) -> str:
        if value == "manual":
            return self._tr("settings_never")
        key = "settings_day_singular" if int(value) == 1 else "settings_day_plural"
        return self._tr(key, count=int(value))

    def _wallpaper_error_text(self, key: str, *, request_error_prefix: str = "daily_error_request_failed") -> str:
        if key.startswith("url_error:"):
            return self._tr("error_network", detail=key[10:])
        if key == "no_api":
            return self._tr("daily_error_no_api")
        if key == "invalid_response":
            return self._tr("daily_error_invalid_response")
        if key.startswith("error:"):
            return self._tr("daily_error_request_exception", detail=key[6:])
        return self._tr(request_error_prefix, detail=key)

    def _apply_language(self):
        self._language = resolve_language(self._language_pref or LANG_AUTO)
        self.setWindowTitle(self._tr("app_title"))
        if hasattr(self, "tab_widget"):
            self.tab_widget.setTabText(self.tab_widget.indexOf(self.video_page), self._tr("tab_video"))
            self.tab_widget.setTabText(self.tab_widget.indexOf(self.audio_page), self._tr("tab_audio"))
            self.tab_widget.setTabText(self.tab_widget.indexOf(self.image_page), self._tr("tab_image"))
            self.tab_widget.setTabText(self.tab_widget.indexOf(self.m3u8_page), self._tr("tab_m3u8"))
            self.tab_widget.setTabText(self.tab_widget.indexOf(self.av_page), self._tr("tab_av"))
            self.tab_widget.setTabText(self.tab_widget.indexOf(self.command_page), self._tr("tab_command"))
            self.tab_widget.setTabText(self.tab_widget.indexOf(self.settings_page), self._tr("tab_settings"))
        for page_name in (
            "video_page",
            "audio_page",
            "image_page",
            "m3u8_page",
            "av_page",
            "command_page",
        ):
            page = getattr(self, page_name, None)
            if page is not None and hasattr(page, "set_language"):
                page.set_language(self._language_pref)
        if hasattr(self, "settings_page") and self.settings_page is not None:
            self.settings_page.set_language(self._language_pref)
            self.settings_page.set_ffmpeg_action(self._ffmpeg_action_text())
        if hasattr(self, "statusBar"):
            self._update_ffmpeg_status_ui()

    def _effective_theme_name(self) -> str:
        if self._theme_name == "auto":
            self._system_theme_name = _detect_system_theme()
            return self._system_theme_name
        return self._theme_name

    def _init_system_theme_timer(self):
        self._system_theme_timer = QTimer(self)
        self._system_theme_timer.timeout.connect(self._on_system_theme_tick)
        self._system_theme_timer.start(5000)

    def _on_system_theme_tick(self):
        if self._theme_name != "auto":
            return
        current = _detect_system_theme()
        if current != self._system_theme_name:
            self._system_theme_name = current
            self._apply_theme()

    def _refresh_ffmpeg_action_ui(self, update_status: bool = True):
        action_mode = self._ffmpeg_action_text()
        action = self._tr("action_update") if action_mode == "update" else self._tr("action_download")
        if hasattr(self, "settings_page") and self.settings_page is not None:
            self.settings_page.set_ffmpeg_action(action)
        if update_status:
            self._update_ffmpeg_status_ui(refresh_action=False)

    def _cleanup_update_cache(self):
        import shutil
        if os.path.exists(UPDATE_CACHE_DIR):
            try:
                shutil.rmtree(UPDATE_CACHE_DIR)
            except Exception as e:
                print(f"Failed to cleanup update cache: {e}")

    # ── UI ──────────────────────────────────────────────────────────
    def _init_ui(self):
        self._central = QWidget()
        self._central.setObjectName("qt_centralwidget")
        self.setCentralWidget(self._central)

        self._bg = BackgroundWidget(self._central)
        self._bg.set_image(self._bg_path)
        self._bg.set_fill_mode(self._bg_fill_mode)
        self._bg.set_blur(self._blur_level)
        self._bg.set_dark(self._effective_theme_name() == "dark")
        self._bg.lower()

        root = QVBoxLayout(self._central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tab_widget = QTabWidget()
        self.tab_widget.tabBar().setExpanding(True)
        root.addWidget(self.tab_widget)

        self.video_page    = VideoConverterPage(ffmpeg_handler=self.ffmpeg_handler)
        self.audio_page    = AudioConverterPage(ffmpeg_handler=self.ffmpeg_handler)
        self.image_page    = ImageConverterPage(ffmpeg_handler=self.ffmpeg_handler)
        self.m3u8_page     = M3U8DownloaderPage(ffmpeg_handler=self.ffmpeg_handler)
        self.av_page       = AVSplitterPage(ffmpeg_handler=self.ffmpeg_handler)
        self.command_page  = CommandConverterPage(ffmpeg_handler=self.cmd_ffmpeg_handler)
        self.settings_page = SettingsPage(
            current_theme  = self._theme_name,
            current_blur   = self._blur_level,
            current_bg     = self._bg_path,
            gpu_vendor     = self._gpu_vendor,
            current_bg_fill_mode = self._bg_fill_mode,
            command_line_enabled = self._command_line_enabled,
            daily_enabled  = self._daily_enabled,
            mask_opacity   = self._bg_opacity,
            current_language = self._language_pref,
            daily_api_url = self._daily_wp_api_url,
            daily_refresh_days = self._daily_wp_refresh_days)

        self.tab_widget.addTab(self.video_page,    "")
        self.tab_widget.addTab(self.audio_page,    "")
        self.tab_widget.addTab(self.image_page,    "")
        self.tab_widget.addTab(self.m3u8_page,     "")
        self.tab_widget.addTab(self.av_page,       "")
        self.tab_widget.addTab(self.command_page,  "")
        self.tab_widget.addTab(self.settings_page, "")
        self._command_tab_index = self.tab_widget.indexOf(self.command_page)
        self._apply_command_line_enabled(self._command_line_enabled)
        self._apply_language()

        self.current_page = self.video_page
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, '_bg'):
            self._bg.setGeometry(0, 0,
                                 self._central.width(),
                                 self._central.height())

    # ── Signals ─────────────────────────────────────────────────────
    def _connect_signals(self):
        for pg in (self.video_page, self.audio_page,
                   self.image_page, self.m3u8_page):
            pg.conversion_requested.connect(self._on_batch_start)
            pg.cancel_conversion_signal.connect(self._on_cancel)
            pg.ffmpeg_download_prompt_requested.connect(self._prompt_download_ffmpeg)
        self.command_page.cancel_conversion_signal.connect(self._on_cmd_cancel)

        # av_page：分离走 conversion_requested，合成走 merge_requested
        self.av_page.conversion_requested.connect(self._on_av_split_task)
        self.av_page.merge_requested.connect(self._on_av_merge_task)
        self.av_page.cancel_conversion_signal.connect(self._on_cancel)
        self.av_page.ffmpeg_download_prompt_requested.connect(self._prompt_download_ffmpeg)

        self._connect_ffmpeg_handler_signals()

        self.settings_page.theme_changed.connect(self._on_theme_changed)
        self.settings_page.blur_changed.connect(self._on_blur_changed)
        self.settings_page.mask_opacity_changed.connect(self._on_mask_opacity_changed)
        self.settings_page.bg_fill_mode_changed.connect(self._on_bg_fill_mode_changed)
        self.settings_page.command_line_toggled.connect(self._on_command_line_toggled)
        self.settings_page.language_changed.connect(self._on_language_changed)
        self.settings_page.bg_image_changed.connect(self._on_bg_changed)
        self.settings_page.bg_clear_requested.connect(self._on_bg_clear_requested)
        self.settings_page.gpu_vendor_changed.connect(self._on_gpu_vendor_changed)
        self.settings_page.daily_wallpaper_toggled.connect(self._on_daily_toggled)
        self.settings_page.daily_wallpaper_refresh.connect(self._on_daily_refresh)
        self.settings_page.daily_wallpaper_api_changed.connect(self._on_daily_api_changed)
        self.settings_page.daily_wallpaper_refresh_days_changed.connect(self._on_daily_refresh_days_changed)
        self.settings_page.check_update_requested.connect(self._on_check_update_requested)
        self.settings_page.download_ffmpeg_requested.connect(self._on_download_ffmpeg_requested)

    def _on_tab_changed(self, i):
        pg = self.tab_widget.widget(i)
        if pg is not self.settings_page:
            self.current_page = pg
            if pg is self.command_page and hasattr(pg, "focus_terminal"):
                pg.focus_terminal()

    def _active_ffmpeg_page(self):
        if self._batch_page is not None:
            return self._batch_page
        if self.ffmpeg_handler and self.ffmpeg_handler.is_busy():
            return self.current_page
        return None

    def _ensure_handler_available(self, page) -> bool:
        busy_page = self._active_ffmpeg_page()
        if busy_page is not None and busy_page is not page:
            if page and hasattr(page, "log_message"):
                page.log_message(self._tr("busy_task_running"), "warning")
            return False
        return True

    # ── Batch ────────────────────────────────────────────────────────
    def _on_batch_start(self, idx, inp, args, stem):
        pg = self.current_page
        if not pg: return
        if not self._ensure_ffmpeg_ready(pg):
            return
        if idx != 0:
            return
        if getattr(pg, "media_type", "") == "m3u8":
            batch_files = [inp] if inp else []
            batch_stems = [stem] if stem else []
        else:
            batch_files = list(pg.input_files)
            batch_stems = []
        batch = (
            pg,
            batch_files,
            pg.output_format_combo.currentText(),
            pg.output_dir,
            args,
            batch_stems,
        )
        if self._batch_page is None:
            self._start_batch(batch)
        else:
            self._batch_queue.append(batch)
            pg.log_message(self._tr("task_queued"), "info")
            pg.start_conversion_button.setEnabled(True)
            pg.cancel_conversion_button.setEnabled(False)

    def _start_batch(self, batch):
        if len(batch) >= 6:
            pg, files, fmt, dir_, args, stems = batch
        else:
            pg, files, fmt, dir_, args = batch
            stems = []
        self._batch_page  = pg
        if hasattr(pg, "_is_busy"):
            pg._is_busy = True
        self._batch_files = files
        self._batch_stems = stems
        self._batch_fmt   = fmt
        self._batch_dir   = dir_
        self._batch_args  = args
        self._batch_total   = len(files)
        self._batch_idx     = 0
        self._batch_done    = 0
        self._batch_success = 0
        self._batch_fail    = 0
        pg.overall_progress_bar.setValue(0)
        pg.overall_progress_bar.setFormat(
            self._tr("progress_batch_format", done=0, total=self._batch_total, percent=0))
        self._submit_next()

    def _submit_next(self):
        pg = self._batch_page
        if not pg or self._batch_idx >= self._batch_total:
            return
        i    = self._batch_idx
        inp  = self._batch_files[i]
        stem = ""
        if i < len(self._batch_stems):
            stem = self._batch_stems[i] or ""
        if not stem:
            stem = os.path.splitext(os.path.basename(inp))[0]
        self._batch_idx += 1

        # M3U8：m3u8 播放列表和 ts 切片统一放在 {stem}_segments 子目录
        if self._batch_fmt == "m3u8":
            seg_dir = os.path.join(self._batch_dir, stem + "_segments")
            os.makedirs(seg_dir, exist_ok=True)
            out = os.path.join(seg_dir, f"{stem}.m3u8")
            # 把参数里的 %03d.ts 替换为子目录绝对路径
            final_args = []
            for a in self._batch_args:
                if a == "%03d.ts":
                    final_args.append(os.path.join(seg_dir, "%03d.ts"))
                else:
                    final_args.append(a)
        else:
            out_name = pg.build_output_path(inp, self._batch_fmt) \
                if hasattr(pg, "build_output_path") else f"{stem}.{self._batch_fmt}"
            out = os.path.join(self._batch_dir, out_name)
            final_args = self._batch_args

        in_size = ""
        try:
            sz = os.path.getsize(inp)
            in_size = f"  ({sz/1024/1024:.1f} MB)" if sz > 1024*1024 \
                      else f"  ({sz/1024:.0f} KB)"
        except OSError:
            pass

        # GPU injection（基于当前 final_args，不覆盖 m3u8 里已处理的路径）
        if self._gpu_vendor != "none":
            new_args, fallback_reason = apply_gpu_args(
                final_args, self._gpu_vendor, self._batch_fmt, self._language)
            if fallback_reason:
                # GPU 不支持此预设，自动回退 CPU，记录提示
                pg.log_ffmpeg_line(i, "warning",
                    self._tr("log_gpu_fallback_cpu", reason=fallback_reason))
            elif new_args != final_args:
                final_args = new_args
                enc = GPU_ENCODERS.get(self._gpu_vendor, {})
                pg.log_ffmpeg_line(i, "encoder",
                    self._tr("log_gpu_enabled", codec=enc.get("h264", ""), label=enc.get("label", "")))
            # fallback_reason 有值时 new_args == final_args，继续用 CPU 参数

        pg.log_message(self._tr(
            "log_queue_item",
            index=i + 1,
            total=self._batch_total,
            input=os.path.basename(inp),
            size=in_size,
            output=os.path.basename(out),
        ), "info")
        self.ffmpeg_handler.convert_file(i, inp, out, final_args)

    def _on_cancel(self):
        self._batch_queue.clear()
        if self.ffmpeg_handler:
            self.ffmpeg_handler.cancel_conversion()

    def _on_cmd_cancel(self):
        if self.cmd_ffmpeg_handler:
            self.cmd_ffmpeg_handler.cancel_conversion()

    # ── AV Splitter / Merger ─────────────────────────────────────────
    def _on_av_split_task(self, idx: int, inp: str, args: list, stem: str):
        """
        分离任务：stem 由 SplitTab 构建，格式为 "{原始名}_audio" 或 "{原始名}_video"。
        扩展名从 split_tab 当前 combo 读取，拼出完整输出路径后直接送入 handler。
        """
        tab     = self.av_page.split_tab
        out_dir = tab._out_dir
        ext     = (tab._audio_fmt_combo.currentText()
                   if stem.endswith("_audio")
                   else tab._video_fmt_combo.currentText())
        out = os.path.join(out_dir, f"{stem}.{ext}")

        if idx == 0 and not self._ensure_handler_available(self.av_page):
            if hasattr(tab, "_cancel_btn"):
                tab._cancel_btn.setEnabled(False)
            if hasattr(tab, "_update_state"):
                tab._update_state()
            return
        self._batch_page  = self.av_page
        self._batch_total = tab._total
        if self._ensure_ffmpeg_ready(self.av_page):
            self.ffmpeg_handler.convert_file(idx, inp, out, args)

    def _on_av_merge_task(self, idx: int, video: str, audio: str,
                          out: str, args: list):
        """
        合成任务：ffmpeg 需要两个 -i 输入。
        handler._run 构建的命令为：ffmpeg -y -i <inp> <args> <out>
        在 args 最前面注入 "-i audio"，命令就变为：
          ffmpeg -y -i video -i audio <merge_args> out
        """
        if idx == 0 and not self._ensure_handler_available(self.av_page):
            merge_tab = self.av_page.merge_tab
            if hasattr(merge_tab, "_cancel_btn"):
                merge_tab._cancel_btn.setEnabled(False)
            if hasattr(merge_tab, "_update_state"):
                merge_tab._update_state()
            return
        self._batch_page  = self.av_page
        self._batch_total = self.av_page.merge_tab._total
        full_args = ["-i", audio] + args
        if self._ensure_ffmpeg_ready(self.av_page):
            self.ffmpeg_handler.convert_file(idx, video, out, full_args)

    def _on_log_line(self, idx, kind, text):
        if self._batch_page:
            self._batch_page.log_ffmpeg_line(idx, kind, text)

    def _on_started(self, idx, path):
        pg = self._batch_page
        if pg:
            pg.log_message(self._tr(
                "log_batch_start_item",
                index=idx + 1,
                total=self._batch_total,
                name=os.path.basename(path),
            ), "info")

    def _on_progress(self, idx, msg, pct):
        pg = self._batch_page
        if pg:
            pg.update_overall_progress(idx, self._batch_total, pct)
            self.statusBar().showMessage(
                f"[{idx+1}/{self._batch_total}] {msg} {pct}%")

    def _on_finished(self, idx, status, msg):
        pg = self._batch_page
        if not pg: return
        kind = {"success":"success","cancelled":"warning",
                "failure":"error"}.get(status, "info")
        display_msg = msg
        if status == "success":
            self._batch_success += 1
            m = _re.search(r"'([^']+)' ✓$", msg)
            if m and self._batch_dir:
                out_path = os.path.join(self._batch_dir, m.group(1))
                try:
                    sz = os.path.getsize(out_path)
                    sz_str = f"{sz/1024/1024:.2f} MB" if sz > 1024*1024 \
                             else f"{sz/1024:.0f} KB"
                    display_msg = msg.replace(" ✓", f"  [{sz_str}] ✓")
                except OSError:
                    pass
        elif status == "failure":
            self._batch_fail += 1

        pg.log_message(f"[{idx+1}/{self._batch_total}]  {display_msg}", kind)

        if status == "success":
            pg.update_overall_progress(idx, self._batch_total, 100)
            if hasattr(pg, "set_progress_state"):
                pg.set_progress_state("100%", "success")
        elif status == "cancelled":
            if hasattr(pg, "set_progress_state"):
                pg.set_progress_state(tr(self._language, "progress_cancelled"), "warning")
        elif status == "failure":
            if hasattr(pg, "set_progress_state"):
                pg.set_progress_state(tr(self._language, "progress_failed"), "error")

        self._batch_done += 1

        if status != "cancelled" and self._batch_idx < self._batch_total:
            # av_page 任务由各子 tab 自己管理批次，不走 _submit_next
            if pg is not self.av_page:
                self._submit_next(); return

        if self._batch_done >= self._batch_total or status == "cancelled":
            if status == "cancelled":
                pg.log_message(tr(self._language, "log_conversion_cancelled"), "warning")
                self.statusBar().showMessage(tr(self._language, "progress_cancelled"))
            else:
                s = self._batch_success
                f = self._batch_fail
                t = self._batch_total
                if f == 0:
                    summary = tr(self._language, "status_all_success", count=t)
                    pg.log_message(summary, "success")
                    self.statusBar().showMessage(tr(self._language, "status_all_success", count=t))
                elif s == 0:
                    summary = tr(self._language, "status_all_failed", count=t)
                    pg.log_message(summary, "error")
                    self.statusBar().showMessage(tr(self._language, "status_conversion_failed"))
                else:
                    summary = tr(self._language, "status_partial", success=s, fail=f, total=t)
                    pg.log_message(summary, "warning")
                    self.statusBar().showMessage(tr(self._language, "status_success_fail", success=s, fail=f))

            # av_page 用自己的 on_finished 更新按钮/进度
            if pg is self.av_page:
                pg.on_finished(idx, status, display_msg)
            else:
                if hasattr(pg, "_is_busy"):
                    pg._is_busy = False
                if hasattr(pg, "_replace_on_next_add"):
                    pg._replace_on_next_add = True
                pg.overall_progress_bar.setValue(100)
                pg.overall_progress_bar.setFormat(tr(self._language, "progress_overall"))
                pg.start_conversion_button.setEnabled(True)
                pg.cancel_conversion_button.setEnabled(False)
            self._batch_page = None

            # 开始下一个排队批次
            if self._batch_queue:
                next_batch = self._batch_queue.pop(0)
                next_pg = next_batch[0]
                next_pg.log_message(self._tr("log_queue_start"), "info")
                self._start_batch(next_batch)

    # ── GPU ─────────────────────────────────────────────────────────
    def _on_gpu_vendor_changed(self, vendor: str):
        self._gpu_vendor = vendor
        self._s.setValue("gpu_vendor", vendor)
        # 通知所有转换页面刷新预设标注
        for pg in (self.video_page, self.audio_page,
                   self.image_page, self.m3u8_page):
            if hasattr(pg, "args_panel"):
                pg.args_panel.set_gpu_vendor(vendor)
        self.statusBar().showMessage(
            f"{self._tr('gpu_setting_updated')}  ·  {self._vendor_status_text()}", 4000)

    def _vendor_status_text(self) -> str:
        from format_factory.gui.settings_page import GPU_VENDORS
        info = GPU_VENDORS.get(self._gpu_vendor, GPU_VENDORS["none"])
        return self._tr("gpu_none_label") if self._gpu_vendor == "none" else info["label"]

    def _ensure_ffmpeg_ready(self, page=None) -> bool:
        if self.ffmpeg_handler is not None and self._ffmpeg_ready:
            return True
        action = self._localized_ffmpeg_action_label()
        msg = f"{self._tr('status_no_ffmpeg', action=action)}  {self._tr('ffmpeg_install_path', path='format_factory/FFmpeg/bin')}"
        self._batch_page = None
        target = page or self.current_page
        if target and hasattr(target, "log_message"):
            target.log_message(msg, "error")
        if target is self.av_page and getattr(self.av_page, "_batch_page", None):
            active = self.av_page._batch_page
            if hasattr(active, "_cancel_btn"):
                active._cancel_btn.setEnabled(False)
            if hasattr(active, "_update_state"):
                active._update_state()
        self.statusBar().showMessage(self._tr("status_no_ffmpeg", action=action), 5000)
        self._update_ffmpeg_status_ui()
        return False

    def _prompt_download_ffmpeg(self):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self._tr("ffmpeg_required_title"))
        box.setText(self._tr("ffmpeg_required_msg"))
        cancel_btn = box.addButton(self._tr("cancel"), QMessageBox.ButtonRole.RejectRole)
        download_btn = box.addButton(self._tr("go_download"), QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(download_btn)
        box.exec()

        if box.clickedButton() is not download_btn:
            return

        self.tab_widget.setCurrentWidget(self.settings_page)
        self._on_download_ffmpeg_requested()

    def _update_ffmpeg_status_ui(self, refresh_action: bool = True):
        self._ffmpeg_ready = has_ffmpeg()
        if refresh_action:
            self._refresh_ffmpeg_action_ui(update_status=False)
        action = self._localized_ffmpeg_action_label()
        if self._ffmpeg_ready:
            self.settings_page.set_ffmpeg_status(
                self._tr("status_ffmpeg_ready"), downloading=False)
        else:
            self.settings_page.set_ffmpeg_status(
                f"{self._tr('status_no_ffmpeg', action=action)}  {self._tr('ffmpeg_install_path', path='format_factory/FFmpeg/bin')}", downloading=False)
        vendor_lbl = self._vendor_status_text()
        ffmpeg_lbl = self._tr("status_ffmpeg_ready") if self._ffmpeg_ready else self._tr("status_no_ffmpeg", action=action)
        self.statusBar().showMessage(f"{self._tr('status_ready')}  ·  {vendor_lbl}  ·  {ffmpeg_lbl}")
        for pg in ("video_page", "audio_page", "image_page", "m3u8_page"):
            page = getattr(self, pg, None)
            if page is not None and hasattr(page, "_update_state"):
                page._update_state()
        if hasattr(self, "av_page") and self.av_page is not None:
            if hasattr(self.av_page, "split_tab") and hasattr(self.av_page.split_tab, "_update_state"):
                self.av_page.split_tab._update_state()
            if hasattr(self.av_page, "merge_tab") and hasattr(self.av_page.merge_tab, "_update_state"):
                self.av_page.merge_tab._update_state()

    # ── Close ────────────────────────────────────────────────────────
    def closeEvent(self, event):
        """关闭窗口时，终止仍在运行的 FFmpeg / FFprobe 后退出。"""
        if self.ffmpeg_handler:
            self.ffmpeg_handler.shutdown()
        if self.cmd_ffmpeg_handler:
            self.cmd_ffmpeg_handler.shutdown()
        if hasattr(self, "_wallpaper_svc"):
            self._wallpaper_svc.stop()
        event.accept()

    # ── Daily Wallpaper ──────────────────────────────────────────────
    def _on_bg_clear_requested(self):
        """
        用户点击背景图片的 ✕ 按钮：
          1. 清空背景
          2. 若每日壁纸正在运行，自动关闭并同步 UI
        """
        if self._daily_enabled:
            self._daily_enabled = False
            self._s.setValue("daily_wp", False)
            self._wallpaper_svc.stop()
            self.settings_page._daily_enabled = False
            self.settings_page._refresh_daily_ui()

    def _on_daily_toggled(self, enabled: bool):
        self._daily_enabled = enabled
        self._s.setValue("daily_wp", enabled)
        if enabled:
            self._wallpaper_svc.start()
        else:
            self._wallpaper_svc.stop()
            # 恢复用户手动选择的背景（没选过则为空）
            restore = self._user_bg_path if (
                self._user_bg_path and os.path.isfile(self._user_bg_path)
            ) else ""
            self._bg_path = restore
            self._s.setValue("bg_path", restore)
            self._bg.set_image(restore)
            self._bg.set_fill_mode(self._bg_fill_mode)
            self._bg_colors = analyze_image_colors(restore) if restore else {}
            self._bg.set_bg_colors(self._bg_colors)
            self._apply_theme()
            if restore:
                self.settings_page._bg_path = restore
                self.settings_page.bg_lbl.setText(os.path.basename(restore))
            else:
                self.settings_page._bg_path = ""
                self.settings_page.bg_lbl.setText(self._tr("settings_not_set"))

    def _on_daily_refresh(self):
        """手动刷新：清除缓存 + 重新获取（force_refresh 内部已包含清缓存）。"""
        self._wallpaper_svc.force_refresh()

    def _on_daily_api_changed(self, api_url: str):
        self._daily_wp_api_url = str(api_url or "").strip()
        self._s.setValue("daily_wp_api_url", self._daily_wp_api_url)
        self._wallpaper_svc.set_api_url(self._daily_wp_api_url)
        if self._daily_wp_api_url:
            if self._daily_enabled:
                self.settings_page.set_daily_status(self._tr("daily_api_applied"))
            else:
                self.settings_page.set_daily_status(self._tr("daily_api_saved_disabled"))
        else:
            self.settings_page.set_daily_status(self._tr("daily_api_missing_custom"))

    def _on_daily_refresh_days_changed(self, refresh_days):
        self._daily_wp_refresh_days = self._normalize_daily_refresh_days(refresh_days)
        self._s.setValue("daily_wp_refresh_days", self._daily_wp_refresh_days)
        self._wallpaper_svc.set_refresh_policy(self._daily_wp_refresh_days)
        label = self._refresh_days_label(self._daily_wp_refresh_days)
        self.settings_page.set_daily_status(self._tr("daily_refresh_pending", label=label))

    def _on_wallpaper_status(self, key: str):
        _MAP = {
            "cached": self._tr("daily_status_cached"),
            "fetching": self._tr("daily_status_fetching"),
            "done": self._tr("daily_status_done"),
        }
        if key.startswith("fail:"):
            raw = key[5:]
            msg = self._wallpaper_error_text(raw)
            self.settings_page.set_daily_status(self._tr("daily_error_fetch_failed", detail=msg))
        else:
            self.settings_page.set_daily_status(_MAP.get(key, key))

    def _on_wallpaper_error(self, key: str):
        msg = self._wallpaper_error_text(key)
        self.settings_page.set_daily_status(f"❌ {msg}")

    def _on_wallpaper_ready(self, local_path: str):
        """
        壁纸已下载到本地，直接应用为背景（不修改 _user_bg_path）。
        wallpaper_ready 信号现在携带本地路径，无需再在主线程下载。
        """
        if not local_path or not os.path.isfile(local_path):
            return
        self._bg_path = local_path
        self._s.setValue("bg_path", local_path)
        self._bg.set_image(local_path)
        self._bg.set_fill_mode(self._bg_fill_mode)
        self._bg_colors = analyze_image_colors(local_path)
        self._bg.set_bg_colors(self._bg_colors)
        self._apply_theme()
        self.settings_page.set_daily_bg_preview(local_path)
        self.statusBar().showMessage(self._tr("daily_updated_toast"), 4000)

    # ── Updater ──────────────────────────────────────────────────────
    def _on_check_update_requested(self):
        """用户点击"检查更新"按钮，启动后台检查。"""
        self._updater_svc.check()

    def _on_update_available(self, info: dict):
        """有新版本时弹窗提示，并在设置页显示状态。"""
        ver   = info.get("version", "?")
        date  = info.get("release_date", "")
        notes = info.get("release_notes", "")
        url   = info.get("update_url", "") or info.get("html_url", "") or info.get("zipball_url", "")
        required_update = _is_update_required(info)

        status = self._tr("update_found_status", icon="🚨" if required_update else "🆕", version=ver)
        if date:
            status += f"  ({date})"
        if required_update:
            status += self._tr("update_required_suffix")
        self.settings_page.set_update_status(status)
        self.settings_page.set_version_badge(True, ver)
        # 公告由 _on_versions_loaded 统一渲染（新版本+当前版本）

        # 检查是否用户选择过“不再显示”该版本的更新弹窗
        ignored_version = self._s.value("ignored_update_version", "")
        if ignored_version == ver and not required_update:
            return  # 如果该版本已经被忽略，直接返回不显示弹窗

        if self._show_update_dialog(ver, date, notes, url, required_update):
            self._start_internal_download(url, ver)

    def _show_update_dialog(self, ver: str, release_date: str, notes: str, url: str, required_update: bool) -> bool:
        dialog = QDialog(self)
        dialog.setWindowTitle(self._tr("update_dialog_title"))
        dialog.setModal(True)
        dialog.resize(620, 520)

        root = QVBoxLayout(dialog)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(10)

        title = QLabel(f"<b>{self._tr('update_dialog_heading', version=ver)}</b>")
        title.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(title)

        meta = []
        if release_date:
            meta.append(self._tr("update_release_date", date=release_date))
        meta.append(self._tr("update_type_required") if required_update else self._tr("update_type_optional"))
        meta_label = QLabel("  ·  ".join(meta))
        meta_label.setObjectName("section_title")
        meta_label.setWordWrap(True)
        root.addWidget(meta_label)

        if required_update:
            warn = QLabel(f"<b>{self._tr('update_required_warning')}</b>")
            warn.setTextFormat(Qt.TextFormat.RichText)
            warn.setWordWrap(True)
            root.addWidget(warn)

        notes_view = QTextBrowser()
        notes_view.setOpenExternalLinks(True)
        notes_view.setReadOnly(True)
        notes_view.setMinimumHeight(260)
        notes_view.setMarkdown(notes or self._tr("update_notes_empty"))
        root.addWidget(notes_view, 1)

        cb_ignore = None
        if not required_update:
            cb_ignore = QCheckBox(self._tr("update_ignore_version"))
            root.addWidget(cb_ignore)

        buttons = QDialogButtonBox(dialog)
        download_btn = None
        later_btn = None
        if url:
            download_btn = buttons.addButton(self._tr("update_download_now"), QDialogButtonBox.ButtonRole.AcceptRole)
            download_btn.clicked.connect(dialog.accept)
            if not required_update:
                later_btn = buttons.addButton(self._tr("update_later"), QDialogButtonBox.ButtonRole.RejectRole)
                later_btn.clicked.connect(dialog.reject)
        else:
            later_btn = buttons.addButton(self._tr("close"), QDialogButtonBox.ButtonRole.AcceptRole)
            later_btn.clicked.connect(dialog.accept)

        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(buttons)
        root.addLayout(bottom)

        accepted = dialog.exec() == int(QDialog.DialogCode.Accepted)
        if cb_ignore is not None and cb_ignore.isChecked():
            self._s.setValue("ignored_update_version", ver)
        return bool(accepted and url)

    def _start_internal_download(self, url: str, ver: str):
        """开始内部下载更新包并显示进度条"""
        from PyQt6.QtWidgets import QProgressDialog
        import os

        self._dl_progress = QProgressDialog(tr(self._language, "download_update_progress", ver=ver) + " (0%)", tr(self._language, "cancel"), 0, 100, self)
        self._dl_progress.setWindowTitle(tr(self._language, "download_update_title"))
        # 修改为非模态，不阻塞主窗口操作
        self._dl_progress.setWindowModality(Qt.WindowModality.NonModal)
        self._dl_progress.setAutoClose(True)
        self._dl_progress.setAutoReset(True)
        self._dl_progress.setValue(0)

        # 固定进度条窗口大小，防止因为文本改变导致窗口一直闪烁和变形
        self._dl_progress.setFixedSize(350, 120)

        self._dl_thread = UpdateDownloaderThread(url, UPDATE_CACHE_DIR, self)

        def on_progress(bytes_read, total_size):
            if total_size > 0:
                percent = int(bytes_read * 100 / total_size)
                self._dl_progress.setLabelText(self._tr("download_update_progress", ver=ver) + f" ({percent}%)")
                self._dl_progress.setValue(percent)

        def on_finished(save_path, error_msg):
            self._dl_progress.close()
            if error_msg:
                if error_msg != "cancelled":
                    QMessageBox.warning(
                        self,
                        self._tr("update_download_failed_title"),
                        self._tr("update_download_failed_msg", message=error_msg),
                    )
            elif save_path and os.path.exists(save_path):
                self._on_download_complete(save_path)

        def on_cancel():
            self._dl_thread.cancel()

        self._dl_thread.progress.connect(on_progress)
        self._dl_thread.finished.connect(on_finished)
        self._dl_progress.canceled.connect(on_cancel)

        self._dl_thread.start()
        self._dl_progress.show()

    def _on_download_complete(self, save_path: str):
        """下载完成，准备运行安装包"""
        import platform
        import subprocess

        system = platform.system()
        if system in {"Darwin", "Linux"}:
            try:
                app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                replace_app_with_archive(save_path, app_root)
                QMessageBox.information(self, self._tr("update_complete_title"), self._tr("update_complete_msg"))
            except Exception as e:
                QMessageBox.critical(
                    self,
                    self._tr("update_replace_failed_title"),
                    self._tr("update_replace_failed_msg", message=str(e), path=save_path),
                )
                return
            from PyQt6.QtWidgets import QApplication
            QApplication.quit()
            return

        msg = QMessageBox(self)
        msg.setWindowTitle(self._tr("update_download_complete_title"))
        msg.setText(self._tr("update_download_complete_msg"))
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

        try:
            if system == "Windows":
                # Windows 下使用 os.startfile 或 subprocess
                os.startfile(save_path)
        except Exception as e:
            QMessageBox.critical(
                self,
                self._tr("update_run_failed_title"),
                self._tr("update_run_failed_msg", message=str(e), path=save_path),
            )
            return

        # 启动安装包后，退出当前应用
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()

    def _normalize_cached_update_versions(self, versions) -> list:
        normalized = []
        if not isinstance(versions, list):
            return normalized
        for item in versions:
            if not isinstance(item, dict):
                continue
            version = str(item.get("version", "")).strip()
            if not version:
                continue
            normalized.append({
                "version": version,
                "release_date": str(item.get("release_date", "") or "").strip(),
                "update_url": str(item.get("update_url", "") or "").strip(),
                "html_url": str(item.get("html_url", "") or "").strip(),
                "zipball_url": str(item.get("zipball_url", "") or "").strip(),
                "release_notes": str(item.get("release_notes", "") or ""),
                "mandatory": _as_bool(item.get("mandatory", False)),
                "min_supported_version": str(item.get("min_supported_version", "") or "").strip(),
            })
        return normalized

    def _save_cached_update_versions(self, versions: list):
        versions = self._normalize_cached_update_versions(versions)
        if not versions:
            return
        self._s.setValue(self._UPDATE_CACHE_KEY, json.dumps(versions, ensure_ascii=False))
        self._s.setValue(self._UPDATE_CACHE_TIME_KEY, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def _load_cached_update_versions(self) -> tuple[list, str]:
        raw = self._s.value(self._UPDATE_CACHE_KEY, "")
        fetched_at = str(self._s.value(self._UPDATE_CACHE_TIME_KEY, "") or "").strip()
        if not raw:
            return [], fetched_at
        try:
            versions = json.loads(raw)
        except Exception:
            return [], fetched_at
        return self._normalize_cached_update_versions(versions), fetched_at

    def _build_update_notes_markdown(self, versions: list) -> str:
        cur_tuple = _parse_version(APP_VERSION)
        newer_parts = []
        current_part = None
        for v in versions:
            ver = v.get("version", "")
            date = v.get("release_date", "")
            notes = v.get("release_notes", "")
            if not ver:
                continue
            header = f"## v{ver}"
            if date:
                header += f"  ({date})"
            body = notes or ""
            block = header + (f"\n\n{body}" if body else "")
            if _parse_version(ver) > cur_tuple:
                newer_parts.append(block)
            elif _parse_version(ver) == cur_tuple:
                current_part = block

        sections = []
        if newer_parts:
            sections.append("\n\n---\n\n".join(newer_parts))
        if current_part:
            sections.append(f"## {self._tr('update_current_version_section')}\n\n{current_part}")
        return "\n\n---\n\n".join(sections)

    def _apply_update_versions_to_ui(self, versions: list, *, offline_fallback: bool = False, fetched_at: str = ""):
        self.settings_page.populate_versions(versions)
        if not versions:
            self.settings_page.set_update_status(self._tr("update_versions_empty"))
            self.settings_page.set_update_notes("")
            return

        latest = versions[0]
        latest_ver = str(latest.get("version", "") or "")
        latest_date = str(latest.get("release_date", "") or "")
        has_update = _parse_version(latest_ver) > _parse_version(APP_VERSION)
        required_update = _is_update_required(latest)

        if offline_fallback:
            suffix = (
                self._tr("update_offline_suffix_at", time=fetched_at)
                if fetched_at else self._tr("update_offline_suffix_generic")
            )
            if has_update:
                status = self._tr(
                    "update_offline_status_new",
                    icon="🚨" if required_update else "🆕",
                    version=latest_ver,
                )
                if latest_date:
                    status += f"  ({latest_date})"
                if required_update:
                    status += self._tr("update_required_suffix")
                status += f"  {suffix}"
                self.settings_page.set_version_badge(True, latest_ver)
            else:
                status = self._tr("update_offline_status_current", version=APP_VERSION, suffix=suffix)
                self.settings_page.set_version_badge(False)
            self.settings_page.set_update_status(status)
        else:
            if not has_update:
                self.settings_page.set_update_status(self._tr("update_latest_status", version=APP_VERSION))
                self.settings_page.set_version_badge(False)

        self.settings_page.set_update_notes(self._build_update_notes_markdown(versions))

    def _on_versions_loaded(self, versions: list):
        """版本列表加载完成；构建更新公告（新版本在上，当前版本在下）。"""
        self._save_cached_update_versions(versions)
        self._apply_update_versions_to_ui(versions)

    def _on_update_check_failed(self, err: str):
        """版本检查失败，翻译为中文后显示在设置页。"""
        if err.startswith("url_error:"):
            cached_versions, fetched_at = self._load_cached_update_versions()
            if cached_versions:
                self._apply_update_versions_to_ui(
                    cached_versions,
                    offline_fallback=True,
                    fetched_at=fetched_at,
                )
                return
        if err.startswith("url_error:"):
            msg = self._tr("error_network", detail=err[10:])
        elif err.startswith("error:"):
            msg = self._tr("update_check_failed", detail=err[6:])
        else:
            msg = self._tr("update_check_failed", detail=err)
        self.settings_page.set_update_status(f"❌ {msg}")

    def _on_download_ffmpeg_requested(self):
        from PyQt6.QtWidgets import QProgressDialog

        if self._ffmpeg_ready and self.ffmpeg_handler is not None:
            self.settings_page.set_ffmpeg_status(self._tr("ffmpeg_already_present"), downloading=False)
            return

        action_mode = self._ffmpeg_action_text()
        action = self._tr("action_update") if action_mode == "update" else self._tr("action_download")

        self._ffmpeg_progress = QProgressDialog(tr(self._language, "ffmpeg_download_progress", action=action) + " (0%)", tr(self._language, "cancel"), 0, 100, self)
        self._ffmpeg_progress.setWindowTitle(tr(self._language, "ffmpeg_download_title", action=action))
        self._ffmpeg_progress.setWindowModality(Qt.WindowModality.NonModal)
        self._ffmpeg_progress.setAutoClose(True)
        self._ffmpeg_progress.setAutoReset(True)
        self._ffmpeg_progress.setValue(0)
        self._ffmpeg_progress.setFixedSize(360, 120)

        self._ffmpeg_dl_thread = FFmpegDownloadThread(
            get_ffmpeg_download_spec(), FFMPEG_CACHE_DIR, FFMPEG_DIR, self)

        def on_progress(done, total, stage):
            if stage.startswith("download"):
                percent = int(done * 100 / total) if total > 0 else 0
                self._ffmpeg_progress.setLabelText(tr(self._language, "ffmpeg_download_progress", action=action) + f" ({percent}%)")
                self._ffmpeg_progress.setValue(percent)
                self.settings_page.set_ffmpeg_status(
                    tr(self._language, "ffmpeg_download_progress", action=action) + f" {percent}%", downloading=True)
            else:
                percent = int(done * 100 / total) if total > 0 else 0
                self._ffmpeg_progress.setLabelText(self._tr("ffmpeg_extract_progress", percent=percent))
                self._ffmpeg_progress.setValue(percent)
                self.settings_page.set_ffmpeg_status(
                    self._tr("ffmpeg_extract_progress_inline", percent=percent), downloading=True)

        def on_finished(success, message):
            self._ffmpeg_progress.close()
            if not success:
                if message != "cancelled":
                    QMessageBox.warning(
                        self,
                        self._tr("ffmpeg_failed_title", action=action),
                        self._tr("ffmpeg_failed_msg", action=action, message=message),
                    )
                    self.settings_page.set_ffmpeg_status(
                        self._tr("ffmpeg_failed_msg", action=action, message=message), downloading=False)
                else:
                    self.settings_page.set_ffmpeg_status(
                        self._tr("ffmpeg_cancelled_msg", action=action), downloading=False)
                return

            try:
                self.ffmpeg_handler = FFmpegHandler()
                self.cmd_ffmpeg_handler = FFmpegHandler()
                self._ffmpeg_ready = True
            except FileNotFoundError as e:
                self.ffmpeg_handler = None
                self.cmd_ffmpeg_handler = None
                self._ffmpeg_ready = False
                QMessageBox.warning(self, self._tr("ffmpeg_install_error"), str(e))
                self.settings_page.set_ffmpeg_status(str(e), downloading=False)
                return

            self._attach_ffmpeg_handler_to_pages()
            self._connect_ffmpeg_handler_signals()
            self._update_ffmpeg_status_ui()
            self.statusBar().showMessage(self._tr("ffmpeg_done_msg", action_lower=action.lower()), 5000)
            QMessageBox.information(
                self,
                self._tr("ffmpeg_done_title", action=action),
                self._tr("ffmpeg_done_msg", action_lower=action.lower()),
            )

        def on_cancel():
            self._ffmpeg_dl_thread.cancel()

        self._ffmpeg_dl_thread.progress.connect(on_progress)
        self._ffmpeg_dl_thread.finished.connect(on_finished)
        self._ffmpeg_progress.canceled.connect(on_cancel)

        self.settings_page.set_ffmpeg_status(self._tr("ffmpeg_preparing", action=action), downloading=True)
        self._ffmpeg_dl_thread.start()
        self._ffmpeg_progress.show()

    def _connect_ffmpeg_handler_signals(self):
        if not self.ffmpeg_handler:
            return
        try:
            self.ffmpeg_handler.conversion_started.disconnect(self._on_started)
        except Exception:
            pass
        try:
            self.ffmpeg_handler.progress_update.disconnect(self._on_progress)
        except Exception:
            pass
        try:
            self.ffmpeg_handler.conversion_finished.disconnect(self._on_finished)
        except Exception:
            pass
        try:
            self.ffmpeg_handler.log_line.disconnect(self._on_log_line)
        except Exception:
            pass
        self.ffmpeg_handler.conversion_started.connect(self._on_started)
        self.ffmpeg_handler.progress_update.connect(self._on_progress)
        self.ffmpeg_handler.conversion_finished.connect(self._on_finished)
        self.ffmpeg_handler.log_line.connect(self._on_log_line)

    def _attach_ffmpeg_handler_to_pages(self):
        handler = self.ffmpeg_handler
        if not handler:
            return

        for pg in (self.video_page, self.audio_page, self.image_page, self.m3u8_page):
            pg.ffmpeg_handler = handler
            try:
                handler.file_info_ready.connect(pg.handle_file_info_ready)
            except Exception:
                pass
            if hasattr(pg, "_update_state"):
                pg._update_state()

        self.command_page.attach_ffmpeg_handler(self.cmd_ffmpeg_handler)

        self.audio_page.ffmpeg_handler = handler
        try:
            handler.conversion_finished.connect(self.audio_page._on_conversion_finished)
        except Exception:
            pass

        self.av_page._handler = handler
        self.av_page.split_tab._handler = handler
        self.av_page.merge_tab._handler = handler
        try:
            handler.file_info_ready.connect(self.av_page.split_tab._on_file_info)
        except Exception:
            pass

    # ── Theme ────────────────────────────────────────────────────────
    def _on_theme_changed(self, mode):
        self._theme_name = mode
        self._s.setValue("theme", mode)
        self._apply_theme()

    def _on_blur_changed(self, level):
        self._blur_level = level
        self._s.setValue("blur", level)
        self._bg.set_blur(level)
        self._apply_theme()

    def _on_mask_opacity_changed(self, pct: int):
        self._bg_opacity = pct
        self._s.setValue("mask_opacity", pct)
        self._bg.set_bg_opacity(pct)

    def _on_language_changed(self, language: str):
        self._language_pref = language or LANG_AUTO
        self._s.setValue("language", self._language_pref)
        self._apply_language()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(self._tr("language_restart_title"))
        box.setText(self._tr("language_restart_msg"))
        now_btn = box.addButton(self._tr("language_restart_now"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self._tr("language_restart_later"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is now_btn:
            self.close()

    def _apply_command_line_enabled(self, enabled: bool):
        enabled = bool(enabled)
        self._command_line_enabled = enabled
        if hasattr(self, "settings_page") and self.settings_page is not None:
            self.settings_page.set_command_line_enabled(enabled)
        if hasattr(self, "command_page") and self.command_page is not None:
            self.command_page.setEnabled(enabled)
        if hasattr(self, "tab_widget") and hasattr(self, "_command_tab_index"):
            if hasattr(self.tab_widget, "setTabVisible"):
                self.tab_widget.setTabVisible(self._command_tab_index, enabled)
            else:
                self.tab_widget.setTabEnabled(self._command_tab_index, enabled)
            if not enabled and self.tab_widget.currentIndex() == self._command_tab_index:
                self.tab_widget.setCurrentWidget(self.video_page)
                self.current_page = self.video_page

    def _on_command_line_toggled(self, enabled: bool):
        self._s.setValue("command_line_enabled", bool(enabled))
        self._apply_command_line_enabled(enabled)
        state = self._tr("command_enabled") if enabled else self._tr("command_disabled")
        self.statusBar().showMessage(f"{self._tr('command_section')}{state}", 4000)

    def _on_bg_fill_mode_changed(self, mode: str):
        mode = mode if mode in {"stretch", "none", "fit", "cover"} else "cover"
        self._bg_fill_mode = mode
        self._s.setValue("bg_fill_mode", mode)
        self._bg.set_fill_mode(mode)

    def _on_bg_changed(self, path):
        # 用户手动选择了背景图片，若每日壁纸正在运行则自动关闭
        if path and self._daily_enabled:
            self._daily_enabled = False
            self._s.setValue("daily_wp", False)
            self._wallpaper_svc.stop()
            self.settings_page._daily_enabled = False
            self.settings_page._refresh_daily_ui()

        self._bg_path = path
        self._s.setValue("bg_path", path)
        self._bg.set_image(path)
        self._bg.set_fill_mode(self._bg_fill_mode)
        self._bg_colors = analyze_image_colors(path) if path else {}
        self._bg.set_bg_colors(self._bg_colors)
        self._apply_theme()
        # 记录用户手动选择的背景（每日壁纸不走这里，不会覆盖）
        self._user_bg_path = path
        self._s.setValue("user_bg_path", path)

    def _apply_theme(self):
        actual_mode = self._effective_theme_name()
        theme  = LIGHT_THEME if actual_mode == "light" else DARK_THEME
        has_bg = bool(self._bg_path and os.path.isfile(self._bg_path))
        ss     = build_stylesheet(theme, actual_mode, has_bg, self._bg_colors)
        self.setStyleSheet(ss)
        self._bg.set_dark(actual_mode == "dark")
        self._bg.set_mask_color(actual_mode == "dark")
        mode = actual_mode
        for pg in (self.video_page, self.audio_page,
                   self.image_page, self.m3u8_page,
                   self.command_page,
                   self.av_page):
            pg.set_theme(mode, self._bg_colors)
        self.settings_page.set_theme(self._theme_name, self._bg_colors, resolved_mode=actual_mode)


# ══════════════════════════════════════════════════════════════════
def run_app():
    app = QApplication(sys.argv)
    app.setApplicationName("格式转换通")
    app.setOrganizationName("Formix")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    run_app()
