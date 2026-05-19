# format_factory/gpu.py
"""
GPU 编码器参数注入逻辑（纯表驱动，无硬件检测）。

将 CPU 编码器参数替换为对应 GPU 编码器（NVENC / AMF / QSV），
不支持时自动回退并给出理由。
"""
from format_factory.gui.settings_page import GPU_ENCODERS
from format_factory.i18n import tr

_CPU_TO_ROLE = {
    "libx264": "h264", "libx265": "hevc",
    "libxvid": "h264", "flv1": "h264",
}

def apply_gpu_args(base_args: list, vendor: str, output_fmt: str, language: str = "auto") -> tuple:
    if vendor == "none" or output_fmt == "gif":
        return base_args, ""
    enc = GPU_ENCODERS.get(vendor, {})
    if not enc.get("h264"):
        return base_args, ""
    supported = enc.get("supported_roles", {"h264", "hevc"})
    result = list(base_args)
    replaced = False
    i = 0
    while i < len(result):
        if result[i] in ("-c:v", "-vcodec") and i+1 < len(result):
            role = _CPU_TO_ROLE.get(result[i+1])
            if role is None:
                return base_args, tr(language, "gpu_apply_unsupported_codec", codec=result[i+1])
            if role not in supported:
                return base_args, tr(language, "gpu_apply_unsupported_role", role=role.upper())
            gpu_codec = enc.get(role)
            if gpu_codec:
                result[i+1] = gpu_codec
                replaced = True
                i += 2; continue
        i += 1
    if not replaced:
        if any(a in base_args for a in ("-preset", "-crf", "-q:v")):
            if "h264" in supported:
                result = ["-c:v", enc["h264"]] + result
                replaced = True
    if replaced and enc.get("extra"):
        for j, tok in enumerate(result):
            if tok in ("-c:v", "-vcodec") and j+1 < len(result):
                result = result[:j+2] + enc["extra"] + result[j+2:]
                break
    if replaced:
        filtered = []
        k = 0
        while k < len(result):
            tok = result[k]
            if tok in {"-preset", "-crf"} and k + 1 < len(result):
                k += 2; continue
            if tok == "-q:v" and k + 1 < len(result):
                k += 2; continue
            filtered.append(tok)
            k += 1
        result = filtered
    if replaced and "-pix_fmt" not in result:
        result = result + ["-pix_fmt", "yuv420p"]
    return result, ""
