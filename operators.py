# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.types import Operator
from bpy.props import EnumProperty, IntProperty, StringProperty

from pathlib import Path
import json
import os
import subprocess
import sys

from .utils import addon_preferences, current_clip, absolute_path, normalized_click, redraw_clip_editors, resolve_python_executable
from .compositor import setup_compositor_tree
from .properties import save_settings_to_json


class AIROTO_OT_pick_point(Operator):
    bl_idname = "airoto.pick_point"
    bl_label = "Pick AI Roto Point"
    bl_description = "Click a point directly on the current Movie Clip"
    bl_options = {'INTERNAL'}

    kind: EnumProperty(
        items=[
            ('POSITIVE', "Foreground", "Point belonging to the subject"),
            ('NEGATIVE', "Background", "Point not belonging to the subject"),
        ],
        default='POSITIVE',
    )

    def invoke(self, context, event):
        if not context.area or context.area.type != 'CLIP_EDITOR':
            self.report({'ERROR'}, "Run this from the Movie Clip Editor")
            return {'CANCELLED'}
        if not current_clip(context):
            self.report({'ERROR'}, "No Movie Clip is loaded")
            return {'CANCELLED'}

        context.window_manager.modal_handler_add(self)

        if hasattr(context.window, "cursor_modal_set"):
            cursor_type = 'EYEDROPPER' if self.kind == 'POSITIVE' else 'CROSSHAIR'
            context.window.cursor_modal_set(cursor_type)

        context.workspace.status_text_set(
            "AI Roto: click the subject" if self.kind == 'POSITIVE'
            else "AI Roto: click a background point"
        )
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            if hasattr(context.window, "cursor_modal_restore"):
                context.window.cursor_modal_restore()
            context.workspace.status_text_set(None)
            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            x, y = normalized_click(context, event)
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                self.report({'WARNING'}, "Click inside the video image")
                return {'RUNNING_MODAL'}

            s = context.scene.airoto
            s.prompt_frame = context.scene.frame_current

            item = s.points.add()
            item.x = float(x)
            item.y = float(y)
            item.kind = self.kind
            s.active_point_index = len(s.points) - 1

            if self.kind == 'POSITIVE':
                s.positive_x = float(x)
                s.positive_y = float(y)
                s.positive_set = True
            else:
                s.negative_x = float(x)
                s.negative_y = float(y)
                s.negative_set = True

            if hasattr(context.window, "cursor_modal_restore"):
                context.window.cursor_modal_restore()

            s.is_previewing = False
            context.workspace.status_text_set(None)
            redraw_clip_editors(context)
            self.report({'INFO'}, f"Added {self.kind.title()} point #{len(s.points)}: ({x:.4f}, {y:.4f}) @ frame {s.prompt_frame}")

            if getattr(s, "auto_track_all", False) and (s.positive_set or any(p.kind == 'POSITIVE' for p in s.points)):
                try:
                    bpy.ops.airoto.generate()
                except Exception as exc:
                    print(f"Auto-track error: {exc}")
            elif s.auto_preview and (s.positive_set or any(p.kind == 'POSITIVE' for p in s.points)):
                try:
                    bpy.ops.airoto.preview()
                except Exception as exc:
                    print(f"Auto-preview error: {exc}")

            return {'FINISHED'}

        return {'RUNNING_MODAL'}


class AIROTO_OT_remove_point(Operator):
    bl_idname = "airoto.remove_point"
    bl_label = "Remove Selected Point"
    bl_description = "Remove the selected point from the prompt list"

    index: IntProperty(default=-1)

    def execute(self, context):
        s = context.scene.airoto
        if not s.points:
            return {'CANCELLED'}
        idx = self.index if self.index >= 0 else s.active_point_index
        if 0 <= idx < len(s.points):
            s.points.remove(idx)
            s.active_point_index = max(0, min(idx, len(s.points) - 1))
            has_pos = any(p.kind == 'POSITIVE' for p in s.points)
            s.positive_set = has_pos
            s.negative_set = any(p.kind == 'NEGATIVE' for p in s.points)
            redraw_clip_editors(context)
            if s.auto_preview and has_pos:
                try:
                    bpy.ops.airoto.preview()
                except Exception:
                    pass
        return {'FINISHED'}


