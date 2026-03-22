from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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
    llm_base_url: str = os.getenv("VIDEOCUT_LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str = os.getenv("VIDEOCUT_LLM_API_KEY", "")
    llm_model: str = os.getenv("VIDEOCUT_LLM_MODEL", "gpt-4o-mini")
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
