#!/usr/bin/env python3
"""
AI Roto Bridge - local SAM2 worker

Called by the Blender extension as:

    /path/to/sam2/python sam2_worker.py /path/to/request.json

This version is compatible with the request.json written by
ai_roto_bridge 0.1.0.

It:
  1. Extracts the requested frame range to temporary JPEG files with ffmpeg.
  2. Loads SAM2 locally using the selected Python environment.
  3. Applies the positive/negative point prompt.
  4. Propagates the mask forward and backward from the prompt frame.
  5. Writes mask_XXXXXX.png files into the requested output directory.

No network/API calls are made.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError as err:
    print(f"ERROR: Missing Python dependency: {err}", flush=True)
    print("Please install missing dependencies in your external Python environment:", flush=True)
    print("  pip install pillow numpy opencv-python torch torchvision", flush=True)
    print("  pip install git+https://github.com/facebookresearch/sam2.git", flush=True)
    sys.exit(1)


class PerfTimer:
    def __init__(self):
        self.timers = {}
        self.start_times = {}

    def start(self, name: str):
        self.start_times[name] = time.perf_counter()

    def stop(self, name: str) -> float:
        if name in self.start_times:
            elapsed = time.perf_counter() - self.start_times.pop(name)
            self.timers[name] = self.timers.get(name, 0.0) + elapsed
            return elapsed
        return 0.0

    def print_summary(self):
        total = sum(self.timers.values())
        log("")
        log("=" * 60)
        log("PERFORMANCE BENCHMARK SUMMARY (MICROSECOND PROFILER)")
        log("=" * 60)
        for name, dur in self.timers.items():
            pct = (dur / total * 100.0) if total > 0 else 0.0
            log(f"  {name:<38}: {dur:6.3f}s ({pct:5.1f}%)")
        log("-" * 60)
        log(f"  {'TOTAL EXECUTION TIME':<38}: {total:6.3f}s (100.0%)")
        log("=" * 60)
        log("")



_active_log_file = None


def set_active_log_file(file_path: Path | None):
    global _active_log_file
    if _active_log_file:
        try:
            _active_log_file.close()
        except Exception:
            pass
        _active_log_file = None

    if file_path:
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            _active_log_file = open(file_path, "w", encoding="utf-8")
        except Exception:
            _active_log_file = None


def log(message: str) -> None:
    print(message, flush=True)
    if _active_log_file:
        try:
            _active_log_file.write(message + "\n")
            _active_log_file.flush()
        except Exception:
            pass


def fail(message: str, exit_code: int = 1) -> None:
    log(f"ERROR: {message}")
    raise SystemExit(exit_code)


def load_request(path: Path) -> dict:
    if not path.is_file():
        fail(f"Request file does not exist: {path}")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Could not read request JSON: {exc}")


def require_file(value: str | None, label: str) -> Path:
    if not value:
        fail(f"{label} is not set")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        fail(f"{label} does not exist: {path}")
    return path


def infer_sam2_config(checkpoint: Path, config_value: str | None) -> str:
    """
    SAM2's build_sam2_video_predictor() expects a Hydra config name such as:

        configs/sam2.1/sam2.1_hiera_s.yaml

    rather than an arbitrary absolute YAML path.

    If Blender gives us an absolute path that contains a 'configs' directory,
    reduce it to the Hydra-relative name. Otherwise infer the official config
    from the checkpoint filename.
    """

    if config_value:
        raw = config_value.replace("\\", "/")

        # Already a Hydra config name.
        if raw.startswith("configs/"):
            return raw

        # Convert ".../sam2/configs/sam2.1/foo.yaml" -> "configs/sam2.1/foo.yaml"
        parts = [p for p in raw.split("/") if p]
        if "configs" in parts:
            i = parts.index("configs")
            return "/".join(parts[i:])

    name = checkpoint.name.lower()

    sam21 = "sam2.1" in name
    prefix = "configs/sam2.1" if sam21 else "configs/sam2"

    if "tiny" in name:
        suffix = "sam2.1_hiera_t.yaml" if sam21 else "sam2_hiera_t.yaml"
    elif "small" in name:
        suffix = "sam2.1_hiera_s.yaml" if sam21 else "sam2_hiera_s.yaml"
    elif "base_plus" in name or "base-plus" in name or "base+" in name:
        suffix = "sam2.1_hiera_b+.yaml" if sam21 else "sam2_hiera_b+.yaml"
    elif "large" in name:
        suffix = "sam2.1_hiera_l.yaml" if sam21 else "sam2_hiera_l.yaml"
    else:
        fail(
            "Could not infer the SAM2 config from checkpoint filename "
            f"'{checkpoint.name}'. Set Model Config in the Blender extension."
        )

    return f"{prefix}/{suffix}"


def choose_device(requested: str):
    import torch

    requested = (requested or "auto").strip().lower()

    if requested in {"auto", "xpu", "intel"}:
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            log("Intel GPU (PyTorch XPU / OneAPI) detected and enabled!")
            return torch.device("xpu")

    if requested == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ):
            return torch.device("mps")
        return torch.device("cpu")

    if requested in {"xpu", "intel"}:
        if hasattr(torch, "xpu") and torch.xpu.is_available():
            return torch.device("xpu")
        log("WARNING: Intel XPU was requested, but PyTorch XPU is not available. Using CPU with multi-threading optimization.")
        return torch.device("cpu")

    if requested == "cuda":
        if not torch.cuda.is_available():
            fail("CUDA was requested, but PyTorch cannot see a CUDA device.")
        return torch.device("cuda")

    if requested == "cpu":
        return torch.device("cpu")

    if requested == "mps":
        if not (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ):
            fail("MPS was requested, but PyTorch cannot use MPS.")
        return torch.device("mps")

    if requested in {"openvino", "other"}:
        fail(
            f"Device '{requested}' is not supported by this SAM2/PyTorch worker. "
            "Choose CPU, Intel GPU (XPU), CUDA, or Auto."
        )

    # Permit advanced PyTorch device strings such as xpu:0 or cuda:1.
    try:
        return torch.device(requested)
    except Exception:
        fail(f"Unknown PyTorch device: {requested}")


def find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        fail(
            "ffmpeg was not found in PATH. "
            "Install ffmpeg or make it visible to the SAM2 Python process."
        )
    return ffmpeg


def extract_frame_range_cv2(
    source: Path,
    destination: Path,
    frame_start: int,
    frame_end: int,
) -> int:
    try:
        import cv2
        destination.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(source))
        if not cap.isOpened():
            return 0

        first_index = frame_start - 1
        last_index = frame_end - 1
        
        cap.set(cv2.CAP_PROP_POS_FRAMES, first_index)

        saved_count = 0
        curr_index = first_index

        while curr_index <= last_index:
            ret, frame = cap.read()
            if not ret:
                break
            out_file = destination / f"{saved_count:06d}.jpg"
            cv2.imwrite(str(out_file), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            saved_count += 1
            curr_index += 1

        cap.release()
        return saved_count
    except Exception:
        return 0


def extract_frame_range(
    source: Path,
    destination: Path,
    frame_start: int,
    frame_end: int,
) -> int:
    """
    Extract a Blender-style inclusive frame range.
    Uses fast OpenCV in-memory decoding when available, falling back to ffmpeg.
    """

    if frame_start < 1:
        fail("frame_start must be >= 1")
    if frame_end < frame_start:
        fail("frame_end must be >= frame_start")

    # Fast OpenCV extraction
    cv2_count = extract_frame_range_cv2(source, destination, frame_start, frame_end)
    if cv2_count == (frame_end - frame_start + 1):
        log(f"Extracted {cv2_count} frame(s) using OpenCV (fast direct decoding)")
        return cv2_count

    first_source_index = frame_start - 1
    last_source_index = frame_end - 1

    destination.mkdir(parents=True, exist_ok=True)

    # SAM2's JPEG-folder loader sorts files by the numeric stem, so start at 0.
    output_pattern = destination / "%06d.jpg"

    # Escape commas for ffmpeg's filtergraph parser.
    select_filter = (
        f"select=between(n\\,{first_source_index}\\,{last_source_index})"
    )

    cmd = [
        find_ffmpeg(),
        "-hide_banner",
        "-loglevel", "error",
        "-i", str(source),
        "-vf", select_filter,
        "-vsync", "0",
        "-start_number", "0",
        "-q:v", "2",
        str(output_pattern),
    ]

    log(
        f"Extracting source frames {frame_start}..{frame_end} "
        f"from {source.name}..."
    )

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        fail(
            "ffmpeg frame extraction failed:\n"
            + (result.stderr.strip() or "(no ffmpeg error text)")
        )

    frames = sorted(destination.glob("*.jpg"))
    expected = frame_end - frame_start + 1

    if not frames:
        fail("ffmpeg produced no JPEG frames.")

    if len(frames) != expected:
        log(
            f"WARNING: requested {expected} frames, but ffmpeg produced "
            f"{len(frames)}. Continuing with the extracted frames."
        )

    return len(frames)


def normalized_points_to_pixels(
    points: list[list[float]],
    width: int,
    height: int,
    coordinate_space: str,
) -> np.ndarray:
    if not points:
        return np.empty((0, 2), dtype=np.float32)

    result = []

    for point in points:
        if len(point) != 2:
            fail(f"Invalid point: {point}")

        x = float(point[0])
        y = float(point[1])

        if coordinate_space == "normalized_bottom_left":
            # Blender clip-space: origin at bottom-left.
            x_px = x * max(width - 1, 1)
            y_px = (1.0 - y) * max(height - 1, 1)

        elif coordinate_space == "normalized_top_left":
            x_px = x * max(width - 1, 1)
            y_px = y * max(height - 1, 1)

        elif coordinate_space == "pixels_top_left":
            x_px = x
            y_px = y

        else:
            fail(f"Unsupported coordinate space: {coordinate_space}")

        # Keep prompts inside the image.
        x_px = min(max(x_px, 0.0), max(width - 1, 0))
        y_px = min(max(y_px, 0.0), max(height - 1, 0))

        result.append([x_px, y_px])

    return np.asarray(result, dtype=np.float32)


def output_filename(pattern: str, blender_frame: int) -> str:
    try:
        return pattern % blender_frame
    except Exception as exc:
        fail(
            f"Invalid output pattern '{pattern}'. "
            f"Expected something like mask_%06d.png. ({exc})"
        )


def mask_tensor_for_object(mask_logits, object_ids, object_id: int):
    """
    Return the [H, W] logits tensor belonging to object_id.
    """

    ids = list(object_ids)

    try:
        obj_index = ids.index(object_id)
    except ValueError:
        fail(
            f"SAM2 output did not contain object id {object_id}; "
            f"returned ids were {ids}"
        )

    mask = mask_logits[obj_index]

    # Usually [1, H, W]; reduce singleton dimensions to [H, W].
    while mask.ndim > 2 and mask.shape[0] == 1:
        mask = mask[0]

    if mask.ndim != 2:
        fail(f"Unexpected SAM2 mask shape: {tuple(mask.shape)}")

    return mask


def save_matte(
    mask_logits,
    object_ids,
    object_id: int,
    destination: Path,
    soft_mask: bool,
) -> None:
    import torch

    logits = mask_tensor_for_object(mask_logits, object_ids, object_id)

    if soft_mask:
        matte = torch.sigmoid(logits).detach().float().cpu().numpy()
        matte = np.clip(matte * 255.0, 0, 255).astype(np.uint8)
    else:
        matte = (
            (logits > 0.0)
            .detach()
            .to(torch.uint8)
            .cpu()
            .numpy()
            * 255
        )

    Image.fromarray(matte, mode="L").save(destination)


def run(request: dict) -> None:
    perf = PerfTimer()
    perf.start("1. Imports (PyTorch & SAM2)")
    try:
        import torch
        import sam2  # noqa: F401
        from sam2.build_sam import build_sam2_video_predictor
    except Exception as exc:
        fail(
            "Could not import PyTorch/SAM2 from this Python environment.\n"
            f"{type(exc).__name__}: {exc}"
        )
    perf.stop("1. Imports (PyTorch & SAM2)")

    source_info = request.get("source", {})
    prompt_info = request.get("prompt", {})
    backend = request.get("backend", {})
    output_info = request.get("output", {})

    source = require_file(source_info.get("path"), "Source video")
    
    checkpoint_val = backend.get("model_path")
    if checkpoint_val and Path(checkpoint_val).expanduser().resolve().is_file():
        checkpoint = Path(checkpoint_val).expanduser().resolve()
    else:
        # Search common SAM2 checkpoint locations (Kdenlive, Torch, HuggingFace)
        search_dirs = [
            Path.home() / ".local/share/kdenlive",
            Path.home() / ".cache/kdenlive",
            Path.home() / ".cache/torch/hub/checkpoints",
            Path.home() / ".cache/huggingface",
            Path.cwd(),
        ]
        candidates = []
        for s_dir in search_dirs:
            if s_dir.is_dir():
                candidates.extend(s_dir.rglob("sam2*.pt"))
                candidates.extend(s_dir.rglob("*.pt"))
        if candidates:
            checkpoint = candidates[0]
            log(f"Auto-detected SAM2 checkpoint: {checkpoint}")
        else:
            checkpoint = require_file(checkpoint_val, "SAM2 checkpoint")

    frame_start = int(source_info.get("frame_start", 1))
    frame_end = int(source_info.get("frame_end", frame_start))
    prompt_frame = int(prompt_info.get("frame", frame_start))

    if not (frame_start <= prompt_frame <= frame_end):
        fail(
            f"Prompt frame {prompt_frame} is outside requested range "
            f"{frame_start}..{frame_end}."
        )

    positive = prompt_info.get("positive", [])
    negative = prompt_info.get("negative", [])

    if not positive:
        fail("At least one positive/foreground point is required.")

    coordinate_space = prompt_info.get(
        "coordinate_space",
        "normalized_bottom_left",
    )

    preview_only = bool(prompt_info.get("preview_only", False))

    output_dir_value = output_info.get("directory")
    if not output_dir_value:
        fail("Output directory is not set.")

    output_dir = Path(output_dir_value).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    pattern = output_info.get("pattern", "mask_%06d.png")
    soft_mask = bool(output_info.get("soft_mask", False))

    if not preview_only:
        # Remove old masks matching our standard prefix so stale frames cannot
        # accidentally be loaded by Blender after a shorter rerun.
        for old_mask in output_dir.glob("mask_*.png"):
            try:
                old_mask.unlink()
            except OSError:
                pass

    config_name = infer_sam2_config(
        checkpoint,
        backend.get("config_path"),
    )

    device = choose_device(backend.get("device", "auto"))

    if device.type == "cpu":
        try:
            num_cores = os.cpu_count() or 4
            torch.set_num_threads(num_cores)
            log(f"Configured PyTorch CPU multi-threading: {num_cores} threads")
        except Exception:
            pass

    log(f"Python: {sys.executable}")
    log(f"PyTorch: {torch.__version__}")
    log(f"SAM2 checkpoint: {checkpoint}")
    log(f"SAM2 config: {config_name}")
    log(f"Device: {device}")
    log(f"Output: {output_dir}")
    if preview_only:
        log(f"Mode: Preview single frame {prompt_frame}")

    with tempfile.TemporaryDirectory(prefix="blender_sam2_") as temp:
        jpeg_dir = Path(temp) / "frames"

        perf.start("2. Frame Extraction (ffmpeg/JPEG)")
        if preview_only:
            num_frames = extract_frame_range(
                source,
                jpeg_dir,
                prompt_frame,
                prompt_frame,
            )
            local_prompt_index = 0
        else:
            num_frames = extract_frame_range(
                source,
                jpeg_dir,
                frame_start,
                frame_end,
            )
            local_prompt_index = prompt_frame - frame_start
        perf.stop("2. Frame Extraction (ffmpeg/JPEG)")

        if local_prompt_index >= num_frames:
            fail(
                f"Prompt frame maps to extracted frame {local_prompt_index}, "
                f"but only {num_frames} frames were extracted."
            )

        log("Building SAM2 video predictor...")
        perf.start("3. SAM2 Model Loading")
        predictor = build_sam2_video_predictor(
            config_name,
            str(checkpoint),
            device=device,
        )
        perf.stop("3. SAM2 Model Loading")

        log("Loading video frames into SAM2...")
        perf.start("4. SAM2 Predictor init_state")
        # Keeping source frames/state on CPU is conservative and is especially
        # suitable for this Intel/CPU use case.
        inference_state = predictor.init_state(
            video_path=str(jpeg_dir),
            offload_video_to_cpu=True,
            offload_state_to_cpu=True,
            async_loading_frames=False,
        )
        perf.stop("4. SAM2 Predictor init_state")

        width = int(inference_state["video_width"])
        height = int(inference_state["video_height"])

        positive_px = normalized_points_to_pixels(
            positive,
            width,
            height,
            coordinate_space,
        )
        negative_px = normalized_points_to_pixels(
            negative,
            width,
            height,
            coordinate_space,
        )

        points = np.concatenate([positive_px, negative_px], axis=0)
        labels = np.concatenate(
            [
                np.ones(len(positive_px), dtype=np.int32),
                np.zeros(len(negative_px), dtype=np.int32),
            ],
            axis=0,
        )

        log(
            f"Prompt frame: {prompt_frame} "
            f"(local SAM2 frame {local_prompt_index})"
        )
        log(
            f"Prompt points: {len(positive_px)} positive, "
            f"{len(negative_px)} negative"
        )

        object_id = 1

        perf.start("5. Single-Frame SAM2 Inference")
        # No CUDA autocast here: CPU operation must remain valid.
        with torch.inference_mode():
            _, object_ids, prompt_logits = predictor.add_new_points_or_box(
                inference_state=inference_state,
                frame_idx=local_prompt_index,
                obj_id=object_id,
                points=points,
                labels=labels,
            )

            prompt_output = output_dir / output_filename(
                pattern,
                prompt_frame,
            )
            save_matte(
                prompt_logits,
                object_ids,
                object_id,
                prompt_output,
                soft_mask,
            )
            perf.stop("5. Single-Frame SAM2 Inference")

            if preview_only:
                log("PREVIEW_FINISHED")
                perf.print_summary()
                return

            perf.start("6. Video Tracking Propagation")
            written = {local_prompt_index}

            def write_result(local_index, ids, logits):
                blender_frame = frame_start + int(local_index)

                # Respect the actual extracted range if ffmpeg returned fewer
                # frames than expected.
                if blender_frame > frame_end:
                    return

                destination = output_dir / output_filename(
                    pattern,
                    blender_frame,
                )
                save_matte(
                    logits,
                    ids,
                    object_id,
                    destination,
                    soft_mask,
                )
                written.add(int(local_index))
                log(
                    f"PROGRESS {len(written)}/{num_frames} "
                    f"frame={blender_frame}"
                )

            # Forward from the selected frame.
            log("Propagating forward...")
            for local_idx, ids, logits in predictor.propagate_in_video(
                inference_state,
                start_frame_idx=local_prompt_index,
                max_frame_num_to_track=num_frames,
                reverse=False,
            ):
                write_result(local_idx, ids, logits)

            # Backward from the selected frame so the user can prompt in the
            # middle of a shot instead of being forced to use frame 1.
            if local_prompt_index > 0:
                log("Propagating backward...")
                for local_idx, ids, logits in predictor.propagate_in_video(
                    inference_state,
                    start_frame_idx=local_prompt_index,
                    max_frame_num_to_track=num_frames,
                    reverse=True,
                ):
                    write_result(local_idx, ids, logits)

            perf.stop("6. Video Tracking Propagation")

        perf.print_summary()
        log(
            f"DONE: generated {len(written)} matte frames "
            f"in {output_dir}"
        )


class SAM2Daemon:
    def __init__(self):
        self.predictor = None
        self.checkpoint = None
        self.config_name = None
        self.device = None
        self.cached_source = None
        self.cached_jpeg_dir = None
        self.cached_temp_dir = None
        self.cached_inference_state = None
        self.cached_range = None
        self.last_perf_stats = {}
        self.last_log_lines = []
        self.total_requests = 0

    def generate_dashboard_html(self) -> str:
        checkpoint_name = self.checkpoint.name if self.checkpoint else "Not Loaded"
        config_str = str(self.config_name) if self.config_name else "Default"
        device_str = str(self.device) if self.device else "Auto"
        video_str = self.cached_source.name if self.cached_source else "None"

        perf_rows = ""
        total_time = sum(self.last_perf_stats.values()) if self.last_perf_stats else 0.0

        if self.last_perf_stats:
            for name, dur in self.last_perf_stats.items():
                pct = (dur / total_time * 100.0) if total_time > 0 else 0.0
                bar_color = "#00e5ff" if "Inference" in name else ("#ff9800" if "init_state" in name else "#4caf50")
                perf_rows += f"""
                <tr>
                    <td style="padding:10px;border-bottom:1px solid #2a2d3d;">{name}</td>
                    <td style="padding:10px;border-bottom:1px solid #2a2d3d;font-weight:bold;">{dur:.3f}s</td>
                    <td style="padding:10px;border-bottom:1px solid #2a2d3d;">
                        <div style="background:#2a2d3d;border-radius:4px;overflow:hidden;height:12px;width:100%;max-width:200px;display:inline-block;vertical-align:middle;margin-right:8px;">
                            <div style="background:{bar_color};width:{pct:.1f}%;height:100%;"></div>
                        </div>
                        <span>{pct:.1f}%</span>
                    </td>
                </tr>
                """
        else:
            perf_rows = "<tr><td colspan='3' style='padding:20px;text-align:center;color:#888;'>No preview requests processed yet. Pick a point in Blender to run benchmarks!</td></tr>"

        log_content = "\n".join(self.last_log_lines[-30:]) if self.last_log_lines else "Daemon started. Listening for requests..."

        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta http-equiv="refresh" content="2">
    <title>AI Roto SAM2 - Performance Dashboard</title>
    <style>
        body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: #0f1015; color: #e0e0e0; margin: 0; padding: 24px; }}
        .header {{ display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid #2a2d3d; padding-bottom: 16px; margin-bottom: 24px; }}
        .badge {{ background: #00e5ff1a; color: #00e5ff; border: 1px solid #00e5ff80; padding: 4px 12px; border-radius: 16px; font-size: 14px; font-weight: 600; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .card {{ background: #181922; border: 1px solid #2a2d3d; border-radius: 8px; padding: 16px; }}
        .card-label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #888; margin-bottom: 6px; }}
        .card-val {{ font-size: 18px; font-weight: bold; color: #fff; word-break: break-all; }}
        .section-title {{ font-size: 18px; font-weight: 600; color: #fff; margin-bottom: 12px; }}
        table {{ width: 100%; border-collapse: collapse; background: #181922; border: 1px solid #2a2d3d; border-radius: 8px; overflow: hidden; margin-bottom: 24px; }}
        th {{ background: #222433; text-align: left; padding: 12px; font-size: 13px; text-transform: uppercase; color: #aaa; border-bottom: 1px solid #2a2d3d; }}
        .log-box {{ background: #08080c; border: 1px solid #2a2d3d; border-radius: 8px; padding: 16px; font-family: monospace; font-size: 12px; line-height: 1.5; color: #00e5ff; white-space: pre-wrap; max-height: 250px; overflow-y: auto; }}
    </style>
</head>
<body>
    <div class="header">
        <h2 style="margin:0;color:#fff;">⚡ AI Roto SAM2 Performance Monitor</h2>
        <span class="badge">🟢 DAEMON ACTIVE (Port 18950)</span>
    </div>

    <div class="grid">
        <div class="card">
            <div class="card-label">Execution Device</div>
            <div class="card-val" style="color:#00e5ff;">{device_str.upper()}</div>
        </div>
        <div class="card">
            <div class="card-label">Loaded Checkpoint</div>
            <div class="card-val">{checkpoint_name}</div>
        </div>
        <div class="card">
            <div class="card-label">Cached Video Clip</div>
            <div class="card-val">{video_str}</div>
        </div>
        <div class="card">
            <div class="card-label">Total Latency</div>
            <div class="card-val" style="color:#ff9800;">{total_time:.3f}s</div>
        </div>
    </div>

    <div class="section-title">📊 Last Inference Performance Breakdown</div>
    <table>
        <thead>
            <tr>
                <th>Execution Phase</th>
                <th>Duration</th>
                <th>Percentage of Total</th>
            </tr>
        </thead>
        <tbody>
            {perf_rows}
        </tbody>
    </table>

    <div class="section-title">📜 Real-Time Worker Log Output (Auto-refreshes)</div>
    <div class="log-box">{log_content}</div>
</body>
</html>"""
        return html

    def process_request(self, request: dict) -> dict:
        output_info = request.get("output", {})
        prompt_info = request.get("prompt", {})
        preview_only = bool(prompt_info.get("preview_only", False))
        
        output_dir_val = output_info.get("directory")
        if output_dir_val:
            out_d = Path(output_dir_val).expanduser().resolve()
            log_name = "worker_preview.log" if preview_only else "worker.log"
            set_active_log_file(out_d / log_name)

        try:
            res = self._do_process_request(request)
            self.total_requests += 1
            return res
        finally:
            set_active_log_file(None)

    def _do_process_request(self, request: dict) -> dict:
        perf = PerfTimer()
        perf.start("1. Message Parsing & Config Check")

        source_info = request.get("source", {})
        prompt_info = request.get("prompt", {})
        backend = request.get("backend", {})
        output_info = request.get("output", {})

        source = require_file(source_info.get("path"), "Source video")
        
        checkpoint_val = backend.get("model_path")
        if checkpoint_val and Path(checkpoint_val).expanduser().resolve().is_file():
            checkpoint = Path(checkpoint_val).expanduser().resolve()
        else:
            search_dirs = [
                Path.home() / ".local/share/kdenlive",
                Path.home() / ".cache/kdenlive",
                Path.home() / ".cache/torch/hub/checkpoints",
                Path.home() / ".cache/huggingface",
                Path.cwd(),
            ]
            candidates = []
            for s_dir in search_dirs:
                if s_dir.is_dir():
                    candidates.extend(s_dir.rglob("sam2*.pt"))
                    candidates.extend(s_dir.rglob("*.pt"))
            if candidates:
                checkpoint = candidates[0]
            else:
                checkpoint = require_file(checkpoint_val, "SAM2 checkpoint")

        config_name = infer_sam2_config(checkpoint, backend.get("config_path"))
        device = choose_device(backend.get("device", "auto"))

        frame_start = int(source_info.get("frame_start", 1))
        frame_end = int(source_info.get("frame_end", frame_start))
        prompt_frame = int(prompt_info.get("frame", frame_start))
        preview_only = bool(prompt_info.get("preview_only", False))

        output_dir = Path(output_info.get("directory")).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        pattern = output_info.get("pattern", "mask_%06d.png")
        soft_mask = bool(output_info.get("soft_mask", False))

        import torch
        from sam2.build_sam import build_sam2_video_predictor

        # 1. Load Predictor (once!)
        if self.predictor is None or self.checkpoint != checkpoint or self.config_name != config_name or self.device != device:
            perf.start("2. SAM2 Model Loading")
            log(f"Daemon: Loading SAM2 model ({checkpoint.name})...")
            self.predictor = build_sam2_video_predictor(config_name, str(checkpoint), device=device)
            self.checkpoint = checkpoint
            self.config_name = config_name
            self.device = device
            self.cached_source = None  # Force frame re-extraction
            perf.stop("2. SAM2 Model Loading")

        # 2. Extract & Init State (cached if same clip & frame range!)
        target_range = (prompt_frame, prompt_frame) if preview_only else (frame_start, frame_end)
        if self.cached_source != source or self.cached_range != target_range or self.cached_inference_state is None:
            perf.start("3. Video Frame Extraction & init_state")
            log(f"Daemon: Extracting frames for {source.name}...")
            if self.cached_temp_dir:
                try:
                    self.cached_temp_dir.cleanup()
                except Exception:
                    pass
            
            self.cached_temp_dir = tempfile.TemporaryDirectory(prefix="blender_sam2_daemon_")
            self.cached_jpeg_dir = Path(self.cached_temp_dir.name) / "frames"

            if preview_only:
                num_frames = extract_frame_range(source, self.cached_jpeg_dir, prompt_frame, prompt_frame)
            else:
                num_frames = extract_frame_range(source, self.cached_jpeg_dir, frame_start, frame_end)

            self.cached_inference_state = self.predictor.init_state(
                video_path=str(self.cached_jpeg_dir),
                offload_video_to_cpu=True,
                offload_state_to_cpu=True,
                async_loading_frames=False,
            )
            self.cached_source = source
            self.cached_range = target_range
            perf.stop("3. Video Frame Extraction & init_state")

        # 3. Fast Point Inference (<100ms!)
        perf.start("4. Single-Frame SAM2 Inference")
        inference_state = self.cached_inference_state
        local_prompt_index = 0 if preview_only else (prompt_frame - frame_start)

        width = int(inference_state["video_width"])
        height = int(inference_state["video_height"])

        positive = prompt_info.get("positive", [])
        negative = prompt_info.get("negative", [])
        coord_space = prompt_info.get("coordinate_space", "normalized_bottom_left")

        positive_px = normalized_points_to_pixels(positive, width, height, coord_space)
        negative_px = normalized_points_to_pixels(negative, width, height, coord_space)

        points = np.concatenate([positive_px, negative_px], axis=0)
        labels = np.concatenate([
            np.ones(len(positive_px), dtype=np.int32),
            np.zeros(len(negative_px), dtype=np.int32),
        ], axis=0)

        object_id = 1
        with torch.inference_mode():
            _, object_ids, prompt_logits = self.predictor.add_new_points_or_box(
                inference_state=inference_state,
                frame_idx=local_prompt_index,
                obj_id=object_id,
                points=points,
                labels=labels,
            )

            prompt_output = output_dir / output_filename(pattern, prompt_frame)
            save_matte(prompt_logits, object_ids, object_id, prompt_output, soft_mask)
            perf.stop("4. Single-Frame SAM2 Inference")
            self.last_perf_stats = perf.timers.copy()

            if preview_only:
                perf.print_summary()
                return {"status": "ok", "mode": "preview", "prompt_frame": prompt_frame, "output": str(prompt_output)}

            # Track full video if not preview_only
            perf.start("5. Video Tracking Propagation")
            written = {local_prompt_index}

            def write_result(local_index, ids, logits):
                blender_frame = frame_start + int(local_index)
                if blender_frame > frame_end:
                    return
                dest = output_dir / output_filename(pattern, blender_frame)
                save_matte(logits, ids, object_id, dest, soft_mask)
                written.add(int(local_index))

            for local_idx, ids, logits in self.predictor.propagate_in_video(
                inference_state, start_frame_idx=local_prompt_index, reverse=False
            ):
                write_result(local_idx, ids, logits)

            if local_prompt_index > 0:
                for local_idx, ids, logits in self.predictor.propagate_in_video(
                    inference_state, start_frame_idx=local_prompt_index, reverse=True
                ):
                    write_result(local_idx, ids, logits)

            perf.stop("5. Video Tracking Propagation")
            self.last_perf_stats = perf.timers.copy()
            perf.print_summary()
            return {"status": "ok", "mode": "full", "frames": len(written), "output_dir": str(output_dir)}


