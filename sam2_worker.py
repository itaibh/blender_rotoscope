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


def log(message: str) -> None:
    print(message, flush=True)


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

    if requested == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ):
            return torch.device("mps")
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
            "Choose CPU, CUDA, Auto, or provide a PyTorch device string."
        )

    # Permit advanced PyTorch device strings such as cuda:1.
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


def extract_frame_range(
    source: Path,
    destination: Path,
    frame_start: int,
    frame_end: int,
) -> int:
    """
    Extract a Blender-style inclusive frame range.

    For this first version we assume Blender frame 1 corresponds to source
    video frame index 0. Therefore frame N maps to ffmpeg frame n=N-1.
    """

    if frame_start < 1:
        fail("frame_start must be >= 1")
    if frame_end < frame_start:
        fail("frame_end must be >= frame_start")

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
    # Import these here so configuration errors are reported in worker.log.
    try:
        import torch
        import sam2  # noqa: F401
        from sam2.build_sam import build_sam2_video_predictor
    except Exception as exc:
        fail(
            "Could not import PyTorch/SAM2 from this Python environment.\n"
            f"{type(exc).__name__}: {exc}"
        )

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

        if local_prompt_index >= num_frames:
            fail(
                f"Prompt frame maps to extracted frame {local_prompt_index}, "
                f"but only {num_frames} frames were extracted."
            )

        log("Building SAM2 video predictor...")

        predictor = build_sam2_video_predictor(
            config_name,
            str(checkpoint),
            device=device,
        )

        log("Loading video frames into SAM2...")

        # Keeping source frames/state on CPU is conservative and is especially
        # suitable for this Intel/CPU use case.
        inference_state = predictor.init_state(
            video_path=str(jpeg_dir),
            offload_video_to_cpu=True,
            offload_state_to_cpu=True,
            async_loading_frames=False,
        )

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

            if preview_only:
                log("PREVIEW_FINISHED")
                return

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

        log(
            f"DONE: generated {len(written)} matte frames "
            f"in {output_dir}"
        )


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "Usage: sam2_worker.py /path/to/request.json",
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
