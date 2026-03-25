from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


load_dotenv()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_cosyvoice_python() -> str:
    bundled_venv_python = _repo_root() / ".venv-cosyvoice" / "bin" / "python"
    if bundled_venv_python.exists():
        return str(bundled_venv_python)
    return "python3.11"


@dataclass(slots=True)
class PipelineConfig:
    pipeline_mode: str = os.getenv("VIDEOCUT_PIPELINE_MODE", "dub")
    translation_backend: str = os.getenv("VIDEOCUT_TRANSLATION_BACKEND", "auto")
    export_platform_materials: bool = os.getenv("VIDEOCUT_EXPORT_PLATFORM_MATERIALS", "1") != "0"

    llm_base_url: str = os.getenv("VIDEOCUT_LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str = os.getenv("VIDEOCUT_LLM_API_KEY", "")
    llm_model: str = os.getenv("VIDEOCUT_LLM_MODEL", "translategemma-4b-it-mlx-4bit")
    llm_timeout: int = int(os.getenv("VIDEOCUT_LLM_TIMEOUT", "120"))
    translation_batch_size: int = int(os.getenv("VIDEOCUT_TRANSLATION_BATCH_SIZE", "25"))
    translation_concurrency: int = int(os.getenv("VIDEOCUT_TRANSLATION_CONCURRENCY", "1"))
    translation_timing_adapt: bool = os.getenv("VIDEOCUT_TRANSLATION_TIMING_ADAPT", "0") != "0"
    translation_target_compact_cps: float = float(
        os.getenv("VIDEOCUT_TRANSLATION_TARGET_COMPACT_CPS", "4.6")
    )
    translation_adapt_slack_chars: int = int(
        os.getenv("VIDEOCUT_TRANSLATION_ADAPT_SLACK_CHARS", "2")
    )
    translation_adapt_passes: int = int(
        os.getenv("VIDEOCUT_TRANSLATION_ADAPT_PASSES", "2")
    )
    translation_adapt_min_chars: int = int(
        os.getenv("VIDEOCUT_TRANSLATION_ADAPT_MIN_CHARS", "4")
    )
    translation_audio_repair: bool = os.getenv("VIDEOCUT_TRANSLATION_AUDIO_REPAIR", "1") != "0"
    translation_audio_target_playback_rate: float = float(
        os.getenv("VIDEOCUT_TRANSLATION_AUDIO_TARGET_PLAYBACK_RATE", "1.0")
    )
    translation_audio_repair_slack_seconds: float = float(
        os.getenv("VIDEOCUT_TRANSLATION_AUDIO_REPAIR_SLACK_SECONDS", "0.05")
    )
    translation_audio_repair_passes: int = int(
        os.getenv("VIDEOCUT_TRANSLATION_AUDIO_REPAIR_PASSES", "2")
    )
    translation_audio_repair_group_size: int = int(
        os.getenv("VIDEOCUT_TRANSLATION_AUDIO_REPAIR_GROUP_SIZE", "1")
    )
    protected_terms_path: str = os.getenv(
        "VIDEOCUT_PROTECTED_TERMS_PATH",
        str(_repo_root() / "translation_protected_terms.txt"),
    )

    tts_provider: str = os.getenv("VIDEOCUT_TTS_PROVIDER", "cosyvoice")
    tts_voice: str = os.getenv("VIDEOCUT_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
    tts_rate: str = os.getenv("VIDEOCUT_TTS_RATE", "+0%")
    tts_command: str = os.getenv("VIDEOCUT_TTS_COMMAND", "")
    tts_command_audio_format: str = os.getenv("VIDEOCUT_TTS_COMMAND_AUDIO_FORMAT", "wav")
    minimax_base_url: str = os.getenv("VIDEOCUT_MINIMAX_BASE_URL", "https://api.minimax.io")
    minimax_api_key: str = os.getenv("VIDEOCUT_MINIMAX_API_KEY", "")
    minimax_model: str = os.getenv("VIDEOCUT_MINIMAX_MODEL", "speech-2.8-turbo")
    minimax_voice_id: str = os.getenv(
        "VIDEOCUT_MINIMAX_VOICE_ID",
        "Chinese (Mandarin)_News_Anchor",
    )
    minimax_speed: float = float(os.getenv("VIDEOCUT_MINIMAX_SPEED", "1.0"))
    minimax_volume: float = float(os.getenv("VIDEOCUT_MINIMAX_VOLUME", "1.0"))
    minimax_pitch: float = float(os.getenv("VIDEOCUT_MINIMAX_PITCH", "0"))
    minimax_concurrency: int = int(os.getenv("VIDEOCUT_MINIMAX_CONCURRENCY", "4"))
    minimax_audio_format: str = os.getenv("VIDEOCUT_MINIMAX_AUDIO_FORMAT", "mp3")
    minimax_sample_rate: int = int(os.getenv("VIDEOCUT_MINIMAX_SAMPLE_RATE", "32000"))
    minimax_bitrate: int = int(os.getenv("VIDEOCUT_MINIMAX_BITRATE", "128000"))
    minimax_language_boost: str = os.getenv("VIDEOCUT_MINIMAX_LANGUAGE_BOOST", "Chinese")
    minimax_voice_clone: bool = os.getenv("VIDEOCUT_MINIMAX_VOICE_CLONE", "0") != "0"
    minimax_timeout: int = int(os.getenv("VIDEOCUT_MINIMAX_TIMEOUT", "180"))
    cosyvoice_python: str = os.getenv("VIDEOCUT_COSYVOICE_PYTHON", _default_cosyvoice_python())
    cosyvoice_repo_dir: str = os.getenv("VIDEOCUT_COSYVOICE_REPO_DIR", "")
    cosyvoice_model_dir: str = os.getenv("VIDEOCUT_COSYVOICE_MODEL_DIR", "")
    cosyvoice_mode: str = os.getenv("VIDEOCUT_COSYVOICE_MODE", "cross_lingual")
    cosyvoice_group_size: int = int(os.getenv("VIDEOCUT_COSYVOICE_GROUP_SIZE", "1"))
    reference_audio_path: str = os.getenv("VIDEOCUT_REFERENCE_AUDIO_PATH", "")
    reference_text: str = os.getenv("VIDEOCUT_REFERENCE_TEXT", "")

    dub_audio_volume: float = float(os.getenv("VIDEOCUT_DUB_AUDIO_VOLUME", "1.0"))
    original_audio_volume: float = float(os.getenv("VIDEOCUT_ORIGINAL_AUDIO_VOLUME", "0.0"))
    timing_mode: str = os.getenv("VIDEOCUT_TIMING_MODE", "natural")
    min_playback_rate: float = float(os.getenv("VIDEOCUT_MIN_PLAYBACK_RATE", "0.6"))
    max_playback_rate: float = float(os.getenv("VIDEOCUT_MAX_PLAYBACK_RATE", "1.18"))
    max_segment_lag: float = float(os.getenv("VIDEOCUT_MAX_SEGMENT_LAG", "0.8"))
    min_segment_gap: float = float(os.getenv("VIDEOCUT_MIN_SEGMENT_GAP", "0.05"))
    max_opening_silence: float = float(os.getenv("VIDEOCUT_MAX_OPENING_SILENCE", "0.35"))
    max_global_shift: float = float(os.getenv("VIDEOCUT_MAX_GLOBAL_SHIFT", "2.5"))
    trim_tts_silence: bool = os.getenv("VIDEOCUT_TRIM_TTS_SILENCE", "1") != "0"
    tts_silence_threshold_db: float = float(os.getenv("VIDEOCUT_TTS_SILENCE_THRESHOLD_DB", "-35"))
    tts_silence_min_duration: float = float(os.getenv("VIDEOCUT_TTS_SILENCE_MIN_DURATION", "0.05"))
    tts_keep_silence: float = float(os.getenv("VIDEOCUT_TTS_KEEP_SILENCE", "0.02"))

    burn_subtitles: bool = os.getenv("VIDEOCUT_BURN_SUBTITLES", "1") != "0"
    subtitle_font: str = os.getenv("VIDEOCUT_SUBTITLE_FONT", "Arial Unicode MS")
    subtitle_font_path: str = os.getenv("VIDEOCUT_SUBTITLE_FONT_PATH", "")
    subtitle_font_size: int = int(os.getenv("VIDEOCUT_SUBTITLE_FONT_SIZE", "18"))
    subtitle_overlay_concurrency: int = int(os.getenv("VIDEOCUT_SUBTITLE_OVERLAY_CONCURRENCY", "4"))
    video_preset: str = os.getenv("VIDEOCUT_VIDEO_PRESET", "medium")
    video_crf: int = int(os.getenv("VIDEOCUT_VIDEO_CRF", "20"))

    asr_model: str = os.getenv("VIDEOCUT_ASR_MODEL", "small")
    asr_device: str = os.getenv("VIDEOCUT_ASR_DEVICE", "auto")
    asr_compute_type: str = os.getenv("VIDEOCUT_ASR_COMPUTE_TYPE", "int8")

    runs_dir: Path = Path("runs")
    output_name: str = "final_cn.mp4"


DEFAULT_CONFIG_PATH = Path("videocut.toml")

_SECTION_FIELD_MAP: dict[str, dict[str, str]] = {
    "pipeline": {
        "mode": "pipeline_mode",
        "translation_backend": "translation_backend",
        "export_platform_materials": "export_platform_materials",
        "output_name": "output_name",
        "runs_dir": "runs_dir",
    },
    "translation": {
        "llm_base_url": "llm_base_url",
        "llm_api_key": "llm_api_key",
        "llm_model": "llm_model",
        "llm_timeout": "llm_timeout",
        "batch_size": "translation_batch_size",
        "concurrency": "translation_concurrency",
        "timing_adapt": "translation_timing_adapt",
        "target_compact_cps": "translation_target_compact_cps",
        "adapt_slack_chars": "translation_adapt_slack_chars",
        "adapt_passes": "translation_adapt_passes",
        "adapt_min_chars": "translation_adapt_min_chars",
        "audio_repair": "translation_audio_repair",
        "audio_target_playback_rate": "translation_audio_target_playback_rate",
        "audio_repair_slack_seconds": "translation_audio_repair_slack_seconds",
        "audio_repair_passes": "translation_audio_repair_passes",
        "audio_repair_group_size": "translation_audio_repair_group_size",
        "protected_terms_path": "protected_terms_path",
    },
    "tts": {
        "provider": "tts_provider",
        "voice": "tts_voice",
        "rate": "tts_rate",
        "command": "tts_command",
        "command_audio_format": "tts_command_audio_format",
    },
    "minimax": {
        "base_url": "minimax_base_url",
        "api_key": "minimax_api_key",
        "model": "minimax_model",
        "voice_id": "minimax_voice_id",
        "speed": "minimax_speed",
        "volume": "minimax_volume",
        "pitch": "minimax_pitch",
        "concurrency": "minimax_concurrency",
        "audio_format": "minimax_audio_format",
        "sample_rate": "minimax_sample_rate",
        "bitrate": "minimax_bitrate",
        "language_boost": "minimax_language_boost",
        "voice_clone": "minimax_voice_clone",
        "timeout": "minimax_timeout",
    },
    "cosyvoice": {
        "python": "cosyvoice_python",
        "repo_dir": "cosyvoice_repo_dir",
        "model_dir": "cosyvoice_model_dir",
        "mode": "cosyvoice_mode",
        "group_size": "cosyvoice_group_size",
        "reference_audio_path": "reference_audio_path",
        "reference_text": "reference_text",
    },
    "audio": {
        "dub_volume": "dub_audio_volume",
        "original_volume": "original_audio_volume",
    },
    "timing": {
        "mode": "timing_mode",
        "min_playback_rate": "min_playback_rate",
        "max_playback_rate": "max_playback_rate",
        "max_segment_lag": "max_segment_lag",
        "min_segment_gap": "min_segment_gap",
        "max_opening_silence": "max_opening_silence",
        "max_global_shift": "max_global_shift",
        "trim_tts_silence": "trim_tts_silence",
        "tts_silence_threshold_db": "tts_silence_threshold_db",
        "tts_silence_min_duration": "tts_silence_min_duration",
        "tts_keep_silence": "tts_keep_silence",
    },
    "subtitles": {
        "burn": "burn_subtitles",
        "font": "subtitle_font",
        "font_path": "subtitle_font_path",
        "font_size": "subtitle_font_size",
        "overlay_concurrency": "subtitle_overlay_concurrency",
    },
    "video": {
        "preset": "video_preset",
        "crf": "video_crf",
    },
    "asr": {
        "model": "asr_model",
        "device": "asr_device",
        "compute_type": "asr_compute_type",
    },
}


def load_pipeline_config(config_path: Path | None = None) -> PipelineConfig:
    config = PipelineConfig()
    resolved = _resolve_config_path(config_path)
    if resolved is None:
        return config
    payload = tomllib.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid config file: {resolved}")
    _apply_config_payload(config, payload)
    return config


def _resolve_config_path(config_path: Path | None) -> Path | None:
    if config_path is not None:
        candidate = config_path.expanduser().resolve()
        if not candidate.exists():
            raise FileNotFoundError(f"Config file does not exist: {candidate}")
        return candidate
    candidate = DEFAULT_CONFIG_PATH.resolve()
    if candidate.exists():
        return candidate
    return None


def _apply_config_payload(config: PipelineConfig, payload: dict[str, Any]) -> None:
    for section_name, mapping in _SECTION_FIELD_MAP.items():
        section_payload = payload.get(section_name)
        if not isinstance(section_payload, dict):
            continue
        for config_key, field_name in mapping.items():
            if config_key not in section_payload:
                continue
            setattr(config, field_name, _coerce_config_value(field_name, section_payload[config_key]))


def _coerce_config_value(field_name: str, value: Any) -> Any:
    if field_name == "runs_dir":
        return Path(str(value)).expanduser()
    if field_name.endswith("_path") or field_name.endswith("_dir"):
        text = str(value).strip()
        if not text:
            return ""
        return str(Path(text).expanduser())
    return value
