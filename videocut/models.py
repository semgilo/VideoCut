from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class Segment:
    index: int
    start: float
    end: float
    english: str
    chinese: str = ""
    audio_path: Path | None = None
    synthetic_duration: float | None = None
    leading_silence: float = 0.0
    trailing_silence: float = 0.0
    scheduled_start: float | None = None
    scheduled_end: float | None = None
    playback_rate: float = 1.0

    @property
    def duration(self) -> float:
        return max(0.01, self.end - self.start)

    @property
    def render_start(self) -> float:
        return self.start if self.scheduled_start is None else self.scheduled_start

    @property
    def render_end(self) -> float:
        return self.end if self.scheduled_end is None else self.scheduled_end

    @property
    def render_duration(self) -> float:
        return max(0.01, self.render_end - self.render_start)


@dataclass(slots=True)
class VideoMetadata:
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    uploader: str = ""
    channel: str = ""
    video_id: str = ""
    webpage_url: str = ""
    upload_date: str = ""
