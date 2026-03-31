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
    export_platform_materials: bool = os.getenv("VIDEOCUT_EXPORT_PLATFORM_MATERIALS", "1") != "0"
    cleanup_source_after_publish: bool = os.getenv("VIDEOCUT_CLEANUP_SOURCE_AFTER_PUBLISH", "1") != "0"

    llm_base_url: str = os.getenv("VIDEOCUT_LLM_BASE_URL", "http://localhost:1234/v1")
    llm_api_key: str = os.getenv("VIDEOCUT_LLM_API_KEY", "")
    llm_model: str = os.getenv("VIDEOCUT_LLM_MODEL", "translategemma-4b-it-mlx-4bit")
    llm_timeout: int = int(os.getenv("VIDEOCUT_LLM_TIMEOUT", "120"))
    translation_batch_size: int = int(os.getenv("VIDEOCUT_TRANSLATION_BATCH_SIZE", "10"))
    translation_concurrency: int = int(os.getenv("VIDEOCUT_TRANSLATION_CONCURRENCY", "1"))
    translation_target_cps: float = float(os.getenv("VIDEOCUT_TRANSLATION_TARGET_CPS", "4.5"))
    translation_char_tolerance: float = float(os.getenv("VIDEOCUT_TRANSLATION_CHAR_TOLERANCE", "0.2"))
    protected_terms_path: str = os.getenv(
        "VIDEOCUT_PROTECTED_TERMS_PATH",
        str(_repo_root() / "translation_protected_terms.txt"),
    )

    cosyvoice_python: str = os.getenv("VIDEOCUT_COSYVOICE_PYTHON", _default_cosyvoice_python())
    cosyvoice_repo_dir: str = os.getenv("VIDEOCUT_COSYVOICE_REPO_DIR", "")
    cosyvoice_model_dir: str = os.getenv("VIDEOCUT_COSYVOICE_MODEL_DIR", "")
    cosyvoice_mode: str = os.getenv("VIDEOCUT_COSYVOICE_MODE", "cross_lingual")
    cosyvoice_group_size: int = int(os.getenv("VIDEOCUT_COSYVOICE_GROUP_SIZE", "1"))
    cosyvoice_concurrency: int = int(os.getenv("VIDEOCUT_COSYVOICE_CONCURRENCY", "1"))
    reference_audio_path: str = os.getenv("VIDEOCUT_REFERENCE_AUDIO_PATH", "")
    reference_text: str = os.getenv("VIDEOCUT_REFERENCE_TEXT", "")

    dub_audio_volume: float = float(os.getenv("VIDEOCUT_DUB_AUDIO_VOLUME", "1.0"))
    original_audio_volume: float = float(os.getenv("VIDEOCUT_ORIGINAL_AUDIO_VOLUME", "0.0"))

    burn_subtitles: bool = os.getenv("VIDEOCUT_BURN_SUBTITLES", "1") != "0"
    subtitle_font: str = os.getenv("VIDEOCUT_SUBTITLE_FONT", "Arial Unicode MS")
    subtitle_font_path: str = os.getenv("VIDEOCUT_SUBTITLE_FONT_PATH", "")
    subtitle_font_size: int = int(os.getenv("VIDEOCUT_SUBTITLE_FONT_SIZE", "18"))
    subtitle_overlay_concurrency: int = int(os.getenv("VIDEOCUT_SUBTITLE_OVERLAY_CONCURRENCY", "4"))
    video_preset: str = os.getenv("VIDEOCUT_VIDEO_PRESET", "medium")
    video_crf: int = int(os.getenv("VIDEOCUT_VIDEO_CRF", "20"))

    runs_dir: Path = Path("runs")
    output_name: str = "final_cn.mp4"


DEFAULT_CONFIG_PATH = Path("videocut.toml")

_SECTION_FIELD_MAP: dict[str, dict[str, str]] = {
    "pipeline": {
        "export_platform_materials": "export_platform_materials",
        "cleanup_source_after_publish": "cleanup_source_after_publish",
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
        "target_cps": "translation_target_cps",
        "char_tolerance": "translation_char_tolerance",
        "protected_terms_path": "protected_terms_path",
    },
    "cosyvoice": {
        "python": "cosyvoice_python",
        "repo_dir": "cosyvoice_repo_dir",
        "model_dir": "cosyvoice_model_dir",
        "mode": "cosyvoice_mode",
        "group_size": "cosyvoice_group_size",
        "concurrency": "cosyvoice_concurrency",
        "reference_audio_path": "reference_audio_path",
        "reference_text": "reference_text",
    },
    "audio": {
        "dub_volume": "dub_audio_volume",
        "original_volume": "original_audio_volume",
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
