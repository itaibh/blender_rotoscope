# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from pathlib import Path

from .utils import current_clip, absolute_path
from .properties import COLOR_MAP


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
    if hasattr(tree, "interface") and hasattr(tree.interface, "new_socket"):
        try:
            has_img_out = any(
                getattr(item, "name", None) == "Image" and getattr(item, "in_out", None) == "OUTPUT"
                for item in tree.interface.items_tree
            )
            if not has_img_out:
                tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        except Exception:
            pass

    for nt in ("NodeGroupOutput", "CompositorNodeComposite", "CMP_NODE_COMPOSITE"):
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


def create_set_alpha_node(tree):
    for nt in ("CompositorNodeSetAlpha", "CMP_NODE_SETALPHA"):
        try:
            return tree.nodes.new(nt)
        except Exception:
            pass
    return None


def create_alpha_over_node(tree):
    for nt in ("CompositorNodeAlphaOver", "CMP_NODE_ALPHAOVER"):
        try:
            return tree.nodes.new(nt)
        except Exception:
            pass
    return None


def create_render_layers_node(tree):
    for nt in ("CompositorNodeRLayers", "CMP_NODE_RLAYERS"):
        try:
            return tree.nodes.new(nt)
        except Exception:
            pass
    return None


def create_scale_node(tree, mode='STRETCH'):
    for nt in ("CompositorNodeScale", "CMP_NODE_SCALE"):
        try:
            node = tree.nodes.new(nt)
            if hasattr(node, "space"):
                try:
                    node.space = 'RENDER_SIZE'
                except Exception:
                    pass
            if hasattr(node, "frame_method"):
                try:
                    node.frame_method = mode
                except Exception:
                    pass
            if hasattr(node, "inputs"):
                if "Type" in node.inputs:
                    try:
                        node.inputs["Type"].default_value = 'Render Size'
                    except Exception:
                        pass
                if "Frame Type" in node.inputs:
                    try:
                        node.inputs["Frame Type"].default_value = mode
                    except Exception:
                        pass
            return node
        except Exception:
            pass
    return None


def link_alpha_over_node(links, bg_socket, fg_socket, ao_node):
    if not ao_node:
        return
    if "Background" in ao_node.inputs:
        links.new(bg_socket, ao_node.inputs["Background"])
    elif len(ao_node.inputs) > 1:
        links.new(bg_socket, ao_node.inputs[1])

    if "Foreground" in ao_node.inputs:
        links.new(fg_socket, ao_node.inputs["Foreground"])
    elif len(ao_node.inputs) > 2:
        links.new(fg_socket, ao_node.inputs[2])


def set_node_image_user_props(node, start_f, duration):
    """
    Set frame_start, frame_duration, frame_offset, and use_auto_refresh on Compositor Image nodes.
    Supports Blender 5.2 / 4.x (direct node attributes) and Blender 3.x (nested image_user attribute).
    """
    if hasattr(node, "frame_duration"):
        try:
            node.frame_duration = duration
        except Exception:
            pass
    if hasattr(node, "frame_start"):
        try:
            node.frame_start = start_f
        except Exception:
            pass
    if hasattr(node, "frame_offset"):
        try:
            node.frame_offset = 0
        except Exception:
            pass
    if hasattr(node, "use_auto_refresh"):
        try:
            node.use_auto_refresh = True
        except Exception:
            pass
    if hasattr(node, "use_cyclic"):
        try:
            node.use_cyclic = False
        except Exception:
            pass

    if hasattr(node, "image_user") and node.image_user:
        iu = node.image_user
        try:
            iu.frame_start = start_f
            iu.frame_duration = duration
            iu.frame_offset = 0
            iu.use_auto_refresh = True
            iu.use_cyclic = False
        except Exception:
            pass


def find_or_create_composite_node(tree):
    # 1. Prefer an existing Group Output / Composite node
    for n in tree.nodes:
        if getattr(n, "type", None) in {'GROUP_OUTPUT', 'COMPOSITE', 'CMP_NODE_COMPOSITE'}:
            return n

    if hasattr(tree, "interface") and hasattr(tree.interface, "new_socket"):
        try:
            has_img_out = any(
                getattr(item, "name", None) == "Image" and getattr(item, "in_out", None) == "OUTPUT"
                for item in tree.interface.items_tree
            )
            if not has_img_out:
                tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        except Exception:
            pass

    node = create_composite_node(tree)
    if node:
        node.name = "AI Roto Composite"
        node.label = "AI Roto Composite"
    return node


def find_or_create_viewer_node(tree):
    # 1. Prefer an existing AI Roto dedicated Viewer node
    node = tree.nodes.get("AI Roto Viewer")
    if node:
        return node
    # 2. Re-use existing scene Viewer node if present
    for n in tree.nodes:
        if getattr(n, "type", None) in {'VIEWER', 'CMP_NODE_VIEWER'}:
            return n
    # 3. Create a new dedicated node
    node = create_viewer_node(tree)
    if node:
        node.name = "AI Roto Viewer"
        node.label = "AI Roto Viewer"
    return node


