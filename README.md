# AI Roto Bridge

[![Sponsor](https://img.shields.io/badge/-Sponsor%20on%20GitHub-ea4aaa?style=flat-square&logo=github&logoColor=white)](https://github.com/sponsors/itaibh)
![license](https://img.shields.io/badge/license-MIT-orange)
![version](https://img.shields.io/badge/version-0.2.0-blue)

A Blender 5.2 extension that provides interactive video foreground extraction, multi-layer rotoscoping, and automatic mask tracking using **Segment Anything Model 2 (SAM 2)** without polluting Blender's embedded Python environment.

![Interactive Viewport Mask & Prompting](docs/videoimage.png)

*Interactive point prompting and real-time mask overlay preview in Blender's Movie Clip Editor viewport. Video credit: [Allan Mas on Pexels](https://www.pexels.com/video/man-doing-hip-hop-dance-5362368/)*

## User Interface & Screenshots

| Movie Clip Editor Panel | Extension Preferences |
| :---: | :---: |
| ![AI Roto Sidebar Panel](docs/sidepanel.png) | ![Extension Preferences](docs/settingspanel.png) |
| **AI Roto Sidebar Panel**: Pick points, step through frames, trigger background sequence tracking, manage multi-layer subfolders, and auto-build Compositor node trees. | **Extension Preferences**: Set your external Python environment, persistent daemon worker, model checkpoint overrides, and target compute device. |

## Key Features

- **Interactive Viewport Prompting**: Pick foreground subject points (`+ Add Subject (FG)`) and background exclusion points (`- Add Background (BG)`) directly on video frames in the Movie Clip Editor.
- **Persistent Daemon Mode**: Keeps SAM 2 loaded in background RAM/VRAM for sub-second (~100ms) single-frame previews without re-initialization overhead.
- **Real-Time Viewport Overlay**: Interactive mask tint overlay with customizable color (Cyan, Red, Green, Magenta, Yellow) and opacity, plus visual click point crosshair markers.
- **Interactive Frame Stepping & Directional Tracking**:
  - **Frame Stepping**: Propagate and preview masks one frame at a time (◀ Step Back / Step Forward ▶).
  - **Sequence Tracking**: Run batch mask propagation **Forwards**, **Backwards**, or in **Both Directions** from any prompt frame.
- **Non-Blocking Asynchronous Engine**: Tracking runs asynchronously in a separate process with live progress monitoring and cancellation capability (`⏹ Stop / Cancel Tracking`) without freezing Blender's interface.
- **Multi-Layer / Subfolder Organization**: Assign subfolder names (e.g. `dancer_1`, `head`, `background`) to separate subject layers and automatically merge them into a single matte.
- **1-Click Compositor Pipeline**: Automated Node Graph generator (`Load & Combine Mattes`) with presets for:
  - **Cutout Object (Alpha)**: Isolate subjects with transparent background using Set Alpha.
  - **3D Sandwich**: Place 3D elements/text behind the rotoscoped foreground subject.
  - **Tint Preview Overlay**: Tinted mask overlay on top of video for visual inspection.
- **Flexible Hardware Acceleration**: Native support for NVIDIA CUDA, Intel GPU (PyTorch XPU / OneAPI), CPU multi-threading, OpenVINO, or custom compute devices.

---

## SAM 2 Environment Setup

Ensure your external Python environment (or Kdenlive SAM2 venv) has `torch`, `torchvision`, `opencv-python`, `Pillow`, `numpy`, and `sam2` installed:

```bash
# Create virtual environment (optional)
python3 -m venv ~/sam2_env
source ~/sam2_env/bin/activate

# Install dependencies
pip install torch torchvision
pip install opencv-python pillow numpy
pip install git+https://github.com/facebookresearch/sam2.git
```

---

## Extension Installation & Configuration

1. In Blender, go to **Edit > Preferences > Extensions > Install from Disk** (select ZIP or extension directory).
2. Open **Edit > Preferences > Add-ons / Extensions > AI Roto Bridge**:
   - **External Python**: Path to your external virtual environment's Python executable (e.g., `/home/user/sam2_env/bin/python`).
   - **Worker Script**: Path to `sam2_worker.py` (leave blank to auto-detect from extension directory).
   - **Use Persistent Daemon**: Enabled by default for sub-second live previews on port `18950`.
   - **Model & Model Config**: (Optional) Specify custom SAM 2 checkpoint file (`.pt`/`.pth`) or model YAML configuration paths.
   - **Device**: Select compute target (`Auto`, `CUDA`, `Intel GPU (XPU)`, `CPU`, `OpenVINO`, or `Other`).

To rebuild the extension package:

```bash
blender --command extension build --source-dir .
```

---

## How to Use AI Roto Bridge

### Step 1: Open Movie Clip & Sync Range
1. Open the **Movie Clip Editor** workspace in Blender and load your target video clip.
2. In the **AI Roto** sidebar panel (`N` key), expand **Frame Range & Output Folder**.
3. Click **Sync Clip Range** to match Blender timeline start/end frames to the movie clip duration.

### Step 2: Interactive Point Prompting & Fast Preview
1. Move to a representative frame where your subject is clearly visible.
2. Click **+ Add Subject (FG)** and click directly on the subject in the viewport.
3. (Optional) Click **- Add Background (BG)** to place exclusion markers on unwanted background regions.
4. When **Auto Preview Single Frame** is enabled, SAM 2 immediately renders a sub-second live mask preview on the viewport.
5. Manage or edit points from the **Prompt Points & Preview** UI list.

### Step 3: Frame-by-Frame Stepping
- Use **◀ Step Back (F...→F...)** or **Step Forward ▶ (F...→F...)** in the **Sequence Mask Tracking** panel to step frame-by-frame.
- SAM 2 will dynamically track and update mask previews for adjacent frames to help you verify tracking accuracy before full sequence generation.

### Step 4: Multi-Layer Rotoscoping (Optional)
- To rotoscope multiple distinct objects or layers (e.g. multiple dancers or separate body parts):
  1. Under **Frame Range & Output Folder**, set **Subfolder / Layer** (e.g. `dancer_1`, `head`).
  2. Perform point prompting and tracking for that layer.
  3. Change the subfolder name (e.g. `dancer_2`) for the next subject.
  4. The **Load & Combine Mattes** operator will automatically find all layer subfolders and join them into a combined master matte.

### Step 5: Run Full Sequence Tracking
1. In the **Sequence Mask Tracking** panel, choose your propagation mode:
   - **◀ Backwards**: Tracks from current prompt frame back to Start frame.
   - **▶ Forwards**: Tracks from current prompt frame forward to End frame.
   - **◀▶ Track Both Directions**: Tracks in both directions to cover the entire frame range.
2. Tracking runs asynchronously in the background. You can monitor progress via the progress bar in the panel or click **⏹ Stop / Cancel Tracking** to stop at any time.

### Step 6: Load Mattes into Blender Compositor
1. Expand **Visual Overlay & Compositor** and choose a **Compositor Preset**:
   - **Cutout Object (Alpha)**: Isolates the subject over transparency.
   - **3D Sandwich (Objects Behind)**: Places 3D elements behind the subject.
   - **Tint Preview Overlay**: Overlays mask tint onto the original video.
2. Click **Load & Combine Mattes** (or enable **Auto-build Compositor Pipeline**).
3. Switch to the **Compositor** workspace to view the generated node network connected to your render output.

---

## Compositing Presets Explained

| Preset | Description | Node Structure |
| :--- | :--- | :--- |
| **Cutout Object (Alpha)** | Cuts out the foreground subject from the original video with a transparent alpha channel. | Movie Clip → Set Alpha (Mask input) → Composite Output |
| **3D Sandwich** | Composite 3D render objects or text *behind* the rotoscoped video subject. | Movie Clip + 3D Render Layer combined with Alpha Over driven by the SAM 2 matte |
| **Tint Preview Overlay** | Overlays a tinted color mask over the video stream for quality verification. | Movie Clip + Mask Tint mixed via MixRGB |

---

## Daemon & Maintenance Controls

- **Reset / Restart Daemon**: Found in **Extension Preferences**. Click to terminate and restart the persistent background Python server if memory needs clearing or configuration changes occur.
- **Open Log File**: Appears in the **AI Roto** panel after running an operation. Opens full stdout/stderr background logs in Blender's text editor for troubleshooting.
- **Clear All Masks**: Purges generated PNG matte sequence files from the output directory.

---

## Changelog: What's New in v0.2.0 (since v0.1.0)

### ⚡ Performance & Asynchronous Engine
- **Non-Blocking Background Worker**: Offloaded sequence tracking to background execution to prevent Blender's UI from hanging during long tracking operations.
- **Daemon Process Stability**: Added robust socket handling, connection timeouts, automatic recovery, and a **Reset / Restart Daemon** button in Preferences.
- **Live Progress & Cancellation**: Real-time percentage progress bar and a **⏹ Stop / Cancel Tracking** operator to safely interrupt sequence tracking at any time.

### 🎬 Tracking & UX Enhancements
- **Interactive Frame Stepping**: Added `◀ Step Back` and `Step Forward ▶` operators for step-by-step frame propagation and immediate validation before full tracking.
- **Multi-Direction Tracking**: Dedicated one-click controls for tracking `Backwards`, `Forwards`, or `Both Directions`.
- **Improved Point Selection UX**: Dynamic viewport cursor changes (`EYEDROPPER` / `CROSSHAIR`), status bar feedback, and instant viewport redraws.
- **Log Inspection**: Integrated **Open Log File** operator to view worker execution logs directly in Blender's text editor.

### 🎨 Multi-Layer Compositing & Pipeline Presets
- **Subfolder Layer Management**: Organizes masks into separate subfolders (e.g. `dancer_1`, `head`) for multi-subject rotoscoping.
- **Auto Multi-Layer Combination**: **Load & Combine Mattes** automatically aggregates and combines all subfolder layers into a unified matte.
- **1-Click Compositor Presets**: Added presets for **Cutout Object (Alpha)**, **3D Sandwich**, and **Tint Preview Overlay**.
- **Frame Count Synchronization**: Fixed Compositor Image Sequence nodes to automatically match Movie Clip duration and frame count.
- **Clean Maintenance**: Added **Clear All Masks** operator to purge generated image sequences and fixed duplicate node creation.