import socket


def run_daemon_server(host: str = "127.0.0.1", port: int = 18950) -> None:
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server_sock.bind((host, port))
        server_sock.listen(5)
    except OSError as err:
        if err.errno == 98 or "address" in str(err).lower():
            log(f"🟢 SAM2 Daemon is ALREADY active and listening on {host}:{port}.")
            return
        raise

    daemon = SAM2Daemon()
    log(f"🟢 SAM2 Persistent Worker Daemon active and listening on {host}:{port}")

    while True:
        try:
            conn, addr = server_sock.accept()
            with conn:
                data = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"\n" in chunk:
                        break
                decoded = data.decode("utf-8", errors="ignore").strip()
                if decoded.startswith("GET ") or decoded.startswith("POST "):
                    dash_body = daemon.generate_dashboard_html()
                    html = (
                        "HTTP/1.1 200 OK\r\n"
                        "Content-Type: text/html; charset=utf-8\r\n"
                        "Connection: close\r\n\r\n" + dash_body
                    )
                    conn.sendall(html.encode("utf-8"))
                    continue

                req = json.loads(decoded)
                res = daemon.process_request(req)
                conn.sendall(json.dumps(res).encode("utf-8") + b"\n")
        except Exception as exc:
            log(f"Daemon Request Error: {exc}")


def main() -> int:
    if "--daemon" in sys.argv:
        port = 18950
        if "--port" in sys.argv:
            idx = sys.argv.index("--port")
            if idx + 1 < len(sys.argv):
                port = int(sys.argv[idx + 1])
        run_daemon_server("127.0.0.1", port)
        return 0

    if len(sys.argv) != 2:
        print(
            "Usage: sam2_worker.py /path/to/request.json OR sam2_worker.py --daemon [--port 18950]",
            file=sys.stderr,
        )
        return 2

    request_path = Path(sys.argv[1]).expanduser().resolve()

    try:
        request = load_request(request_path)
        run(request)
        return 0
    except SystemExit:
        raise
    except Exception:
        log("UNHANDLED ERROR:")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