class AIROTO_OT_clear_all_points(Operator):
    bl_idname = "airoto.clear_all_points"
    bl_label = "Clear All Points"
    bl_description = "Clear all picked foreground and background prompt points"

    def execute(self, context):
        s = context.scene.airoto
        s.points.clear()
        s.positive_set = False
        s.negative_set = False
        redraw_clip_editors(context)
        return {'FINISHED'}


class AIROTO_OT_clear_negative(Operator):
    bl_idname = "airoto.clear_negative"
    bl_label = "Clear Background Points"

    def execute(self, context):
        s = context.scene.airoto
        indices_to_remove = [i for i, pt in enumerate(s.points) if pt.kind == 'NEGATIVE']
        for i in reversed(indices_to_remove):
            s.points.remove(i)
        s.negative_set = False
        s.active_point_index = max(0, len(s.points) - 1)
        redraw_clip_editors(context)
        return {'FINISHED'}


class AIROTO_OT_sync_clip_range(Operator):
    bl_idname = "airoto.sync_clip_range"
    bl_label = "Sync Clip Range"
    bl_description = "Set start and end frames from the active Movie Clip duration"

    def execute(self, context):
        clip = current_clip(context)
        if not clip:
            self.report({'ERROR'}, "No Movie Clip is loaded")
            return {'CANCELLED'}

        s = context.scene.airoto
        start = context.scene.frame_start
        duration = clip.frame_duration
        if duration > 0:
            s.start_frame = start
            s.end_frame = start + duration - 1
            self.report({'INFO'}, f"Set frame range to {s.start_frame} .. {s.end_frame}")
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "Could not determine clip frame duration")
            return {'CANCELLED'}


import socket
import time

_daemon_process = None


def send_daemon_request(request_dict: dict, port: int = 18950, timeout: float = 5.0) -> dict | None:
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        with sock:
            payload = (json.dumps(request_dict) + "\n").encode("utf-8")
            sock.sendall(payload)
            response_bytes = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_bytes += chunk
                if b"\n" in chunk:
                    break
            if response_bytes:
                return json.loads(response_bytes.decode("utf-8").strip())
    except Exception:
        pass
    return None


