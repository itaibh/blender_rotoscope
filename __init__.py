# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty

from pathlib import Path
import json
import os
import subprocess
import sys
import math
import gpu
import blf
from gpu_extras.batch import batch_for_shader

_draw_handler = None

COLOR_MAP = {
    'CYAN': (0.0, 0.8, 1.0),
    'RED': (1.0, 0.2, 0.2),
    'GREEN': (0.2, 1.0, 0.3),
    'MAGENTA': (1.0, 0.2, 0.9),
    'YELLOW': (1.0, 0.9, 0.2),
}


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


# --- Node Creation Helpers (Blender 5.2 & Backward Compatible) ---

def create_movieclip_node(tree):
    for nt in ("CompositorNodeMovieClip", "CMP_NODE_MOVIECLIP"):
        try:
            return tree.nodes.new(nt)
        except Exception:
            pass
    return None


def create_image_node(tree):
    for nt in ("CompositorNodeImage", "CMP_NODE_IMAGE"):
        try:
            return tree.nodes.new(nt)
        except Exception:
            pass
    return None


def create_rgb_node(tree):
    for nt in ("CompositorNodeRGB", "CompositorNodeColor", "CMP_NODE_RGB", "ShaderNodeRGB"):
        try:
            return tree.nodes.new(nt)
        except Exception:
            pass
    return None


def create_mix_node(tree, blend_type='MIX'):
    for nt in ("CompositorNodeMix", "CompositorNodeMixRGB", "CMP_NODE_MIX", "CMP_NODE_MIX_RGB", "ShaderNodeMixRGB"):
        try:
            node = tree.nodes.new(nt)
            if hasattr(node, "blend_type"):
                try:
                    node.blend_type = blend_type
                except Exception:
                    pass
            if hasattr(node, "mode"):
                try:
                    node.mode = blend_type
                except Exception:
                    pass
            return node
        except Exception:
            pass
    return None


def create_math_node(tree, operation='MAXIMUM'):
    for nt in ("CompositorNodeMath", "CMP_NODE_MATH", "ShaderNodeMath"):
        try:
            node = tree.nodes.new(nt)
            if hasattr(node, "operation"):
                try:
                    node.operation = operation
                except Exception:
                    pass
            return node
        except Exception:
            pass
    return None


def create_composite_node(tree):
    for nt in ("CompositorNodeComposite", "CMP_NODE_COMPOSITE"):
        try:
            return tree.nodes.new(nt)
        except Exception:
            pass
    return None


def create_viewer_node(tree):
    for nt in ("CompositorNodeViewer", "CMP_NODE_VIEWER"):
        try:
            return tree.nodes.new(nt)
        except Exception:
            pass
    return None


def find_all_mask_sequences(output_dir: Path):
    """
    Find all mask PNG sequences in output_dir and any subdirectories.
    Returns dict mapping layer_name -> list of Path.
    """
    if not output_dir.exists():
        return {}

    sequences = {}

    # 1. Main masks in root
    root_files = sorted(output_dir.glob("mask_*.png"))
    if root_files:
        sequences["Main Matte"] = root_files

    # 2. Layer subdirectories (e.g. output_dir/dancer_1/mask_*.png)
    for subdir in sorted(output_dir.iterdir()):
        if subdir.is_dir():
            sub_files = sorted(subdir.glob("mask_*.png")) or sorted(subdir.glob("*.png"))
            if sub_files:
                sequences[subdir.name] = sub_files

    # 3. Prefixed files in root if no subdirs/root masks found
    if not sequences:
        prefixed = {}
        for p in sorted(output_dir.glob("*.png")):
            parts = p.stem.rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit():
                prefixed.setdefault(parts[0], []).append(p)
        for prefix, files in prefixed.items():
            sequences[prefix] = sorted(files)

    return sequences


# --- GPU Draw Utilities ---

