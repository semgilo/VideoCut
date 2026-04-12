"""
Video inpainting module for VideoCut.

Provides two main use-cases:
  1. Logo / watermark removal  – user specifies one or more rectangular
     regions (or a mask image) that cover the logo; those pixels are filled
     in from the surrounding content every frame.
  2. Old-film scratch repair   – scratches are detected automatically via
     vertical-stripe analysis and then inpainted.

Built-in algorithms (require only OpenCV, no GPU):
  - TELEA  Fast Marching Method (Telea 2004). Propagates boundary pixels
    inward along the fast-marching front. Fast and sharp; best for thin
    scratches and small logos.
  - NS     Navier-Stokes diffusion (Bertalmio et al. 2001). Follows image
    isophotes (lines of equal intensity) inward; smoother results at the
    cost of some blurring. Better for medium-size regions.

Optional high-quality algorithm (requires `pip install simple-lama-inpainting`):
  - LAMA   Resolution-Robust Large Mask Inpainting (Suvorov et al. 2022).
    Uses a deep CNN with Fast Fourier Convolutions that can handle very
    large masks with complex backgrounds. Requires ~2 GB RAM for the model.
    Falls back to TELEA automatically if not installed.

Usage example:
    from videocut.inpaint import InpaintMethod, inpaint_video

    # Remove a logo in the top-right corner
    inpaint_video(
        input_path=Path("input.mp4"),
        output_path=Path("clean.mp4"),
        regions=[(1720, 40, 180, 60)],   # (x, y, w, h) in pixels
        method=InpaintMethod.TELEA,
    )

    # Repair film scratches automatically
    inpaint_video(
        input_path=Path("old_film.mp4"),
        output_path=Path("restored.mp4"),
        auto_scratch=True,
        scratch_sensitivity=1.5,
    )
"""
from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
from enum import Enum
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Algorithm enum
# ---------------------------------------------------------------------------

class InpaintMethod(str, Enum):
    TELEA = "telea"  # Fast Marching – thin regions, scratches
    NS = "ns"        # Navier-Stokes – medium regions, logos
    LAMA = "lama"    # Deep learning – large regions (requires extra install)


# ---------------------------------------------------------------------------
# Mask construction helpers
# ---------------------------------------------------------------------------

def build_mask_from_regions(
    frame_shape: tuple[int, int],
    regions: list[tuple[int, int, int, int]],
) -> np.ndarray:
    """Return a uint8 mask (255 = inpaint, 0 = keep) from (x, y, w, h) boxes.

    Coordinates are clipped to the frame dimensions so out-of-bounds regions
    are handled gracefully.
    """
    fh, fw = frame_shape[:2]
    mask = np.zeros((fh, fw), dtype=np.uint8)
    for (x, y, rw, rh) in regions:
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(fw, x + rw)
        y2 = min(fh, y + rh)
        mask[y1:y2, x1:x2] = 255
    return mask


def build_mask_from_file(mask_path: Path) -> np.ndarray:
    """Load a grayscale mask image.  White (> 128) → inpaint, black → keep."""
    mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if mask is None:
        raise FileNotFoundError(f"Cannot read mask image: {mask_path}")
    _, binary = cv2.threshold(mask, 128, 255, cv2.THRESH_BINARY)
    return binary


def dilate_mask(mask: np.ndarray, pixels: int = 2) -> np.ndarray:
    """Expand the mask outward by `pixels` to avoid border artefacts."""
    if pixels <= 0:
        return mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (pixels * 2 + 1,) * 2)
    return cv2.dilate(mask, kernel)


# ---------------------------------------------------------------------------
# Scratch detection
# ---------------------------------------------------------------------------

