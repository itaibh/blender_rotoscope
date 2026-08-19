# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import gpu
import blf
import math
import os
from pathlib import Path
from gpu_extras.batch import batch_for_shader

from .utils import current_clip, absolute_path
from .properties import COLOR_MAP


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

        if len(s.points) > 0:
            pos_idx = 1
            neg_idx = 1
            for idx, pt in enumerate(s.points):
                px, py = v2d.view_to_region(pt.x, pt.y)
                is_active = (idx == s.active_point_index)

                if pt.kind == 'POSITIVE':
                    c_outer = (0.1, 1.0, 0.3, 0.9 * alpha_mult)
                    c_inner = (0.2, 1.0, 0.4, 1.0 * alpha_mult)
                    lbl = f"+{pos_idx} FG" if not is_active else f"+{pos_idx} FG (Selected)"
                    pos_idx += 1
                else:
                    c_outer = (1.0, 0.2, 0.2, 0.9 * alpha_mult)
                    c_inner = (1.0, 0.3, 0.3, 1.0 * alpha_mult)
                    lbl = f"-{neg_idx} BG" if not is_active else f"-{neg_idx} BG (Selected)"
                    neg_idx += 1

                r_out = 12 if is_active else 9
                r_in = 5 if is_active else 3
                draw_circle_2d(px, py, r_out, c_outer)
                draw_filled_circle_2d(px, py, r_in, c_inner)
                draw_crosshair_2d(px, py, 14, c_outer)
                draw_text_2d(px + 14, py - 4, lbl, c_inner)
        else:
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
                                img.gl_free()
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

    # 3. Draw Preview Loading Visual Cue Badge
    if s.is_previewing:
        try:
            p0 = v2d.view_to_region(0.0, 0.0)
            p2 = v2d.view_to_region(1.0, 1.0)
            center_x = (p0[0] + p2[0]) / 2.0
            top_y = p2[1] - 30.0

            draw_filled_circle_2d(center_x, top_y, 16, (0.05, 0.05, 0.08, 0.85), segments=16)
            draw_text_2d(center_x - 95, top_y - 4, "AI Roto: Predicting Preview...", (0.0, 0.95, 1.0, 1.0), size=13)
        except Exception:
            pass
