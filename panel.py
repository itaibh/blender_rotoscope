# SPDX-License-Identifier: GPL-3.0-or-later

from bpy.types import Panel
from .utils import current_clip


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
