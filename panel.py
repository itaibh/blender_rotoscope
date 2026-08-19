# SPDX-License-Identifier: GPL-3.0-or-later

from bpy.types import Panel, UIList
from .utils import current_clip


class AIROTO_UL_points(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            if item.kind == 'POSITIVE':
                row.label(text=f"+{index+1} FG", icon='ADD')
            else:
                row.label(text=f"-{index+1} BG", icon='REMOVE')
            row.prop(item, "x", text="X", slider=False)
            row.prop(item, "y", text="Y", slider=False)
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text=f"P{index+1}")


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

        # Prompt Points Box
        box = layout.box()
        box.label(text=f"Prompt Points ({len(s.points)})", icon='TRACKER')
        
        row = box.row(align=True)
        op_fg = row.operator("airoto.pick_point", text="+ Add Subject (FG)", icon='EYEDROPPER')
        op_fg.kind = 'POSITIVE'
        op_bg = row.operator("airoto.pick_point", text="- Add Background (BG)", icon='CURSOR')
        op_bg.kind = 'NEGATIVE'

        if s.points:
            row_list = box.row()
            row_list.template_list("AIROTO_UL_points", "", s, "points", s, "active_point_index", rows=3)

            col_btns = row_list.column(align=True)
            col_btns.operator("airoto.remove_point", text="", icon='REMOVE')
            col_btns.operator("airoto.clear_all_points", text="", icon='TRASH')

        col_auto = box.column(align=True)
        col_auto.prop(s, "auto_preview", text="Auto Preview Single Frame")
        col_auto.prop(s, "auto_track_all", text="Auto-Track All Frames on Pick")

        # Sequence Mask Tracking & Generation Box
        gbox = layout.box()
        gbox.label(text="Sequence Mask Tracking", icon='PLAY')

        # Single Frame Step & Playhead Advance
        curr = context.scene.frame_current
        row_step = gbox.row(align=True)
        op_sb = row_step.operator("airoto.step_frame", text=f"◀ Step Back (F{curr}→F{curr-1})", icon='TRACKING_BACKWARDS_SINGLE')
        op_sb.delta = -1

        op_sf = row_step.operator("airoto.step_frame", text=f"Step Forward ▶ (F{curr}→F{curr+1})", icon='TRACKING_FORWARDS_SINGLE')
        op_sf.delta = 1

        row_dirs = gbox.row(align=True)
        op_back = row_dirs.operator("airoto.generate", text=f"◀ Backwards (F{s.prompt_frame}-F{s.start_frame})", icon='TRACKING_BACKWARDS')
        op_back.direction = 'BACKWARDS'
        
        op_fwd = row_dirs.operator("airoto.generate", text=f"▶ Forwards (F{s.prompt_frame}-{s.end_frame})", icon='TRACKING_FORWARDS')
        op_fwd.direction = 'FORWARDS'

        num_frames = max(1, s.end_frame - s.start_frame + 1)
        row_main = gbox.row()
        row_main.scale_y = 1.3
        op_both = row_main.operator(
            "airoto.generate",
            text=f"◀▶ Track Both Directions ({num_frames} Frames)",
            icon='PLAY',
        )
        op_both.direction = 'BOTH'

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

        layout.operator("airoto.load_matte", text="Load & Combine Mattes into Compositor", icon='NODE_COMPOSITING')
        if s.last_log:
            layout.operator("airoto.open_log", text="Open Log File", icon='TEXT')


class AIROTO_PT_panel_image_editor(Panel):
    bl_label = "AI Roto Bridge"
    bl_idname = "AIROTO_PT_panel_image_editor"
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "AI Roto"

    def draw(self, context):
        AIROTO_PT_panel.draw(self, context)


class AIROTO_PT_panel_view3d(Panel):
    bl_label = "AI Roto Bridge"
    bl_idname = "AIROTO_PT_panel_view3d"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AI Roto"

    def draw(self, context):
        AIROTO_PT_panel.draw(self, context)