def draw_circle_2d(x, y, radius, color, segments=24):
    gpu.state.blend_set('ALPHA')
    try:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    except Exception:
        shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')

    positions = []
    for i in range(segments):
        theta = 2.0 * math.pi * i / segments
        positions.append((x + math.cos(theta) * radius, y + math.sin(theta) * radius))

    batch = batch_for_shader(shader, 'LINE_LOOP', {"pos": positions})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def draw_filled_circle_2d(x, y, radius, color, segments=24):
    gpu.state.blend_set('ALPHA')
    try:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    except Exception:
        shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')

    positions = [(x, y)]
    for i in range(segments + 1):
        theta = 2.0 * math.pi * i / segments
        positions.append((x + math.cos(theta) * radius, y + math.sin(theta) * radius))

    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": positions})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def draw_crosshair_2d(x, y, radius, color):
    gpu.state.blend_set('ALPHA')
    try:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    except Exception:
        shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')

    positions = [
        (x - radius, y), (x + radius, y),
        (x, y - radius), (x, y + radius)
    ]
    batch = batch_for_shader(shader, 'LINES', {"pos": positions})
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def draw_text_2d(x, y, text, color, size=13):
    font_id = 0
    blf.position(font_id, x, y, 0)
    blf.size(font_id, size)
    blf.color(font_id, color[0], color[1], color[2], color[3])
    blf.draw(font_id, text)


def redraw_clip_editors(context):
    try:
        wm = getattr(context, "window_manager", None) or getattr(bpy.context, "window_manager", None)
        if wm:
            for window in wm.windows:
                if window.screen:
                    for area in window.screen.areas:
                        if area.type == 'CLIP_EDITOR':
                            area.tag_redraw()
    except Exception:
        pass


def draw_clip_editor_overlay(dummy):
    context = bpy.context
    if not context or not context.area or context.area.type != 'CLIP_EDITOR':
        return
    if not context.scene:
        return

    s = getattr(context.scene, "airoto", None)
    if not s:
        return

    clip = current_clip(context)
    if not clip:
        return

    region = context.region
    if not region or not region.view2d:
        return

    v2d = region.view2d

    # 1. Draw Prompt Point Markers
    if s.show_points:
        curr_f = context.scene.frame_current
        is_prompt_frame = (curr_f == s.prompt_frame)
        alpha_mult = 1.0 if is_prompt_frame else 0.4

        if s.positive_set:
            px, py = v2d.view_to_region(s.positive_x, s.positive_y)
            c_outer = (0.1, 1.0, 0.3, 0.9 * alpha_mult)
            c_inner = (0.2, 1.0, 0.4, 1.0 * alpha_mult)
            draw_circle_2d(px, py, 10, c_outer)
            draw_filled_circle_2d(px, py, 4, c_inner)
            draw_crosshair_2d(px, py, 14, c_outer)
            lbl = f"FG Prompt (F{s.prompt_frame})" if is_prompt_frame else f"FG (F{s.prompt_frame})"
            draw_text_2d(px + 14, py - 4, lbl, c_inner)

        if s.negative_set:
            nx, ny = v2d.view_to_region(s.negative_x, s.negative_y)
            c_outer = (1.0, 0.2, 0.2, 0.9 * alpha_mult)
            c_inner = (1.0, 0.3, 0.3, 1.0 * alpha_mult)
            draw_circle_2d(nx, ny, 10, c_outer)
            draw_filled_circle_2d(nx, ny, 4, c_inner)
            draw_crosshair_2d(nx, ny, 14, c_outer)
            lbl = f"BG Prompt (F{s.prompt_frame})" if is_prompt_frame else f"BG (F{s.prompt_frame})"
            draw_text_2d(nx + 14, ny - 4, lbl, c_inner)

    # 2. Draw Mask Preview Overlay over Clip Image (Multi-mask supported)
    if s.show_overlay and s.output_dir:
        out_dir = Path(absolute_path(s.output_dir))
        curr_f = context.scene.frame_current

        mask_files = []
        target_dirs = [out_dir]
        if s.subfolder_name.strip():
            sub_d = out_dir / s.subfolder_name.strip()
            if sub_d.is_dir():
                target_dirs.append(sub_d)

        for d in target_dirs:
            mf = d / f"mask_{curr_f:06d}.png"
            if mf.is_file():
                mask_files.append(mf)

        if out_dir.is_dir():
            for subdir in out_dir.iterdir():
                if subdir.is_dir() and subdir not in target_dirs:
                    sub_mask = subdir / f"mask_{curr_f:06d}.png"
                    if sub_mask.is_file():
                        mask_files.append(sub_mask)

        if mask_files:
            try:
                p0 = v2d.view_to_region(0.0, 0.0)
                p1 = v2d.view_to_region(1.0, 0.0)
                p2 = v2d.view_to_region(1.0, 1.0)
                p3 = v2d.view_to_region(0.0, 1.0)

                positions = [p0, p1, p2, p3]
                tex_coords = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

                try:
                    shader = gpu.shader.from_builtin('IMAGE_COLOR')
                except Exception:
                    try:
                        shader = gpu.shader.from_builtin('2D_IMAGE_COLOR')
                    except Exception:
                        try:
                            shader = gpu.shader.from_builtin('IMAGE')
                        except Exception:
                            shader = gpu.shader.from_builtin('2D_IMAGE')

                rgb = COLOR_MAP.get(s.overlay_color, (0.0, 0.8, 1.0))
                op = float(s.overlay_opacity)
                tint = (rgb[0] * op, rgb[1] * op, rgb[2] * op, 1.0)

                gpu.state.blend_set('ADDITIVE')
                shader.bind()

                for idx, m_file in enumerate(mask_files):
                    img_name = f"AI_ROTO_PREVIEW_TEMP_{idx}"
                    img = bpy.data.images.get(img_name)
                    mtime = os.path.getmtime(m_file) if os.path.isfile(m_file) else 0

                    if not img:
                        try:
                            img = bpy.data.images.load(str(m_file), check_existing=False)
                            img.name = img_name
                            img["_last_mtime"] = mtime
                        except Exception:
                            img = None
                    else:
                        if img.filepath != str(m_file) or img.get("_last_mtime", 0) != mtime:
                            img.filepath = str(m_file)
                            try:
                                img.reload()
                            except Exception:
                                pass
                            img["_last_mtime"] = mtime

                    if img:
                        texture = gpu.texture.from_image(img)
                        batch = batch_for_shader(shader, 'TRI_FAN', {"pos": positions, "texCoord": tex_coords})
                        shader.uniform_sampler("image", texture)
                        if hasattr(shader, "uniform_float"):
                            try:
                                shader.uniform_float("color", tint)
                            except Exception:
                                pass
                        batch.draw(shader)

                gpu.state.blend_set('NONE')
            except Exception:
                pass


