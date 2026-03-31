from __future__ import annotations

from videocut.models import Segment


# Natural Chinese speech rate used for translation budget (~4.5 chars/sec).
# Playback rate range: 1.0x preferred, up to max_playback_rate when audio
# overflows the slot. Segments are always aligned to their original subtitle
# timestamps (render_start = segment.start, render_end = segment.end).
# No overlap: audio that still exceeds the slot at max rate is trimmed by
# compose_dubbed_track via the atrim filter.

def schedule_dubbing_timing(
    segments: list[Segment],
    max_playback_rate: float = 1.3,
) -> None:
    """Assign playback_rate to each segment.

    - Natural speed (1.0x) when synthetic audio fits within the subtitle slot.
    - Capped at max_playback_rate when it overflows (audio trimmed, no overlap).
    - scheduled_start/end are left as None so render_start/render_end fall back
      to the original segment.start/end, preserving alignment with the source.
    """
    for segment in segments:
        if segment.synthetic_duration is None:
            segment.playback_rate = 1.0
            continue
        slot = segment.duration
        if segment.synthetic_duration <= slot:
            segment.playback_rate = 1.0
        else:
            segment.playback_rate = min(max_playback_rate, segment.synthetic_duration / slot)
        # Keep scheduled_start/end as None — render_start/end fall back to
        # segment.start/end, perfectly aligned with the original video.
        segment.scheduled_start = None
        segment.scheduled_end = None
