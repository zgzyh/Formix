# format_factory/gui/video_converter.py
import os
from .base_page import BaseConverterPage
from ..config import VIDEO_INPUT_EXTENSIONS
from ..i18n import tr


class VideoConverterPage(BaseConverterPage):
    def __init__(self, ffmpeg_handler, parent=None):
        super().__init__('video', ffmpeg_handler, parent)

    def _get_file_filter(self):
        video_label = tr(self._language, "file_filter_video")
        all_label = tr(self._language, "file_filter_all")
        patterns = " ".join(f"*.{ext}" for ext in VIDEO_INPUT_EXTENSIONS)
        return f"{video_label} ({patterns});;{all_label} (*.*)"

    def _start_conversion_process(self):
        if not self.ffmpeg_handler:
            self.log_message(tr(self._language, "ffmpeg_not_found_download"), "error")
            self.start_conversion_button.setEnabled(True)
            self.cancel_conversion_button.setEnabled(False)
            return

        output_format = self.output_format_combo.currentText()
        args = self._build_args(output_format)
        for i, input_path in enumerate(self.input_files):
            name = os.path.splitext(os.path.basename(input_path))[0]
            self.log_message(f"[{i+1}] {os.path.basename(input_path)}  →  .{output_format}", "info")
            if output_format == "m3u8":
                # HLS：把 %03d.ts 标记传出去，_submit_next 里处理实际路径
                hls_args = list(args)
                self.conversion_requested.emit(i, input_path, hls_args, name)
            else:
                self.conversion_requested.emit(i, input_path, args, name)