def find_all_mask_sequences(target_dir: Path):
    """
    Find all mask PNG sequences in target_dir or any layer subdirectories inside target_dir.
    Returns dict mapping layer_name -> list of Path.
    """
    if not target_dir.exists():
        return {}

    sequences = {}

    # 1. Check layer subdirectories (e.g. target_dir/dancer_1/mask_*.png)
    for subdir in sorted(target_dir.iterdir()):
        if subdir.is_dir():
            sub_files = sorted(subdir.glob("mask_*.png")) or sorted(subdir.glob("*.png"))
            if sub_files:
                sequences[subdir.name] = sub_files

    # 2. Check mask files in target_dir root
    root_files = sorted(target_dir.glob("mask_*.png")) or sorted(target_dir.glob("*.png"))
    if root_files:
        if not sequences:
            sequences["Main Matte"] = root_files

    return sequences


def setup_compositor_tree(context):
    s = context.scene.airoto
    start_f = int(s.start_frame)
    end_f = int(s.end_frame)
    expected_frames = max(1, end_f - start_f + 1)

    base_dir = Path(absolute_path(s.output_dir))
    subfolder = s.subfolder_name.strip()
    target_dir = (base_dir / subfolder) if subfolder else base_dir

    if not target_dir.exists():
        if base_dir.exists() and not subfolder:
            target_dir = base_dir
        else:
            return None, 0, f"Folder '{target_dir}' does not exist"

    sequences = find_all_mask_sequences(target_dir)

    if not sequences:
        return None, 0, f"No mask PNG files found in '{target_dir}'"

    missing_files = []
    for seq_name, files in sequences.items():
        if len(files) < expected_frames:
            missing_files.append(f"'{seq_name}' ({len(files)}/{expected_frames} frames)")

    scene = context.scene

    if hasattr(scene, "use_nodes"):
        try:
            scene.use_nodes = True
        except Exception:
            pass
    if hasattr(scene, "render"):
        try:
            scene.render.use_compositing = True
            scene.render.film_transparent = True
        except Exception:
            pass

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

    # Remove existing AI Roto generated nodes to prevent duplication
    nodes_to_remove = [
        node for node in tree.nodes
        if node.name.startswith("AI Roto") or node.name.startswith("Combine_Matte")
    ]
    for node in nodes_to_remove:
        tree.nodes.remove(node)

    clip = current_clip(context)

    # 1. Movie Clip Node
    clip_node = create_movieclip_node(tree)
    if clip_node:
        clip_node.name = "AI Roto Clip"
        clip_node.label = "AI Roto Source Clip"
        if clip:
            clip_node.clip = clip
        clip_node.location = (-900, 200)

    # Purge existing AI Roto Matte images from bpy.data.images so Blender loads fresh sequence metadata
    for img in list(bpy.data.images):
        if img.name.startswith("AI Roto Matte"):
            try:
                bpy.data.images.remove(img)
            except Exception:
                pass

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
            set_node_image_user_props(matte_node, start_f, expected_frames)
            matte_nodes.append(matte_node)

    if not matte_nodes:
        return None, 0, "Failed to create matte image sequence nodes"

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

    # 4. Scale inputs to Scene Render Size for 1:1 pixel match with scene output
    clip_output = clip_node.outputs[0] if clip_node and len(clip_node.outputs) > 0 else None
    scale_clip = create_scale_node(tree, 'STRETCH')
    if scale_clip and clip_output:
        scale_clip.name = "AI Roto Scale Clip"
        scale_clip.label = "Scale Clip to Render Size"
        scale_clip.location = (-650, 200)
        links.new(clip_output, scale_clip.inputs[0])
        clip_output = scale_clip.outputs[0]

    scale_matte = create_scale_node(tree, 'STRETCH')
    if scale_matte and last_matte_output:
        scale_matte.name = "AI Roto Scale Matte"
        scale_matte.label = "Scale Matte to Render Size"
        scale_matte.location = (-650, -200)
        links.new(last_matte_output, scale_matte.inputs[0])
        last_matte_output = scale_matte.outputs[0]

    comp_node = find_or_create_composite_node(tree)
    if comp_node:
        comp_node.location = (400, 100)

    viewer_node = find_or_create_viewer_node(tree)
    if viewer_node:
        viewer_node.location = (400, -100)

    mode = getattr(s, "compositor_mode", 'CUTOUT')
    final_output = None

    if mode == 'CUTOUT':
        set_alpha = create_set_alpha_node(tree)
        if set_alpha:
            set_alpha.name = "AI Roto Set Alpha"
            set_alpha.label = "Foreground Subject Cutout"
            set_alpha.location = (-300, 100)
            if clip_output:
                if "Image" in set_alpha.inputs:
                    links.new(clip_output, set_alpha.inputs["Image"])
                elif len(set_alpha.inputs) > 0:
                    links.new(clip_output, set_alpha.inputs[0])
            if last_matte_output:
                if "Alpha" in set_alpha.inputs:
                    links.new(last_matte_output, set_alpha.inputs["Alpha"])
                elif len(set_alpha.inputs) > 1:
                    links.new(last_matte_output, set_alpha.inputs[1])
            final_output = set_alpha.outputs[0]

    elif mode == 'SANDWICH':
        set_alpha = create_set_alpha_node(tree)
        if set_alpha:
            set_alpha.name = "AI Roto Set Alpha"
            set_alpha.label = "Foreground Subject Cutout"
            set_alpha.location = (-400, 200)
            if clip_output:
                if "Image" in set_alpha.inputs:
                    links.new(clip_output, set_alpha.inputs["Image"])
                elif len(set_alpha.inputs) > 0:
                    links.new(clip_output, set_alpha.inputs[0])
            if last_matte_output:
                if "Alpha" in set_alpha.inputs:
                    links.new(last_matte_output, set_alpha.inputs["Alpha"])
                elif len(set_alpha.inputs) > 1:
                    links.new(last_matte_output, set_alpha.inputs[1])

        rlayers = create_render_layers_node(tree)
        if rlayers:
            rlayers.name = "AI Roto Render Layers"
            rlayers.label = "3D Scene / Text Render"
            rlayers.location = (-400, -100)

        ao_bg_3d = create_alpha_over_node(tree)
        if ao_bg_3d:
            ao_bg_3d.name = "AI Roto BG + 3D"
            ao_bg_3d.label = "Video BG + 3D Elements"
            ao_bg_3d.location = (-150, -50)
            if clip_output and rlayers and len(rlayers.outputs) > 0:
                link_alpha_over_node(links, clip_output, rlayers.outputs[0], ao_bg_3d)

        ao_final = create_alpha_over_node(tree)
        if ao_final:
            ao_final.name = "AI Roto Final Sandwich"
            ao_final.label = "Foreground Object on Top"
            ao_final.location = (100, 100)
            if ao_bg_3d and len(ao_bg_3d.outputs) > 0 and set_alpha and len(set_alpha.outputs) > 0:
                link_alpha_over_node(links, ao_bg_3d.outputs[0], set_alpha.outputs[0], ao_final)
            final_output = ao_final.outputs[0]

    else:  # OVERLAY
        rgb_node = create_rgb_node(tree)
        if rgb_node:
            rgb_node.name = "AI Roto Tint Color"
            rgb_node.label = "AI Roto Tint Color"
            rgb = COLOR_MAP.get(s.overlay_color, (0.0, 0.8, 1.0))
            if hasattr(rgb_node, "outputs") and len(rgb_node.outputs) > 0:
                rgb_node.outputs[0].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
            rgb_node.location = (-450, -350)

        mult_node = create_mix_node(tree, 'MULTIPLY')
        if mult_node:
            mult_node.name = "AI Roto Tint Multiply"
            mult_node.label = "Tinted Mask"
            mult_node.location = (-300, -200)

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

        try:
            if last_matte_output and mult_node and len(mult_node.inputs) > 1:
                links.new(last_matte_output, mult_node.inputs[1])
            if rgb_node and mult_node and len(mult_node.inputs) > 2:
                links.new(rgb_node.outputs[0], mult_node.inputs[2])

            if clip_output and mix_node and len(mix_node.inputs) > 1:
                links.new(clip_output, mix_node.inputs[1])
            if mult_node and mix_node and len(mult_node.inputs) > 2:
                links.new(mult_node.outputs[0], mix_node.inputs[2])
        except Exception:
            pass

        if mix_node and len(mix_node.outputs) > 0:
            final_output = mix_node.outputs[0]

    if final_output:
        if comp_node:
            try:
                if "Image" in comp_node.inputs:
                    links.new(final_output, comp_node.inputs["Image"])
                elif len(comp_node.inputs) > 0:
                    links.new(final_output, comp_node.inputs[0])
            except Exception:
                pass
        if viewer_node:
            try:
                if "Image" in viewer_node.inputs:
                    links.new(final_output, viewer_node.inputs["Image"])
                elif len(viewer_node.inputs) > 0:
                    links.new(final_output, viewer_node.inputs[0])
            except Exception:
                pass

    total_files = sum(len(f) for f in sequences.values())
    warn_msg = None
    if missing_files:
        warn_msg = f"Warning: missing mask files in '{target_dir}': " + ", ".join(missing_files)
    return len(sequences), total_files, warn_msg