# --- Addon Classes ---

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


def update_viewport(self, context):
    redraw_clip_editors(context)


class AIROTO_Settings(PropertyGroup):
    output_dir: StringProperty(
        name="Output Folder",
        description="Folder where matte PNG files will be generated",
        subtype='DIR_PATH',
        default="//ai_roto_masks",
    )
    subfolder_name: StringProperty(
        name="Subfolder / Layer",
        default="",
        description="Optional subfolder name for this mask layer (e.g. dancer_1, head, background) to create multiple joined mask layers",
    )
    start_frame: IntProperty(name="Start", default=1, min=0)
    end_frame: IntProperty(name="End", default=250, min=0)
    prompt_frame: IntProperty(name="Prompt Frame", default=1, min=0)

    positive_x: FloatProperty(default=0.5, update=update_viewport)
    positive_y: FloatProperty(default=0.5, update=update_viewport)
    positive_set: BoolProperty(default=False, update=update_viewport)

    negative_x: FloatProperty(default=0.0, update=update_viewport)
    negative_y: FloatProperty(default=0.0, update=update_viewport)
    negative_set: BoolProperty(default=False, update=update_viewport)

    show_overlay: BoolProperty(
        name="Show Mask Overlay",
        default=True,
        description="Render real-time mask overlay over video frame in Movie Clip Editor",
        update=update_viewport,
    )
    show_points: BoolProperty(
        name="Show Prompt Markers",
        default=True,
        description="Render visual FG/BG click point crosshair markers",
        update=update_viewport,
    )
    overlay_opacity: FloatProperty(
        name="Opacity",
        default=0.5,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        description="Opacity of the interactive mask overlay",
        update=update_viewport,
    )
    overlay_color: EnumProperty(
        name="Color",
        items=[
            ('CYAN', "Cyan", "Cyan mask highlight"),
            ('RED', "Red", "Red mask highlight"),
            ('GREEN', "Green", "Green mask highlight"),
            ('MAGENTA', "Magenta", "Magenta mask highlight"),
            ('YELLOW', "Yellow", "Yellow mask highlight"),
        ],
        default='CYAN',
        description="Tint color for interactive mask preview overlay",
        update=update_viewport,
    )
    auto_preview: BoolProperty(
        name="Auto Preview on Pick",
        default=True,
        description="Automatically run fast single-frame prediction when picking points to preview captured object immediately",
        update=update_viewport,
    )
    auto_load_compositor: BoolProperty(
        name="Auto Compositor",
        default=True,
        description="Automatically set up Compositor overlay node graph after generation",
    )

    last_request: StringProperty(default="")
    last_log: StringProperty(default="")

    is_generating: BoolProperty(default=False)
    progress_pct: FloatProperty(name="Progress", default=-1.0, min=0.0, max=100.0, subtype='PERCENTAGE')
    status_msg: StringProperty(default="")