def ensure_daemon_running(python_exe: str, worker: str, port: int = 18950) -> bool:
    global _daemon_process
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=0.05)
        s.close()
        return True
    except Exception:
        pass

    try:
        _daemon_process = subprocess.Popen(
            [python_exe, worker, "--daemon", "--port", str(port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(Path(worker).parent),
        )
        time.sleep(0.1)
        return True
    except Exception as exc:
        print(f"Could not launch daemon: {exc}")

    return False


class AIROTO_OT_preview(Operator):
    bl_idname = "airoto.preview"
    bl_label = "Preview Current Frame"
    bl_description = "Run fast single-frame prediction to preview captured object on the prompt frame"

    _process = None
    _timer = None
    _log_handle = None

    def execute(self, context):
        save_settings_to_json(context)
        prefs = addon_preferences(context)
        s = context.scene.airoto
        clip = current_clip(context)

        if prefs is None or clip is None or not s.positive_set:
            return {'CANCELLED'}

        python_exe = resolve_python_executable(prefs)
        worker = absolute_path(prefs.worker_script) if prefs and prefs.worker_script else ""

        if not worker:
            addon_dir = os.path.dirname(__file__)
            default_worker = os.path.join(addon_dir, "sam2_worker.py")
            if os.path.isfile(default_worker):
                worker = default_worker

        if not python_exe or not os.path.isfile(python_exe) or not worker or not os.path.isfile(worker):
            return {'CANCELLED'}

        source_path = absolute_path(clip.filepath)
        if not source_path or not os.path.isfile(source_path):
            return {'CANCELLED'}

        output_dir = Path(absolute_path(s.output_dir))
        if s.subfolder_name.strip():
            output_dir = output_dir / s.subfolder_name.strip()
        output_dir.mkdir(parents=True, exist_ok=True)

        device = prefs.other_device if prefs.device == 'OTHER' else prefs.device.lower()
        pos_points = [[float(pt.x), float(pt.y)] for pt in s.points if pt.kind == 'POSITIVE']
        neg_points = [[float(pt.x), float(pt.y)] for pt in s.points if pt.kind == 'NEGATIVE']
        if not pos_points and s.positive_set:
            pos_points = [[float(s.positive_x), float(s.positive_y)]]
        if not neg_points and s.negative_set:
            neg_points = [[float(s.negative_x), float(s.negative_y)]]

        request = {
            "schema_version": 1,
            "source": {
                "path": source_path,
                "frame_start": int(s.prompt_frame),
                "frame_end": int(s.prompt_frame),
            },
            "prompt": {
                "frame": int(s.prompt_frame),
                "positive": pos_points,
                "negative": neg_points,
                "coordinate_space": "normalized_bottom_left",
                "preview_only": True,
            },
            "backend": {
                "device": device,
                "model_path": absolute_path(prefs.model_path),
                "config_path": absolute_path(prefs.config_path),
            },
            "output": {
                "directory": str(output_dir),
                "pattern": "mask_%06d.png",
                "matte": "white_subject_black_background",
            },
        }

        # 1. Try Persistent Worker Daemon Mode (Fast 100ms IPC socket execution)
        if getattr(prefs, "use_daemon", True):
            port = getattr(prefs, "daemon_port", 18950)
            if ensure_daemon_running(python_exe, worker, port):
                s.is_previewing = True
                context.workspace.status_text_set("AI Roto: Predicting SAM2 single-frame preview (Daemon)...")
                redraw_clip_editors(context)

                res = send_daemon_request(request, port=port, timeout=5.0)
                s.is_previewing = False
                context.workspace.status_text_set(None)

                if res and res.get("status") == "ok":
                    s.last_log = str(output_dir / "worker_preview.log")
                    redraw_clip_editors(context)
                    self.report({'INFO'}, "Single-frame preview generated via Daemon (Instant)")
                    return {'FINISHED'}

        # 2. Subprocess Fallback Mode
        request_path = output_dir / "request_preview.json"
        log_path = output_dir / "worker_preview.log"
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")

        s.is_previewing = True
        context.workspace.status_text_set("AI Roto: Predicting SAM2 single-frame preview...")

        try:
            self._log_handle = open(log_path, "w", encoding="utf-8")
            self._process = subprocess.Popen(
                [python_exe, worker, str(request_path)],
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                cwd=str(Path(worker).parent),
            )
        except Exception as exc:
            s.is_previewing = False
            context.workspace.status_text_set(None)
            self.report({'ERROR'}, f"Could not launch preview worker: {exc}")
            return {'CANCELLED'}

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        redraw_clip_editors(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self._finish(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
            redraw_clip_editors(context)
            code = self._process.poll()
            if code is None:
                return {'RUNNING_MODAL'}

            self._finish(context)
            if code == 0:
                redraw_clip_editors(context)
                self.report({'INFO'}, "Single-frame preview generated")
                return {'FINISHED'}

            self.report({'WARNING'}, f"Preview worker exited with code {code}")
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def _finish(self, context):
        s = context.scene.airoto
        s.is_previewing = False
        context.workspace.status_text_set(None)
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None
        redraw_clip_editors(context)


class AIROTO_OT_step_frame(Operator):
    bl_idname = "airoto.step_frame"
    bl_label = "Step Frame & Track"
    bl_description = "Track SAM2 mask for 1 frame in the chosen direction and advance the timeline playhead"

    delta: IntProperty(default=1)

    def execute(self, context):
        prefs = addon_preferences(context)
        s = context.scene.airoto
        clip = current_clip(context)

        if prefs is None or clip is None or not (s.positive_set or len(s.points) > 0):
            self.report({'WARNING'}, "Pick a foreground point first")
            return {'CANCELLED'}

        python_exe = resolve_python_executable(prefs)
        worker = absolute_path(prefs.worker_script) if prefs and prefs.worker_script else ""
        if not worker:
            addon_dir = os.path.dirname(__file__)
            default_worker = os.path.join(addon_dir, "sam2_worker.py")
            if os.path.isfile(default_worker):
                worker = default_worker

        source_path = absolute_path(clip.filepath)
        if not source_path or not os.path.isfile(source_path):
            self.report({'ERROR'}, "The Movie Clip source file cannot be found")
            return {'CANCELLED'}

        output_dir = Path(absolute_path(s.output_dir))
        if s.subfolder_name.strip():
            output_dir = output_dir / s.subfolder_name.strip()
        output_dir.mkdir(parents=True, exist_ok=True)

        curr_frame = context.scene.frame_current
        target_frame = curr_frame + self.delta
        s.prompt_frame = curr_frame

        direction = "forwards" if self.delta > 0 else "backwards"
        pos_points = [[float(pt.x), float(pt.y)] for pt in s.points if pt.kind == 'POSITIVE']
        neg_points = [[float(pt.x), float(pt.y)] for pt in s.points if pt.kind == 'NEGATIVE']
        if not pos_points and s.positive_set:
            pos_points = [[float(s.positive_x), float(s.positive_y)]]
        if not neg_points and s.negative_set:
            neg_points = [[float(s.negative_x), float(s.negative_y)]]

        request = {
            "schema_version": 1,
            "source": {
                "path": source_path,
                "frame_start": int(min(curr_frame, target_frame)),
                "frame_end": int(max(curr_frame, target_frame)),
            },
            "prompt": {
                "frame": int(curr_frame),
                "positive": pos_points,
                "negative": neg_points,
                "direction": direction,
                "single_step": True,
                "max_frames": 1,
                "coordinate_space": "normalized_bottom_left",
            },
            "backend": {
                "device": prefs.other_device if prefs.device == 'OTHER' else prefs.device.lower(),
                "model_path": absolute_path(prefs.model_path),
                "config_path": absolute_path(prefs.config_path),
            },
            "output": {
                "directory": str(output_dir),
                "pattern": "mask_%06d.png",
                "matte": "white_subject_black_background",
            },
        }

        port = getattr(prefs, "daemon_port", 18950)
        ensure_daemon_running(python_exe, worker, port)
        send_daemon_request(request, port=port, timeout=5.0)

        context.scene.frame_set(target_frame)
        s.prompt_frame = target_frame
        redraw_clip_editors(context)
        self.report({'INFO'}, f"Tracked 1 frame ({direction}): Advanced playhead to Frame {target_frame}")
        return {'FINISHED'}


class AIROTO_OT_generate(Operator):
    bl_idname = "airoto.generate"
    bl_label = "Generate Matte"
    bl_description = "Launch the configured external segmentation worker"

    direction: StringProperty(name="Direction", default="")

    _process = None
    _timer = None
    _log_handle = None

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        prefs = addon_preferences(context)
        s = context.scene.airoto
        clip = current_clip(context)

        if prefs is None:
            self.report({'ERROR'}, "AI Roto Bridge preferences are unavailable")
            return {'CANCELLED'}
        if clip is None:
            self.report({'ERROR'}, "Open the source clip in the Movie Clip Editor")
            return {'CANCELLED'}
        if not s.positive_set:
            self.report({'ERROR'}, "Pick a foreground point first")
            return {'CANCELLED'}

        python_exe = resolve_python_executable(prefs)
        worker = absolute_path(prefs.worker_script) if prefs and prefs.worker_script else ""

        if not worker:
            addon_dir = os.path.dirname(__file__)
            default_worker = os.path.join(addon_dir, "sam2_worker.py")
            if os.path.isfile(default_worker):
                worker = default_worker

        if not python_exe or not os.path.isfile(python_exe):
            self.report({'ERROR'}, "Set a valid External Python in extension preferences")
            return {'CANCELLED'}
        if not worker or not os.path.isfile(worker):
            self.report({'ERROR'}, "Set a valid Worker Script in extension preferences")
            return {'CANCELLED'}

        source_path = absolute_path(clip.filepath)
        if not source_path or not os.path.isfile(source_path):
            self.report({'ERROR'}, "The Movie Clip source file cannot be found")
            return {'CANCELLED'}

        output_dir = Path(absolute_path(s.output_dir))
        if s.subfolder_name.strip():
            output_dir = output_dir / s.subfolder_name.strip()
        output_dir.mkdir(parents=True, exist_ok=True)

        if s.end_frame < s.start_frame:
            self.report({'ERROR'}, "End frame must be >= start frame")
            return {'CANCELLED'}

        device = prefs.other_device if prefs.device == 'OTHER' else prefs.device.lower()
        pos_points = [[float(pt.x), float(pt.y)] for pt in s.points if pt.kind == 'POSITIVE']
        neg_points = [[float(pt.x), float(pt.y)] for pt in s.points if pt.kind == 'NEGATIVE']
        if not pos_points and s.positive_set:
            pos_points = [[float(s.positive_x), float(s.positive_y)]]
        if not neg_points and s.negative_set:
            neg_points = [[float(s.negative_x), float(s.negative_y)]]

        chosen_dir = self.direction if self.direction else getattr(s, "track_direction", "BOTH")

        request = {
            "schema_version": 1,
            "source": {
                "path": source_path,
                "frame_start": int(s.start_frame),
                "frame_end": int(s.end_frame),
            },
            "prompt": {
                "frame": int(s.prompt_frame),
                "positive": pos_points,
                "negative": neg_points,
                "direction": chosen_dir.lower(),
                "coordinate_space": "normalized_bottom_left",
            },
            "backend": {
                "device": device,
                "model_path": absolute_path(prefs.model_path),
                "config_path": absolute_path(prefs.config_path),
            },
            "output": {
                "directory": str(output_dir),
                "pattern": "mask_%06d.png",
                "matte": "white_subject_black_background",
            },
        }

        request_path = output_dir / "request.json"
        log_path = output_dir / "worker.log"
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")

        s.last_request = str(request_path)
        s.last_log = str(log_path)
        s.is_generating = True
        s.progress_pct = 0.0
        s.status_msg = "Starting SAM2 tracking sequence..."

        # 1. Try Persistent Worker Daemon Mode (Background socket execution)
        if getattr(prefs, "use_daemon", True):
            port = getattr(prefs, "daemon_port", 18950)
            if ensure_daemon_running(python_exe, worker, port):
                import threading

                def run_bg():
                    send_daemon_request(request, port=port, timeout=3600.0)

                threading.Thread(target=run_bg, daemon=True).start()
                self._process = None

                wm = context.window_manager
                self._timer = wm.event_timer_add(0.2, window=context.window)
                wm.modal_handler_add(self)
                context.workspace.status_text_set("AI Roto: tracking sequence in background... (Esc cancels)")
                return {'RUNNING_MODAL'}

        try:
            self._log_handle = open(log_path, "w", encoding="utf-8")
            self._process = subprocess.Popen(
                [python_exe, worker, str(request_path)],
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                cwd=str(Path(worker).parent),
            )
        except Exception as exc:
            s.is_generating = False
            s.progress_pct = -1.0
            if self._log_handle:
                self._log_handle.close()
            self.report({'ERROR'}, f"Could not start worker: {exc}")
            return {'CANCELLED'}

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        context.workspace.status_text_set("AI Roto: generating matte... Esc cancels")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        s = context.scene.airoto

        if event.type == 'ESC':
            if self._process and self._process.poll() is None:
                self._process.terminate()
            self._finish(context)
            self.report({'WARNING'}, "AI Roto generation cancelled")
            return {'CANCELLED'}

        if event.type == 'TIMER':
            if s.last_log and os.path.isfile(s.last_log):
                try:
                    with open(s.last_log, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                        for line in reversed(lines):
                            line_str = line.strip()
                            if "PROGRESS" in line_str:
                                try:
                                    parts = line_str.split("PROGRESS")[1].strip().split()
                                    nums = parts[0].split("/")
                                    cur_f, total_f = float(nums[0]), float(nums[1])
                                    pct = (cur_f / total_f) * 100.0 if total_f > 0 else 0.0
                                    s.progress_pct = pct
                                    s.status_msg = f"Tracking: {int(cur_f)}/{int(total_f)} frames ({pct:.0f}%)"
                                    context.workspace.status_text_set(f"AI Roto: {s.status_msg} (Esc to cancel)")
                                    redraw_clip_editors(context)
                                    break
                                except Exception:
                                    pass
                            elif any(k in line_str for k in ("Propagating", "Extracting", "Building SAM2", "Loading SAM2")):
                                s.status_msg = line_str
                                context.workspace.status_text_set(f"AI Roto: {line_str} (Esc to cancel)")
                                redraw_clip_editors(context)
                                break
                except Exception:
                    pass

            if self._process is None:
                if s.progress_pct >= 100.0 or (s.progress_pct > 0 and s.progress_pct >= 99.9):
                    self._finish(context)
                    self.report({'INFO'}, "Sequence tracking finished (Background Daemon)")
                    if s.auto_load_compositor:
                        try:
                            bpy.ops.airoto.load_matte()
                        except Exception:
                            pass
                    redraw_clip_editors(context)
                    return {'FINISHED'}
                return {'RUNNING_MODAL'}

            code = self._process.poll()
            if code is None:
                return {'RUNNING_MODAL'}

            self._finish(context)
            if code == 0:
                self.report({'INFO'}, "Matte generation finished")
                if s.auto_load_compositor:
                    try:
                        bpy.ops.airoto.load_matte()
                    except Exception:
                        pass
                redraw_clip_editors(context)
                return {'FINISHED'}

            self.report({'ERROR'}, f"Worker exited with code {code}; see worker.log")
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def _finish(self, context):
        s = context.scene.airoto
        s.is_generating = False
        s.progress_pct = -1.0
        s.status_msg = ""
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None
        context.workspace.status_text_set(None)


class AIROTO_OT_load_matte(Operator):
    bl_idname = "airoto.load_matte"
    bl_label = "Load & Combine Mattes"
    bl_description = "Load generated PNG masks and combine all mask sequences into an interactive composite overlay node graph"

    def execute(self, context):
        seq_count, file_count, err = setup_compositor_tree(context)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        self.report({'INFO'}, f"Loaded & combined {seq_count} mask sequence(s) ({file_count} total frames) into Compositor")
        return {'FINISHED'}


class AIROTO_OT_open_log(Operator):
    bl_idname = "airoto.open_log"
    bl_label = "Show Worker Log"
    bl_description = "Load worker.log into Blender's Text Editor"

    def execute(self, context):
        path = absolute_path(context.scene.airoto.last_log)
        if not path or not os.path.isfile(path):
            self.report({'ERROR'}, "No worker log is available")
            return {'CANCELLED'}

        name = "AI Roto worker.log"
        text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
        text.clear()
        text.write(Path(path).read_text(encoding="utf-8", errors="replace"))
        self.report({'INFO'}, "Worker log loaded as a Blender Text datablock")
        return {'FINISHED'}
