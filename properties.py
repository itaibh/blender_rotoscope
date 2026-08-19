# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.types import AddonPreferences, PropertyGroup
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from .utils import redraw_clip_editors

COLOR_MAP = {
    'CYAN': (0.0, 0.8, 1.0),
    'RED': (1.0, 0.2, 0.2),
    'GREEN': (0.2, 1.0, 0.3),
    'MAGENTA': (1.0, 0.2, 0.9),
    'YELLOW': (1.0, 0.9, 0.2),
}


def update_viewport(self, context):
    redraw_clip_editors(context)


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

    is_previewing: BoolProperty(default=False)
    is_generating: BoolProperty(default=False)
    progress_pct: FloatProperty(name="Progress", default=-1.0, min=0.0, max=100.0, subtype='PERCENTAGE')
    status_msg: StringProperty(default="")
