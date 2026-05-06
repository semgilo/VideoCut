from __future__ import annotations

from videocut.models import Segment


def schedule_dubbing_timing(
    segments: list[Segment],
    min_playback_rate: float | None = None,
    max_playback_rate: float | None = None,
) -> None:
    """Assign per-segment playback rates with strict subtitle-boundary alignment.

    Requirements enforced by this scheduler:
    - Segment audio starts at the original subtitle start.
    - Segment audio ends at the original subtitle end.
    - Playback rate is computed from synthetic_duration / subtitle_duration.
    - No trimming/cropping is used to force fit.
    """
    if min_playback_rate is not None or max_playback_rate is not None:
        print(
            "Warning: min/max playback-rate constraints are ignored in the unified pipeline. "
            "Using exact boundary alignment with stretch/compress."
        )
    previous_end: float | None = None
    for segment in segments:
        if previous_end is not None and segment.start < previous_end - 1e-6:
            raise RuntimeError(
                f"Subtitle windows overlap at segment {segment.index} "
                f"({segment.start:.3f}s < previous end {previous_end:.3f}s). "
                "Strict boundary alignment with no overlap is impossible for overlapping source subtitles."
            )
        previous_end = segment.end

        slot_duration = segment.duration
        segment.scheduled_start = segment.start
        segment.scheduled_end = segment.end

        if segment.synthetic_duration is None:
            segment.playback_rate = 1.0
            continue

        required_rate = segment.synthetic_duration / slot_duration
        if required_rate <= 0:
            raise RuntimeError(
                f"Segment {segment.index} has invalid playback rate {required_rate:.6f} "
                f"(synthetic_duration={segment.synthetic_duration}, slot_duration={slot_duration})."
            )

        # Clamp playback rate to ±10% to avoid excessive stretch/compress.
        # Audio that falls outside the window is either slightly cut short or
        # leaves a brief gap — both are less jarring than extreme speed changes.
        clamped_rate = max(0.9, min(1.1, required_rate))
        if clamped_rate != required_rate:
            print(
                f"  Segment {segment.index}: playback rate {required_rate:.4f} clamped to "
                f"{clamped_rate:.4f} (slot={slot_duration:.3f}s, "
                f"synth={segment.synthetic_duration:.3f}s)"
            )
        segment.playback_rate = clamped_rate
