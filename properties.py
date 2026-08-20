# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.types import AddonPreferences, PropertyGroup
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

import json
from pathlib import Path

from .utils import redraw_clip_editors

COLOR_MAP = {
    'CYAN': (0.0, 0.8, 1.0),
    'RED': (1.0, 0.2, 0.2),
    'GREEN': (0.2, 1.0, 0.3),
    'MAGENTA': (1.0, 0.2, 0.9),
    'YELLOW': (1.0, 0.9, 0.2),
}

CONFIG_PATH = Path.home() / ".config" / "blender" / "ai_roto_bridge_settings.json"


def save_settings_to_json(context=None):
    try:
        ctx = context or bpy.context
        from .utils import addon_preferences
        prefs = addon_preferences(ctx)
        s = getattr(ctx.scene, "airoto", None) if ctx and hasattr(ctx, "scene") else None

        data = {}
        if prefs:
            data["python_executable"] = prefs.python_executable
            data["worker_script"] = prefs.worker_script
            data["model_path"] = prefs.model_path
            data["config_path"] = prefs.config_path
            data["device"] = prefs.device
            data["other_device"] = prefs.other_device
            data["use_daemon"] = prefs.use_daemon
            data["daemon_port"] = prefs.daemon_port

        if s:
            data["output_dir"] = s.output_dir
            data["subfolder_name"] = s.subfolder_name
            data["show_overlay"] = s.show_overlay
            data["show_points"] = s.show_points
            data["overlay_opacity"] = s.overlay_opacity
            data["overlay_color"] = s.overlay_color
            data["auto_preview"] = s.auto_preview
            data["auto_load_compositor"] = s.auto_load_compositor

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"Could not save ai_roto_bridge settings: {exc}")


def load_settings_from_json(context=None):
    try:
        if not CONFIG_PATH.is_file():
            return
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        ctx = context or bpy.context
        from .utils import addon_preferences
        prefs = addon_preferences(ctx)
        s = getattr(ctx.scene, "airoto", None) if ctx and hasattr(ctx, "scene") else None

        if prefs:
            if "python_executable" in data and data["python_executable"]:
                prefs.python_executable = data["python_executable"]
            if "worker_script" in data and data["worker_script"]:
                prefs.worker_script = data["worker_script"]
            if "model_path" in data and data["model_path"]:
                prefs.model_path = data["model_path"]
            if "config_path" in data and data["config_path"]:
                prefs.config_path = data["config_path"]
            if "device" in data:
                prefs.device = data["device"]
            if "other_device" in data:
                prefs.other_device = data["other_device"]
            if "use_daemon" in data:
                prefs.use_daemon = data["use_daemon"]
            if "daemon_port" in data:
                prefs.daemon_port = data["daemon_port"]

        if s:
            if "output_dir" in data and data["output_dir"]:
                s.output_dir = data["output_dir"]
            if "subfolder_name" in data:
                s.subfolder_name = data["subfolder_name"]
            if "show_overlay" in data:
                s.show_overlay = data["show_overlay"]
            if "show_points" in data:
                s.show_points = data["show_points"]
            if "overlay_opacity" in data:
                s.overlay_opacity = data["overlay_opacity"]
            if "overlay_color" in data:
                s.overlay_color = data["overlay_color"]
            if "auto_preview" in data:
                s.auto_preview = data["auto_preview"]
            if "auto_load_compositor" in data:
                s.auto_load_compositor = data["auto_load_compositor"]
    except Exception as exc:
        print(f"Could not load ai_roto_bridge settings: {exc}")


def update_viewport(self, context):
    redraw_clip_editors(context)
    save_settings_to_json(context)


class AIROTO_Preferences(AddonPreferences):
    bl_idname = __package__ if __package__ else __name__.rsplit('.', 1)[0]

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
            ('CPU', "CPU", "Use CPU with OpenMP/MKL multi-threading"),
            ('XPU', "Intel GPU (XPU)", "Use Intel GPU via PyTorch XPU / OneAPI"),
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

    use_daemon: BoolProperty(
        name="Use Persistent Daemon",
        default=True,
        description="Keep SAM2 loaded in background RAM/VRAM for instant sub-second (~100ms) previews",
    )
    daemon_port: IntProperty(
        name="Daemon Port",
        default=18950,
        min=1024,
        max=65535,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "python_executable")
        layout.prop(self, "worker_script")
        layout.separator()
        layout.prop(self, "use_daemon")
        if self.use_daemon:
            row_daemon = layout.row(align=True)
            row_daemon.prop(self, "daemon_port")
            row_daemon.operator("airoto.reset_daemon", text="Reset / Restart Daemon", icon='FILE_REFRESH')
        layout.separator()
        layout.prop(self, "model_path")
        layout.prop(self, "config_path")
        layout.prop(self, "device")
        if self.device == 'OTHER':
            layout.prop(self, "other_device")


from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, StringProperty


class AIROTO_PointProperty(PropertyGroup):
    x: FloatProperty(name="X", default=0.5, update=update_viewport)
    y: FloatProperty(name="Y", default=0.5, update=update_viewport)
    kind: EnumProperty(
        name="Kind",
        items=[
            ('POSITIVE', "Foreground (+)", "Foreground subject point"),
            ('NEGATIVE', "Background (-)", "Background exclusion point"),
        ],
        default='POSITIVE',
        update=update_viewport,
    )


class AIROTO_Settings(PropertyGroup):
    points: CollectionProperty(type=AIROTO_PointProperty)
    active_point_index: IntProperty(name="Active Point Index", default=0)

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
    track_direction: EnumProperty(
        name="Direction",
        items=[
            ('BOTH', "Both Directions (◀ ▶)", "Propagate tracking both forward and backward from current frame"),
            ('FORWARDS', "Forwards Only (▶)", "Propagate tracking only forwards from current frame to End frame"),
            ('BACKWARDS', "Backwards Only (◀)", "Propagate tracking only backwards from current frame to Start frame"),
        ],
        default='BOTH',
        description="Direction to propagate SAM2 mask tracking across timeline",
        update=update_viewport,
    )
    auto_preview: BoolProperty(
        name="Auto Preview on Pick",
        default=True,
        description="Automatically run fast single-frame prediction when picking points to preview captured object immediately",
        update=update_viewport,
    )
    auto_track_all: BoolProperty(
        name="Auto-Track All Frames on Pick",
        default=False,
        description="Automatically launch full sequence tracking across all frames whenever points are added or changed",
        update=update_viewport,
    )
    auto_load_compositor: BoolProperty(
        name="Auto Compositor",
        default=True,
        description="Automatically set up Compositor overlay node graph after generation",
    )
    compositor_mode: EnumProperty(
        name="Compositor Mode",
        items=[
            ('CUTOUT', "Cutout Object (Alpha)", "Cut out subject from video with transparent background via Set Alpha"),
            ('SANDWICH', "3D Sandwich (Objects Behind)", "Place 3D elements / text behind rotoscoped foreground subject"),
            ('OVERLAY', "Tint Preview Overlay", "Overlay colored mask tint on top of video clip for inspection"),
        ],
        default='CUTOUT',
        description="Node graph template built by Load & Combine Mattes",
    )

    last_request: StringProperty(default="")
    last_log: StringProperty(default="")

    is_previewing: BoolProperty(default=False)
    is_generating: BoolProperty(default=False)
    progress_pct: FloatProperty(name="Progress", default=-1.0, min=0.0, max=100.0, subtype='PERCENTAGE')
    status_msg: StringProperty(default="")
