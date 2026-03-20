from __future__ import annotations

from collections.abc import Iterable

from videocut.models import Segment


def plan_dubbing_timing(
    segments: list[Segment],
    video_duration: float,
    timing_mode: str,
    max_opening_silence: float,
    max_global_shift: float,
    min_segment_gap: float,
    min_playback_rate: float,
    max_playback_rate: float,
    max_segment_lag: float,
) -> None:
    if not segments:
        return
    if min_playback_rate <= 0:
        raise ValueError(f"min_playback_rate must be positive. Received {min_playback_rate!r}")
    if max_playback_rate < min_playback_rate:
        raise ValueError(
            "max_playback_rate must be greater than or equal to min_playback_rate. "
            f"Received {min_playback_rate!r}..{max_playback_rate!r}"
        )
    if timing_mode not in {"natural", "fit"}:
        raise ValueError(f"Unsupported timing_mode: {timing_mode!r}")

    anchor_shift = _compute_anchor_shift(
        segments=segments,
        max_opening_silence=max_opening_silence,
        max_global_shift=max_global_shift,
    )
    first_anchor = max(segments[0].start - anchor_shift, 0.0)
    total_natural_duration = sum(max(0.01, segment.synthetic_duration or 0.01) for segment in segments)
    total_gap_duration = min_segment_gap * max(0, len(segments) - 1)
    available_speech_window = max(0.01, video_duration - first_anchor - total_gap_duration)
    required_base_rate = total_natural_duration / available_speech_window
    if required_base_rate > max_playback_rate + 0.001:
        overflow_seconds = (total_natural_duration / max_playback_rate) - available_speech_window
        raise RuntimeError(
            "Dub timing is infeasible with the current natural-speed limit. "
            f"Even at {max_playback_rate:.2f}x, the dubbing would exceed the video by about "
            f"{overflow_seconds:.2f}s. Shorten translations, use a faster voice, increase "
            "VIDEOCUT_MAX_PLAYBACK_RATE, or allow more timeline shift."
        )
    if timing_mode == "fit":
        base_playback_rate = max(min_playback_rate, required_base_rate)
    else:
        base_playback_rate = max(1.0, required_base_rate)
    previous_end = 0.0
    synthetic_durations = [max(0.01, segment.synthetic_duration or 0.01) for segment in segments]
    remaining_natural_duration = [0.0] * (len(segments) + 1)
    for index in range(len(segments) - 1, -1, -1):
        remaining_natural_duration[index] = remaining_natural_duration[index + 1] + synthetic_durations[index]

    for index, segment in enumerate(segments):
        synthetic_duration = synthetic_durations[index]

        next_anchor = video_duration
        if index + 1 < len(segments):
            next_anchor = max(segments[index + 1].start - anchor_shift, 0.0)

        anchor_start = max(segment.start - anchor_shift, 0.0)
        earliest_start = max(0.0, previous_end + min_segment_gap)
        scheduled_start = max(anchor_start, earliest_start)
        if scheduled_start >= video_duration:
            scheduled_start = max(0.0, video_duration - 0.01)

        target_end = min(video_duration, next_anchor + max_segment_lag)
        min_possible_duration = synthetic_duration / max_playback_rate
        max_possible_duration = synthetic_duration / min_playback_rate
        remaining_segments = len(segments) - index - 1
        remaining_gap_duration = min_segment_gap * max(0, remaining_segments)
        latest_safe_end = (
            video_duration
            - remaining_gap_duration
            - (remaining_natural_duration[index + 1] / max_playback_rate)
        )
        max_allowed_duration = max(
            0.01,
            min(
                max(0.01, target_end - scheduled_start),
                max(0.01, latest_safe_end - scheduled_start),
                max(0.01, video_duration - scheduled_start),
                max_possible_duration,
            ),
        )
        if max_allowed_duration + 0.001 < min_possible_duration:
            required_rate = synthetic_duration / max(max_allowed_duration, 0.01)
            raise RuntimeError(
                "Dub timing is infeasible for the current subtitle layout. "
                f"Segment {segment.index} would need {required_rate:.2f}x playback, which exceeds "
                f"the configured maximum of {max_playback_rate:.2f}x. Shorten the translation, "
                "switch to a faster voice, raise the playback-rate ceiling, or relax the lag constraints."
            )

        if timing_mode == "fit":
            target_duration = segment.duration
        else:
            target_duration = synthetic_duration / base_playback_rate
        render_duration = _clamp(
            target_duration,
            min_possible_duration,
            max_allowed_duration,
        )
        playback_rate = synthetic_duration / render_duration
        scheduled_end = min(video_duration, scheduled_start + render_duration)

        segment.playback_rate = playback_rate
        segment.scheduled_start = scheduled_start
        segment.scheduled_end = max(scheduled_start + 0.01, scheduled_end)
        previous_end = segment.scheduled_end


def validate_source_segment_coverage(
    source_segments: list[Segment],
    target_segments: list[Segment],
    max_uncovered_duration: float = 0.35,
) -> None:
    if max_uncovered_duration < 0:
        raise ValueError(
            "max_uncovered_duration must be non-negative. "
            f"Received {max_uncovered_duration!r}"
        )

    source_intervals = _merge_intervals(
        (segment.start, segment.end)
        for segment in source_segments
        if segment.end > segment.start
    )
    target_intervals = _merge_intervals(
        (segment.start, segment.end)
        for segment in target_segments
        if segment.end > segment.start
    )
    if not source_intervals or not target_intervals:
        return

    uncovered = _subtract_intervals(source_intervals, target_intervals)
    significant_gaps = [
        (start, end)
        for start, end in uncovered
        if end - start > max_uncovered_duration + 0.001
    ]
    if not significant_gaps:
        return

    gap_start, gap_end = significant_gaps[0]
    raise RuntimeError(
        "Segment coverage validation failed. "
        f"Source speech from {gap_start:.2f}s to {gap_end:.2f}s "
        f"({gap_end - gap_start:.2f}s) is not covered by the current dubbing layout. "
        "Add or widen a segment before exporting."
    )


def _compute_anchor_shift(
    segments: list[Segment],
    max_opening_silence: float,
    max_global_shift: float,
) -> float:
    first_start = segments[0].start
    if first_start <= max_opening_silence:
        return 0.0
    return min(max_global_shift, max(0.0, first_start - max_opening_silence))


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _merge_intervals(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    normalized = sorted(
        (max(0.0, float(start)), max(0.0, float(end)))
        for start, end in intervals
        if float(end) > float(start)
    )
    if not normalized:
        return []

    merged: list[list[float]] = [[normalized[0][0], normalized[0][1]]]
    for start, end in normalized[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end + 0.001:
            merged[-1][1] = max(previous_end, end)
            continue
        merged.append([start, end])
    return [(start, end) for start, end in merged]


def _subtract_intervals(
    source_intervals: list[tuple[float, float]],
    target_intervals: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    uncovered: list[tuple[float, float]] = []
    target_index = 0

    for source_start, source_end in source_intervals:
        cursor = source_start

        while target_index < len(target_intervals) and target_intervals[target_index][1] <= cursor + 0.001:
            target_index += 1

        current_index = target_index
        while current_index < len(target_intervals):
            target_start, target_end = target_intervals[current_index]
            if target_start >= source_end - 0.001:
                break
            if target_start > cursor + 0.001:
                uncovered.append((cursor, min(target_start, source_end)))
            cursor = max(cursor, target_end)
            if cursor >= source_end - 0.001:
                break
            current_index += 1

        if cursor < source_end - 0.001:
            uncovered.append((cursor, source_end))

    return uncovered