class AIROTO_OT_preview(Operator):
    bl_idname = "airoto.preview"
    bl_label = "Preview Current Frame"
    bl_description = "Run fast single-frame prediction to preview captured object on the prompt frame"

    _process = None
    _timer = None
    _log_handle = None

    def execute(self, context):
        prefs = addon_preferences(context)
        s = context.scene.airoto
        clip = current_clip(context)

        if prefs is None or clip is None or not s.positive_set:
            return {'CANCELLED'}

        python_exe = absolute_path(prefs.python_executable) if prefs and prefs.python_executable else sys.executable
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
        request = {
            "schema_version": 1,
            "source": {
                "path": source_path,
                "frame_start": int(s.prompt_frame),
                "frame_end": int(s.prompt_frame),
            },
            "prompt": {
                "frame": int(s.prompt_frame),
                "positive": [[float(s.positive_x), float(s.positive_y)]],
                "negative": (
                    [[float(s.negative_x), float(s.negative_y)]]
                    if s.negative_set else []
                ),
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

        request_path = output_dir / "request_preview.json"
        log_path = output_dir / "worker_preview.log"
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")

        try:
            self._log_handle = open(log_path, "w", encoding="utf-8")
            self._process = subprocess.Popen(
                [python_exe, worker, str(request_path)],
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                cwd=str(Path(worker).parent),
            )
        except Exception as exc:
            self.report({'ERROR'}, f"Could not launch preview worker: {exc}")
            return {'CANCELLED'}

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self._finish(context)
            return {'CANCELLED'}

        if event.type == 'TIMER':
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
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None


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
            self.report({'INFO'}, f"{self.kind.title()} point: ({x:.4f}, {y:.4f}) @ frame {s.prompt_frame}")

            if s.auto_preview and s.positive_set:
                try:
                    bpy.ops.airoto.preview()
                except Exception as exc:
                    print(f"Auto-preview error: {exc}")

            return {'FINISHED'}

        return {'RUNNING_MODAL'}


class AIROTO_OT_clear_negative(Operator):
    bl_idname = "airoto.clear_negative"
    bl_label = "Clear Background Point"

    def execute(self, context):
        context.scene.airoto.negative_set = False
        if context.area:
            context.area.tag_redraw()
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
        if s.subfolder_name.strip():
            output_dir = output_dir / s.subfolder_name.strip()
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
                if s.auto_load_compositor:
                    try:
                        bpy.ops.airoto.load_matte()
                    except Exception:
                        pass
                if context.area:
                    context.area.tag_redraw()
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


def setup_compositor_tree(context):
    s = context.scene.airoto
    output_dir = Path(absolute_path(s.output_dir))
    sequences = find_all_mask_sequences(output_dir)

    if not sequences:
        return None, f"No mask PNG files found in '{output_dir}'"

    scene = context.scene

    if hasattr(scene, "use_nodes"):
        scene.use_nodes = True

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

    clip = current_clip(context)

    # 1. Movie Clip Node
    clip_node = create_movieclip_node(tree)
    if clip_node:
        clip_node.name = "AI Roto Clip"
        clip_node.label = "AI Roto Source Clip"
        if clip:
            clip_node.clip = clip
        clip_node.location = (-900, 200)

    # 2. Image Sequence Nodes for each detected mask sequence
    matte_nodes = []
    y_pos = 100
    for seq_name, files in sequences.items():
        try:
            image = bpy.data.images.load(str(files[0]), check_existing=False)
            image.name = f"AI Roto Matte ({seq_name})"
            image.source = 'SEQUENCE'
        except Exception:
            continue

        matte_node = create_image_node(tree)
        if matte_node:
            matte_node.name = f"AI Roto Matte_{seq_name}"
            matte_node.label = f"Matte ({seq_name})"
            matte_node.image = image
            matte_node.location = (-900, y_pos)
            y_pos -= 250
            if hasattr(matte_node, "image_user") and matte_node.image_user:
                matte_node.image_user.frame_start = s.start_frame
                matte_node.image_user.frame_duration = len(files)
                matte_node.image_user.use_auto_refresh = True
            matte_nodes.append(matte_node)

    if not matte_nodes:
        return None, "Failed to create matte image sequence nodes"

    links = tree.links

    # 3. Combine multiple matte outputs if more than 1 sequence exists
    last_matte_output = None
    if len(matte_nodes) == 1:
        last_matte_output = matte_nodes[0].outputs[0]
    else:
        prev_output = matte_nodes[0].outputs[0]
        y_math = 0
        for i in range(1, len(matte_nodes)):
            math_node = create_math_node(tree, 'MAXIMUM') or create_mix_node(tree, 'ADD')
            if math_node:
                math_node.name = f"Combine_Matte_{i}"
                math_node.label = f"Combine Mask {i+1}"
                math_node.location = (-650, y_math)
                y_math -= 200
                try:
                    links.new(prev_output, math_node.inputs[0])
                    links.new(matte_nodes[i].outputs[0], math_node.inputs[1])
                except Exception:
                    pass
                prev_output = math_node.outputs[0]
        last_matte_output = prev_output

    # 4. Tint Color RGB Node
    rgb_node = create_rgb_node(tree)
    if rgb_node:
        rgb_node.name = "AI Roto Tint Color"
        rgb_node.label = "AI Roto Tint Color"
        rgb = COLOR_MAP.get(s.overlay_color, (0.0, 0.8, 1.0))
        if hasattr(rgb_node, "outputs") and len(rgb_node.outputs) > 0:
            rgb_node.outputs[0].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
        rgb_node.location = (-450, -350)

    # 5. Multiply Node (Combined Matte * Tint Color)
    mult_node = create_mix_node(tree, 'MULTIPLY')
    if mult_node:
        mult_node.name = "AI Roto Tint Multiply"
        mult_node.label = "Tinted Mask"
        mult_node.location = (-300, -200)

    # 6. Mix Overlay Node (Clip + Tinted Mask)
    mix_node = create_mix_node(tree, 'ADD')
    if mix_node:
        mix_node.name = "AI Roto Overlay Mix"
        mix_node.label = "Video + Mask Overlay"
        if hasattr(mix_node, "inputs") and len(mix_node.inputs) > 0:
            try:
                mix_node.inputs[0].default_value = float(s.overlay_opacity)
            except Exception:
                pass
        mix_node.location = (0, 0)

    # 7. Composite & Viewer Nodes
    comp_node = create_composite_node(tree)
    if comp_node:
        comp_node.location = (300, 100)

    viewer_node = create_viewer_node(tree)
    if viewer_node:
        viewer_node.location = (300, -100)

    # Link nodes safely
    try:
        if last_matte_output and mult_node and len(mult_node.inputs) > 1:
            links.new(last_matte_output, mult_node.inputs[1])
        if rgb_node and mult_node and len(mult_node.inputs) > 2:
            links.new(rgb_node.outputs[0], mult_node.inputs[2])

        if clip_node and mix_node and len(mix_node.inputs) > 1:
            links.new(clip_node.outputs[0], mix_node.inputs[1])
        if mult_node and mix_node and len(mix_node.inputs) > 2:
            links.new(mult_node.outputs[0], mix_node.inputs[2])

        if mix_node and comp_node:
            links.new(mix_node.outputs[0], comp_node.inputs[0])
        if mix_node and viewer_node:
            links.new(mix_node.outputs[0], viewer_node.inputs[0])
    except Exception:
        pass

    total_files = sum(len(f) for f in sequences.values())
    return len(sequences), total_files, None


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
        box.label(text="Range & Layer Output")
        row = box.row(align=True)
        row.prop(s, "start_frame")
        row.prop(s, "end_frame")
        box.operator("airoto.sync_clip_range", text="Sync Clip Range", icon='TIME')
        box.prop(s, "output_dir")
        box.prop(s, "subfolder_name", icon='FOLDER_REDIRECT')

        box = layout.box()
        box.label(text="Prompt", icon='TRACKER')
        op = box.operator("airoto.pick_point", text="Pick Subject / Foreground", icon='EYEDROPPER')
        op.kind = 'POSITIVE'
        if s.positive_set:
            box.label(text=f"FG: {s.positive_x:.3f}, {s.positive_y:.3f} @ frame {s.prompt_frame}")

        row = box.row(align=True)
        op = row.operator("airoto.pick_point", text="Pick Background", icon='EYEDROPPER')
        op.kind = 'NEGATIVE'
        if s.negative_set:
            row.operator("airoto.clear_negative", text="", icon='X')
            box.label(text=f"BG: {s.negative_x:.3f}, {s.negative_y:.3f}")

        row = box.row(align=True)
        row.prop(s, "auto_preview", text="Auto Preview")
        row.operator("airoto.preview", text="Preview Frame", icon='HIDE_OFF')

        # Interactive Visual Preview Settings
        box = layout.box()
        box.label(text="Visual Overlay Preview", icon='HIDE_OFF')
        row = box.row(align=True)
        row.prop(s, "show_overlay", text="Mask Overlay", toggle=True)
        row.prop(s, "show_points", text="Click Markers", toggle=True)
        
        if s.show_overlay:
            col = box.column(align=True)
            col.prop(s, "overlay_color", text="Tint Color")
            col.prop(s, "overlay_opacity", slider=True)

        box.prop(s, "auto_load_compositor", text="Auto-build Compositor Pipeline")

        layout.separator()
        if s.is_generating:
            pbox = layout.box()
            pbox.label(text=s.status_msg or "Generating Matte...", icon='TIME')
            if s.progress_pct >= 0:
                pbox.prop(s, "progress_pct", text="Progress", slider=True)
            layout.separator()

        layout.operator("airoto.generate", icon='PLAY')
        layout.operator("airoto.load_matte", text="Load & Combine Mattes", icon='NODE_COMPOSITING')
        if s.last_log:
            layout.operator("airoto.open_log", icon='TEXT')


classes = (
    AIROTO_Preferences,
    AIROTO_Settings,
    AIROTO_OT_pick_point,
    AIROTO_OT_clear_negative,
    AIROTO_OT_sync_clip_range,
    AIROTO_OT_preview,
    AIROTO_OT_generate,
    AIROTO_OT_load_matte,
    AIROTO_OT_open_log,
    AIROTO_PT_panel,
)


def register():
    global _draw_handler
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.airoto = PointerProperty(type=AIROTO_Settings)

    if _draw_handler is None:
        _draw_handler = bpy.types.SpaceClipEditor.draw_handler_add(
            draw_clip_editor_overlay, (None,), 'WINDOW', 'POST_PIXEL'
        )


def unregister():
    global _draw_handler
    if _draw_handler is not None:
        bpy.types.SpaceClipEditor.draw_handler_remove(_draw_handler, 'WINDOW')
        _draw_handler = None

    del bpy.types.Scene.airoto
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
