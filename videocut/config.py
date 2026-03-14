from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


@dataclass(slots=True)
class PipelineConfig:
    llm_base_url: str = os.getenv("VIDEOCUT_LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str = os.getenv("VIDEOCUT_LLM_API_KEY", "")
    llm_model: str = os.getenv("VIDEOCUT_LLM_MODEL", "gpt-4o-mini")
    llm_timeout: int = int(os.getenv("VIDEOCUT_LLM_TIMEOUT", "120"))
    translation_batch_size: int = int(os.getenv("VIDEOCUT_TRANSLATION_BATCH_SIZE", "25"))

    tts_provider: str = os.getenv("VIDEOCUT_TTS_PROVIDER", "edge")
    tts_voice: str = os.getenv("VIDEOCUT_TTS_VOICE", "zh-CN-XiaoxiaoNeural")
    tts_rate: str = os.getenv("VIDEOCUT_TTS_RATE", "+0%")
    cosyvoice_python: str = os.getenv("VIDEOCUT_COSYVOICE_PYTHON", "python3.11")
    cosyvoice_repo_dir: str = os.getenv("VIDEOCUT_COSYVOICE_REPO_DIR", "")
    cosyvoice_model_dir: str = os.getenv("VIDEOCUT_COSYVOICE_MODEL_DIR", "")
    cosyvoice_mode: str = os.getenv("VIDEOCUT_COSYVOICE_MODE", "cross_lingual")
    reference_audio_path: str = os.getenv("VIDEOCUT_REFERENCE_AUDIO_PATH", "")
    reference_text: str = os.getenv("VIDEOCUT_REFERENCE_TEXT", "")

    dub_audio_volume: float = float(os.getenv("VIDEOCUT_DUB_AUDIO_VOLUME", "1.0"))
    original_audio_volume: float = float(os.getenv("VIDEOCUT_ORIGINAL_AUDIO_VOLUME", "0.0"))
    max_playback_rate: float = float(os.getenv("VIDEOCUT_MAX_PLAYBACK_RATE", "1.18"))
    max_segment_lag: float = float(os.getenv("VIDEOCUT_MAX_SEGMENT_LAG", "0.8"))
    min_segment_gap: float = float(os.getenv("VIDEOCUT_MIN_SEGMENT_GAP", "0.05"))
    max_opening_silence: float = float(os.getenv("VIDEOCUT_MAX_OPENING_SILENCE", "0.35"))
    max_global_shift: float = float(os.getenv("VIDEOCUT_MAX_GLOBAL_SHIFT", "1.5"))

    burn_subtitles: bool = os.getenv("VIDEOCUT_BURN_SUBTITLES", "1") != "0"
    subtitle_font: str = os.getenv("VIDEOCUT_SUBTITLE_FONT", "Arial Unicode MS")
    subtitle_font_size: int = int(os.getenv("VIDEOCUT_SUBTITLE_FONT_SIZE", "18"))

    asr_model: str = os.getenv("VIDEOCUT_ASR_MODEL", "small")
    asr_device: str = os.getenv("VIDEOCUT_ASR_DEVICE", "auto")
    asr_compute_type: str = os.getenv("VIDEOCUT_ASR_COMPUTE_TYPE", "int8")

    runs_dir: Path = Path("runs")
    output_name: str = "final_cn.mp4"
