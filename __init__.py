import sys
import importlib
import bpy
from bpy.props import PointerProperty

from . import utils, properties, gpu_overlay, compositor, operators, panel

for mod in (utils, properties, gpu_overlay, compositor, operators, panel):
    importlib.reload(mod)

from .properties import AIROTO_Preferences, AIROTO_PointProperty, AIROTO_Settings, load_settings_from_json, save_settings_to_json
from .panel import AIROTO_PT_panel, AIROTO_UL_points
from .gpu_overlay import draw_clip_editor_overlay

_draw_handler = None

classes = (
    AIROTO_Preferences,
    AIROTO_PointProperty,
    AIROTO_Settings,
    operators.AIROTO_OT_pick_point,
    operators.AIROTO_OT_remove_point,
    operators.AIROTO_OT_clear_all_points,
    operators.AIROTO_OT_clear_negative,
    operators.AIROTO_OT_sync_clip_range,
    operators.AIROTO_OT_preview,
    operators.AIROTO_OT_generate,
    operators.AIROTO_OT_load_matte,
    operators.AIROTO_OT_open_log,
    AIROTO_UL_points,
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
