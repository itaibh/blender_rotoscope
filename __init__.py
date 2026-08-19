# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty

from pathlib import Path
import json
import os
import subprocess
import sys


def addon_preferences(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def current_clip(context):
    if context.area and context.area.type == 'CLIP_EDITOR':
        return context.space_data.clip
    return None


def absolute_path(value: str) -> str:
    if not value:
        return ""
    return os.path.abspath(bpy.path.abspath(value))


def normalized_click(context, event):
    x, y = context.region.view2d.region_to_view(
        event.mouse_region_x,
        event.mouse_region_y,
    )
    return float(x), float(y)


class AIROTO_Preferences(AddonPreferences):
    bl_idname = __package__

    python_executable: StringProperty(
        name="External Python",
        description="Python executable belonging to the AI environment",
        subtype='FILE_PATH',
    )
    worker_script: StringProperty(
        name="Worker Script",
        description="External worker launched as: python worker.py request.json",
        subtype='FILE_PATH',
    )
    model_path: StringProperty(
        name="Model",
        description="Optional model/checkpoint path passed to the worker",
        subtype='FILE_PATH',
    )
    config_path: StringProperty(
        name="Model Config",
        description="Optional model configuration path passed to the worker",
        subtype='FILE_PATH',
    )
    device: EnumProperty(
        name="Device",
        items=[
            ('AUTO', "Auto", "Let the worker choose"),
            ('CPU', "CPU", "Use CPU"),
            ('CUDA', "CUDA", "Use NVIDIA CUDA"),
            ('OPENVINO', "OpenVINO", "Use OpenVINO if supported by the worker"),
            ('OTHER', "Other", "Worker-specific device"),
        ],
        default='AUTO',
    )
    other_device: StringProperty(
        name="Other Device",
        description="Worker-specific device string when Device is Other",
        default="",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "python_executable")
        layout.prop(self, "worker_script")
        layout.separator()
        layout.prop(self, "model_path")
        layout.prop(self, "config_path")
        layout.prop(self, "device")
        if self.device == 'OTHER':
            layout.prop(self, "other_device")


class AIROTO_Settings(PropertyGroup):
    output_dir: StringProperty(
        name="Output",
        description="Folder where matte PNG files will be generated",
        subtype='DIR_PATH',
        default="//ai_roto_masks",
    )
    start_frame: IntProperty(name="Start", default=1, min=0)
    end_frame: IntProperty(name="End", default=250, min=0)
    prompt_frame: IntProperty(name="Prompt Frame", default=1, min=0)

    positive_x: FloatProperty(default=0.5)
    positive_y: FloatProperty(default=0.5)
    positive_set: BoolProperty(default=False)

    negative_x: FloatProperty(default=0.0)
    negative_y: FloatProperty(default=0.0)
    negative_set: BoolProperty(default=False)

    last_request: StringProperty(default="")
    last_log: StringProperty(default="")

    is_generating: BoolProperty(default=False)
    progress_pct: FloatProperty(name="Progress", default=-1.0, min=0.0, max=100.0, subtype='PERCENTAGE')
    status_msg: StringProperty(default="")


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
        context.workspace.status_text_set(
            "AI Roto: click the subject" if self.kind == 'POSITIVE'
            else "AI Roto: click a background point"
        )
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'}:
            context.workspace.status_text_set(None)
            return {'CANCELLED'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            x, y = normalized_click(context, event)
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                self.report({'WARNING'}, "Click inside the video image")
                return {'RUNNING_MODAL'}

            s = context.scene.airoto
            s.prompt_frame = context.scene.frame_current

            if self.kind == 'POSITIVE':
                s.positive_x = x
                s.positive_y = y
                s.positive_set = True
            else:
                s.negative_x = x
                s.negative_y = y
                s.negative_set = True

            context.workspace.status_text_set(None)
            context.area.tag_redraw()
            self.report({'INFO'}, f"{self.kind.title()} point: ({x:.4f}, {y:.4f})")
            return {'FINISHED'}

        return {'RUNNING_MODAL'}


class AIROTO_OT_clear_negative(Operator):
    bl_idname = "airoto.clear_negative"
    bl_label = "Clear Background Point"

    def execute(self, context):
        context.scene.airoto.negative_set = False
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


class AIROTO_OT_generate(Operator):
    bl_idname = "airoto.generate"
    bl_label = "Generate Matte"
    bl_description = "Launch the configured external segmentation worker"

    _process = None
    _timer = None
    _log_handle = None

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

        python_exe = absolute_path(prefs.python_executable) if prefs and prefs.python_executable else sys.executable
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
        output_dir.mkdir(parents=True, exist_ok=True)

        if s.end_frame < s.start_frame:
            self.report({'ERROR'}, "End frame must be >= start frame")
            return {'CANCELLED'}

        device = prefs.other_device if prefs.device == 'OTHER' else prefs.device.lower()
        request = {
            "schema_version": 1,
            "source": {
                "path": source_path,
                "frame_start": int(s.start_frame),
                "frame_end": int(s.end_frame),
            },
            "prompt": {
                "frame": int(s.prompt_frame),
                "positive": [[float(s.positive_x), float(s.positive_y)]],
                "negative": (
                    [[float(s.negative_x), float(s.negative_y)]]
                    if s.negative_set else []
                ),
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
        s.status_msg = "Starting SAM2 worker..."

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
            # Read progress from worker.log
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
                                    if context.area:
                                        context.area.tag_redraw()
                                    break
                                except Exception:
                                    pass
                            elif any(k in line_str for k in ("Propagating", "Extracting", "Building SAM2", "Loading SAM2")):
                                s.status_msg = line_str
                                context.workspace.status_text_set(f"AI Roto: {line_str} (Esc to cancel)")
                                if context.area:
                                    context.area.tag_redraw()
                                break
                except Exception:
                    pass

            code = self._process.poll()
            if code is None:
                return {'RUNNING_MODAL'}

            self._finish(context)
            if code == 0:
                self.report({'INFO'}, "Matte generation finished")
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
    bl_label = "Load Matte in Compositor"
    bl_description = "Load generated PNG masks as an image-sequence compositor node"

    def execute(self, context):
        s = context.scene.airoto
        output_dir = Path(absolute_path(s.output_dir))
        files = sorted(output_dir.glob("mask_*.png"))

        if not files:
            self.report({'ERROR'}, "No mask_*.png files found in the output folder")
            return {'CANCELLED'}

        try:
            image = bpy.data.images.load(str(files[0]), check_existing=False)
            image.name = "AI Roto Matte"
            image.source = 'SEQUENCE'
        except Exception as exc:
            self.report({'ERROR'}, f"Could not load matte sequence: {exc}")
            return {'CANCELLED'}

        scene = context.scene

        if hasattr(scene, "use_nodes"):
            scene.use_nodes = True

        # Blender 5.2 uses scene.compositing_node_group
        if hasattr(scene, "compositing_node_group"):
            if scene.compositing_node_group is None:
                scene.compositing_node_group = bpy.data.node_groups.new(
                    name="Compositing", type='CompositorNodeTree'
                )
            tree = scene.compositing_node_group
        elif hasattr(scene, "compositor"):
            if getattr(scene.compositor, "node_tree", None) is None:
                scene.compositor.node_tree = bpy.data.node_groups.new(
                    name="Compositing", type='CompositorNodeTree'
                )
            tree = scene.compositor.node_tree
        elif hasattr(scene, "node_tree"):
            if scene.node_tree is None:
                scene.node_tree = bpy.data.node_groups.new(
                    name="Compositing", type='CompositorNodeTree'
                )
            tree = scene.node_tree
        else:
            tree = bpy.data.node_groups.new(
                name="AI Roto Matte Tree", type='CompositorNodeTree'
            )

        try:
            node = tree.nodes.new("CompositorNodeImage")
        except Exception:
            node = tree.nodes.new("CMP_NODE_IMAGE")

        node.name = "AI Roto Matte"
        node.label = "AI Roto Matte"
        node.image = image
        node.location = (-400, -250)
        if hasattr(node, "image_user") and node.image_user:
            node.image_user.frame_start = s.start_frame
            node.image_user.frame_duration = len(files)
            node.image_user.use_auto_refresh = True

        self.report({'INFO'}, f"Loaded {len(files)} matte frames into the compositor")
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


class AIROTO_PT_panel(Panel):
    bl_label = "AI Roto Bridge"
    bl_idname = "AIROTO_PT_panel"
    bl_space_type = 'CLIP_EDITOR'
    bl_region_type = 'UI'
    bl_category = "AI Roto"

    def draw(self, context):
        layout = self.layout
        s = context.scene.airoto
        clip = current_clip(context)

        box = layout.box()
        box.label(text="Source", icon='FILE_MOVIE')
        if clip:
            box.label(text=clip.name)
            box.label(text=clip.filepath)
        else:
            box.label(text="No Movie Clip loaded", icon='ERROR')

        box = layout.box()
        box.label(text="Range")
        row = box.row(align=True)
        row.prop(s, "start_frame")
        row.prop(s, "end_frame")
        box.operator("airoto.sync_clip_range", text="Sync Clip Range", icon='TIME')
        box.prop(s, "output_dir")

        box = layout.box()
        box.label(text="Prompt", icon='TRACKER')
        op = box.operator("airoto.pick_point", text="Pick Dancer / Foreground", icon='EYEDROPPER')
        op.kind = 'POSITIVE'
        if s.positive_set:
            box.label(text=f"FG: {s.positive_x:.3f}, {s.positive_y:.3f} @ frame {s.prompt_frame}")

        row = box.row(align=True)
        op = row.operator("airoto.pick_point", text="Pick Background", icon='EYEDROPPER')
        op.kind = 'NEGATIVE'
        if s.negative_set:
            row.operator("airoto.clear_negative", text="", icon='X')
            box.label(text=f"BG: {s.negative_x:.3f}, {s.negative_y:.3f}")

        layout.separator()
        if s.is_generating:
            pbox = layout.box()
            pbox.label(text=s.status_msg or "Generating Matte...", icon='TIME')
            if s.progress_pct >= 0:
                pbox.prop(s, "progress_pct", text="Progress", slider=True)
            layout.separator()

        layout.operator("airoto.generate", icon='PLAY')
        layout.operator("airoto.load_matte", icon='NODE_COMPOSITING')
        if s.last_log:
            layout.operator("airoto.open_log", icon='TEXT')


classes = (
    AIROTO_Preferences,
    AIROTO_Settings,
    AIROTO_OT_pick_point,
    AIROTO_OT_clear_negative,
    AIROTO_OT_sync_clip_range,
    AIROTO_OT_generate,
    AIROTO_OT_load_matte,
    AIROTO_OT_open_log,
    AIROTO_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.airoto = PointerProperty(type=AIROTO_Settings)


def unregister():
    del bpy.types.Scene.airoto
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
