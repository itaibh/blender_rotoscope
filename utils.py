# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import os


def addon_preferences(context):
    pkg = __name__.rsplit('.', 1)[0]
    addon = context.preferences.addons.get(pkg)
    if addon and hasattr(addon, "preferences"):
        return addon.preferences

    # Fallback to search all registered addon preferences for AIROTO_Preferences
    try:
        for a_name, a_obj in context.preferences.addons.items():
            if hasattr(a_obj, "preferences") and type(a_obj.preferences).__name__ == "AIROTO_Preferences":
                return a_obj.preferences
    except Exception:
        pass
    return None


def current_clip(context):
    if context.area and context.area.type == 'CLIP_EDITOR':
        return context.space_data.clip
    return None


def absolute_path(value: str) -> str:
    if not value:
        return ""
    return os.path.abspath(bpy.path.abspath(value))


from pathlib import Path
import sys


def resolve_python_executable(prefs=None) -> str:
    if prefs and getattr(prefs, "python_executable", None) and os.path.isfile(absolute_path(prefs.python_executable)):
        return absolute_path(prefs.python_executable)

    candidates = [
        Path.home() / ".local/share/kdenlive/venv-sam/bin/python3",
        Path.home() / ".local/share/kdenlive/venv-sam/bin/python",
        Path.home() / ".cache/venv-sam/bin/python3",
        Path.home() / "venv-sam/bin/python3",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)

    return sys.executable


def normalized_click(context, event):
    x, y = context.region.view2d.region_to_view(
        event.mouse_region_x,
        event.mouse_region_y,
    )
    return float(x), float(y)


def redraw_clip_editors(context=None):
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
