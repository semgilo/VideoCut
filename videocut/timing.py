from __future__ import annotations

from videocut.models import Segment


def plan_dubbing_timing(
    segments: list[Segment],
    video_duration: float,
    max_opening_silence: float,
    max_global_shift: float,
    min_segment_gap: float,
    max_playback_rate: float,
    max_segment_lag: float,
) -> None:
    if not segments:
        return

    anchor_shift = _compute_anchor_shift(
        segments=segments,
        max_opening_silence=max_opening_silence,
        max_global_shift=max_global_shift,
    )
    first_anchor = max(segments[0].start - anchor_shift, 0.0)
    total_natural_duration = sum(max(0.01, segment.synthetic_duration or 0.01) for segment in segments)
    total_gap_duration = min_segment_gap * max(0, len(segments) - 1)
    available_speech_window = max(0.01, video_duration - first_anchor - total_gap_duration)
    required_base_rate = max(1.0, total_natural_duration / available_speech_window)
    if required_base_rate > max_playback_rate + 0.001:
        overflow_seconds = (total_natural_duration / max_playback_rate) - available_speech_window
        raise RuntimeError(
            "Dub timing is infeasible with the current natural-speed limit. "
            f"Even at {max_playback_rate:.2f}x, the dubbing would exceed the video by about "
            f"{overflow_seconds:.2f}s. Shorten translations, use a faster voice, increase "
            "VIDEOCUT_MAX_PLAYBACK_RATE, or allow more timeline shift."
        )
    base_playback_rate = required_base_rate
    previous_end = 0.0

    for index, segment in enumerate(segments):
        if segment.synthetic_duration is None:
            raise RuntimeError(f"Segment {segment.index} is missing synthesized duration")

        next_anchor = video_duration
        if index + 1 < len(segments):
            next_anchor = max(segments[index + 1].start - anchor_shift, 0.0)

        anchor_start = max(segment.start - anchor_shift, 0.0)
        earliest_start = max(0.0, previous_end + min_segment_gap)
        scheduled_start = max(anchor_start, earliest_start)
        if scheduled_start >= video_duration:
            scheduled_start = max(0.0, video_duration - 0.01)

        playback_rate = base_playback_rate
        render_duration = max(0.01, segment.synthetic_duration / playback_rate)
        target_end = min(video_duration, next_anchor + max_segment_lag)
        natural_end = scheduled_start + render_duration

        if natural_end > target_end and target_end > scheduled_start + 0.01:
            required_rate = segment.synthetic_duration / max(target_end - scheduled_start, 0.01)
            playback_rate = min(max_playback_rate, max(base_playback_rate, required_rate))
            render_duration = max(0.01, segment.synthetic_duration / playback_rate)

        scheduled_end = scheduled_start + render_duration
        if scheduled_end > video_duration and scheduled_start < video_duration:
            remaining = max(0.01, video_duration - scheduled_start)
            required_rate = segment.synthetic_duration / remaining
            if required_rate <= max_playback_rate:
                playback_rate = max(playback_rate, required_rate)
                render_duration = max(0.01, segment.synthetic_duration / playback_rate)
                scheduled_end = min(video_duration, scheduled_start + render_duration)
            else:
                scheduled_end = video_duration

        segment.playback_rate = playback_rate
        segment.scheduled_start = scheduled_start
        segment.scheduled_end = max(scheduled_start + 0.01, scheduled_end)
        previous_end = segment.scheduled_end


def _compute_anchor_shift(
    segments: list[Segment],
    max_opening_silence: float,
    max_global_shift: float,
) -> float:
    first_start = segments[0].start
    if first_start <= max_opening_silence:
        return 0.0
    return min(max_global_shift, max(0.0, first_start - max_opening_silence))