def detect_scratch_mask(frame: np.ndarray, sensitivity: float = 1.0) -> np.ndarray:
    """Detect film-scratch regions in a single frame.

    Old film scratches appear as bright (or dark) vertical streaks.  The
    algorithm compares each pixel column to a vertically-blurred version of
    itself: columns with high residual are flagged as scratches.

    Args:
        frame:        BGR (or greyscale) numpy array.
        sensitivity:  Multiplier: higher values → more pixels flagged.
                      Range 0.5 (conservative) … 3.0 (aggressive).

    Returns:
        Binary uint8 mask: 255 = scratch, 0 = clean.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame.copy()

    # Vertical-only blur smooths along the scratch direction.
    blurred = cv2.GaussianBlur(gray, (1, 31), 0)
    diff = cv2.absdiff(gray.astype(np.float32), blurred.astype(np.float32))

    # Normalise and threshold.
    diff_u8 = cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    threshold = max(1, int(60 / sensitivity))
    _, mask = cv2.threshold(diff_u8, threshold, 255, cv2.THRESH_BINARY)

    # Keep only near-vertical structures (height >> width).
    kernel_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 15))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_v)

    # Widen slightly so we cover the full scratch.
    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 1))
    mask = cv2.dilate(mask, kernel_h, iterations=2)
    return mask


# ---------------------------------------------------------------------------
# Single-frame inpainting
# ---------------------------------------------------------------------------

def inpaint_frame(
    frame: np.ndarray,
    mask: np.ndarray,
    method: InpaintMethod = InpaintMethod.TELEA,
    radius: int = 5,
) -> np.ndarray:
    """Inpaint damaged/masked pixels in a single BGR frame.

    Args:
        frame:   H×W×3 BGR image.
        mask:    H×W uint8 mask; 255 = fill, 0 = keep.
        method:  Algorithm to use.
        radius:  Neighbourhood radius for TELEA / NS (pixels).

    Returns:
        Inpainted BGR image of the same shape.
    """
    if not np.any(mask):
        return frame  # nothing to do

    if method == InpaintMethod.TELEA:
        return cv2.inpaint(frame, mask, radius, cv2.INPAINT_TELEA)
    elif method == InpaintMethod.NS:
        return cv2.inpaint(frame, mask, radius, cv2.INPAINT_NS)
    elif method == InpaintMethod.LAMA:
        return _inpaint_lama_frame(frame, mask)
    else:
        raise ValueError(f"Unknown inpaint method: {method!r}")


# LaMa model singleton (loaded once on first use).
_lama_model = None


def _inpaint_lama_frame(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """LaMa inpainting; falls back to TELEA if the package is absent."""
    global _lama_model
    try:
        from simple_lama_inpainting import SimpleLama  # type: ignore
        from PIL import Image  # type: ignore

        if _lama_model is None:
            logger.info("Loading LaMa inpainting model (first run)…")
            _lama_model = SimpleLama()

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        pil_msk = Image.fromarray(mask)
        result_pil = _lama_model(pil_img, pil_msk)
        return cv2.cvtColor(np.array(result_pil), cv2.COLOR_RGB2BGR)

    except ImportError:
        logger.warning(
            "simple-lama-inpainting is not installed – falling back to TELEA.\n"
            "Install it with:  pip install simple-lama-inpainting"
        )
        return cv2.inpaint(frame, mask, 5, cv2.INPAINT_TELEA)


# ---------------------------------------------------------------------------
# Full-video pipeline
# ---------------------------------------------------------------------------

def inpaint_video(
    input_path: Path,
    output_path: Path,
    regions: list[tuple[int, int, int, int]] | None = None,
    mask_path: Path | None = None,
    auto_scratch: bool = False,
    method: InpaintMethod = InpaintMethod.TELEA,
    radius: int = 5,
    scratch_sensitivity: float = 1.0,
    dilate_pixels: int = 2,
) -> Path:
    """Process a video file and remove logos or repair scratches.

    Mask priority (highest → lowest):
      1. ``mask_path``  – explicit grayscale image file.
      2. ``regions``    – list of (x, y, w, h) rectangles.
      3. ``auto_scratch`` – per-frame automatic scratch detection.

    At least one of the three must be supplied.

    Args:
        input_path:         Source video file.
        output_path:        Destination path for the cleaned video.
        regions:            Static rectangular regions to inpaint every frame,
                            e.g. [(x, y, w, h), …].  Useful for logos that
                            always appear in the same position.
        mask_path:          Greyscale image where white pixels mark regions to
                            inpaint.  Must match or be resized to video size.
        auto_scratch:       Automatically detect and repair film scratches on
                            every frame using vertical-stripe analysis.
        method:             Inpainting algorithm (TELEA / NS / LAMA).
        radius:             Neighbourhood radius for TELEA / NS.
        scratch_sensitivity: Sensitivity for automatic scratch detection
                            (default 1.0; increase to catch lighter scratches).
        dilate_pixels:      Expand mask by this many pixels before inpainting
                            to avoid faint border rings.

    Returns:
        Absolute path to the output file.
    """
    if regions is None and mask_path is None and not auto_scratch:
        raise ValueError(
            "Specify at least one of: --region, --mask, or --scratch."
        )

    input_path = input_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {input_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # --- build static mask (if any) ------------------------------------------
    static_mask: np.ndarray | None = None

    if mask_path is not None:
        raw_mask = build_mask_from_file(mask_path)
        if raw_mask.shape[:2] != (height, width):
            raw_mask = cv2.resize(raw_mask, (width, height), interpolation=cv2.INTER_NEAREST)
        static_mask = dilate_mask(raw_mask, dilate_pixels)
        logger.info("Static mask loaded from %s", mask_path)

    elif regions:
        raw_mask = build_mask_from_regions((height, width), regions)
        static_mask = dilate_mask(raw_mask, dilate_pixels)
        logger.info(
            "Static mask built from %d region(s): %s", len(regions), regions
        )

    # --- write inpainted frames to a temp file (no audio) --------------------
    tmp_video = output_path.with_suffix(".tmp_noaudio.mp4")

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(tmp_video), fourcc, fps, (width, height))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Failed to open VideoWriter.")

    frame_idx = 0
    log_interval = max(1, total_frames // 20)  # log ~20 progress lines

    print(f"[inpaint] {total_frames} frames  {width}×{height}  {fps:.2f} fps")
    print(f"[inpaint] method={method.value}  radius={radius}")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        # Determine mask for this frame.
        if static_mask is not None:
            mask = static_mask
        else:
            # auto_scratch: per-frame detection
            scratch = detect_scratch_mask(frame, sensitivity=scratch_sensitivity)
            mask = dilate_mask(scratch, dilate_pixels)

        result = inpaint_frame(frame, mask, method=method, radius=radius)
        writer.write(result)

        frame_idx += 1
        if frame_idx % log_interval == 0 or frame_idx == total_frames:
            pct = 100.0 * frame_idx / max(1, total_frames)
            print(f"[inpaint] {frame_idx}/{total_frames} frames ({pct:.0f}%)", flush=True)

    cap.release()
    writer.release()

    # --- mux original audio back in with ffmpeg ------------------------------
    _mux_audio(input_path, tmp_video, output_path)
    tmp_video.unlink(missing_ok=True)

    print(f"[inpaint] Done → {output_path}")
    return output_path.resolve()


def _mux_audio(source_video: Path, silent_video: Path, output: Path) -> None:
    """Copy original audio into the inpainted video (no re-encode)."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(silent_video),   # inpainted frames (no audio)
        "-i", str(source_video),   # original (audio source)
        "-c:v", "copy",
        "-c:a", "aac",
        "-map", "0:v:0",
        "-map", "1:a:0?",          # '?' = OK if source has no audio
        "-shortest",
        str(output),
    ]
    logger.debug("ffmpeg mux: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # If audio mux fails, keep the silent video as fallback.
        logger.warning(
            "Audio mux failed (ffmpeg exit %d); output will have no audio.\n%s",
            result.returncode,
            result.stderr[-1000:],
        )
        import shutil
        shutil.copy2(str(silent_video), str(output))


# ---------------------------------------------------------------------------
# Fast-path: ffmpeg delogo (no opencv required)
# ---------------------------------------------------------------------------

def inpaint_video_ffmpeg(
    input_path: Path,
    output_path: Path,
    regions: list[tuple[int, int, int, int]],
    show: bool = False,
) -> Path:
    """Remove logo regions using ffmpeg's built-in ``delogo`` filter.

    This is a lightweight alternative to the OpenCV-based ``inpaint_video``
    that works without any Python image-processing dependencies.  The delogo
    filter blurs the boundary of each specified rectangle and fills the
    interior by extrapolating surrounding pixels.

    ``delogo`` works best for uniform or low-frequency backgrounds (skies,
    walls, studio shots).  For complex backgrounds, prefer
    ``inpaint_video(method=InpaintMethod.TELEA)`` which gives higher quality.

    Args:
        input_path:   Source video file.
        output_path:  Output path (will be overwritten).
        regions:      List of (x, y, w, h) bounding boxes to remove.
        show:         If True, draw a visible green box instead of removing
                      (useful for verifying coordinates before committing).

    Returns:
        Resolved absolute path to the output file.
    """
    if not regions:
        raise ValueError("At least one region is required for ffmpeg delogo.")

    input_path = input_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    show_flag = "1" if show else "0"

    # Build a delogo filter for each region, chained with commas.
    filter_parts = [
        f"delogo=x={x}:y={y}:w={w}:h={h}:show={show_flag}"
        for (x, y, w, h) in regions
    ]
    vf = ",".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-c:a", "copy",
        str(output_path),
    ]
    print(f"[inpaint-ffmpeg] filter: {vf}")
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg delogo failed (exit {result.returncode}):\n{result.stderr[-2000:]}"
        )
    print(f"[inpaint-ffmpeg] Done → {output_path}")
    return output_path.resolve()


# ---------------------------------------------------------------------------
# Convenience: parse "x,y,w,h" strings from the CLI
# ---------------------------------------------------------------------------

def parse_region(text: str) -> tuple[int, int, int, int]:
    """Parse a ``'x,y,w,h'`` string into a tuple of ints."""
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise ValueError(f"Region must be 'x,y,w,h', got: {text!r}")
    try:
        x, y, w, h = map(int, parts)
    except ValueError:
        raise ValueError(f"Region values must be integers, got: {text!r}")
    if w <= 0 or h <= 0:
        raise ValueError(f"Region width and height must be positive, got: {text!r}")
    return x, y, w, h
