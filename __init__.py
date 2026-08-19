# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.props import PointerProperty

if "utils" in locals():
    import importlib
    importlib.reload(utils)
    importlib.reload(properties)
    importlib.reload(gpu_overlay)
    importlib.reload(compositor)
    importlib.reload(operators)
    importlib.reload(panel)

from . import utils, properties, gpu_overlay, compositor, operators, panel
from .properties import AIROTO_Preferences, AIROTO_Settings, load_settings_from_json, save_settings_to_json
from .operators import (
    AIROTO_OT_pick_point,
    AIROTO_OT_clear_negative,
    AIROTO_OT_sync_clip_range,
    AIROTO_OT_preview,
    AIROTO_OT_generate,
    AIROTO_OT_load_matte,
    AIROTO_OT_open_log,
)
from .panel import AIROTO_PT_panel
from .gpu_overlay import draw_clip_editor_overlay

_draw_handler = None

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

    try:
        load_settings_from_json()
    except Exception:
        pass

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
