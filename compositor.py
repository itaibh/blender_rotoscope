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


def find_or_create_composite_node(tree):
    # 1. Prefer an existing AI Roto dedicated Composite node
    node = tree.nodes.get("AI Roto Composite")
    if node:
        return node
    # 2. Re-use existing scene Composite node if present
    for n in tree.nodes:
        if getattr(n, "type", None) in {'COMPOSITE', 'CMP_NODE_COMPOSITE'}:
            return n
    # 3. Create a new dedicated node
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


def setup_compositor_tree(context):
    s = context.scene.airoto
    output_dir = Path(absolute_path(s.output_dir))
    sequences = find_all_mask_sequences(output_dir)

    if not sequences:
        return None, 0, f"No mask PNG files found in '{output_dir}'"

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

    # 2. Image Sequence Nodes for each detected mask sequence
    matte_nodes = []
    y_pos = 100
    for seq_name, files in sequences.items():
        try:
            image = bpy.data.images.load(str(files[0]), check_existing=False)
            image.name = f"AI Roto Matte ({seq_name})"
            image.source = 'SEQUENCE'
            try:
                image.reload()
                image.gl_free()
            except Exception:
                pass
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
    comp_node = find_or_create_composite_node(tree)
    if comp_node:
        comp_node.location = (300, 100)

    viewer_node = find_or_create_viewer_node(tree)
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
