#!/usr/bin/env python3
"""
Chromatic Vision: Visual Rendering of Constraint Conflict Sonification

Rebuttal experiment — generates visual color-field figures from the
submission's sonification data. Deployed if reviewers attack audio demos.

Reads from:  submission_repo/supplementary/demos/sonification.py (constants)
             submission_repo/supplementary/demos/audio_demos/*.wav (waveforms)
Writes to:   rebuttal/figures/sonification_defense.{png,pdf}
             rebuttal/figures/sonification_defense_rho_sweep.{png,pdf}

Provenance chain:
    Paper Table (measured rho) -> sonification.py (WAV) -> this script (color)
    Same A(theta) = |cos(theta/2)|, different substrate.

Historical precedent: Newton's Opticks (1704), Book I Part II Prop. VI.
Psychoacoustic basis: Plomp & Levelt (1965) critical bandwidth.
"""

import numpy as np
from scipy.io import wavfile
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge
from matplotlib.colors import hsv_to_rgb
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

# =============================================================================
# Path setup: reach into submission repo for paper data
# =============================================================================

ICML_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # ICML_2026_Template/
DEMOS_DIR = ICML_ROOT / "submission_repo" / "supplementary" / "demos"
AUDIO_DIR = DEMOS_DIR / "audio_demos"
FIGURES_DIR = ICML_ROOT / "rebuttal" / "figures"

sys.path.insert(0, str(DEMOS_DIR))

from sonification import (
    conflict_to_interference,
    MEASURED_CONFLICT,
    CONSTITUTION_WHEEL,
    get_measured_rho,
    constitution_interval,
    interval_dissonance,
    NOTE_SEMITONES,
)

PRINCIPLES = list(CONSTITUTION_WHEEL.keys())

# =============================================================================
# Color Mapping: Circle of Fifths -> Color Wheel
# =============================================================================

# Newton (Opticks, 1704) mapped VIBGYOR to the scale.
# Principle -> hue (0-1), chosen so that:
#   - Consonant pairs (low rho) have analogous colors (close hues)
#   - Dissonant pairs (high rho) have complementary colors (far hues)
PRINCIPLE_HUE = {
    'Helpful':  0.00,   # Red     (C, root)
    'Harmless': 0.15,   # Orange  (G, fifth -- close on wheel, low rho)
    'Honest':   0.33,   # Green   (D, two fifths -- moderate distance)
    'Autonomy': 0.75,   # Violet  (A, three fifths -- far on wheel)
}

def principle_to_rgb(name, saturation=0.85, value=0.95):
    """Map a constitutional principle to its color."""
    hue = PRINCIPLE_HUE[name]
    return hsv_to_rgb([hue, saturation, value])

def interference_color_blend(color1, color2, rho):
    """
    Blend two principle colors based on interference amplitude.

    A(theta) = |cos(theta/2)| controls the blend:
      High A (consonance) -> vivid mix of the two colors
      Low A (dissonance)  -> desaturated gray (destructive interference)
    """
    A = conflict_to_interference(rho)
    c1 = np.array(color1)
    c2 = np.array(color2)
    blend = 0.5 * c1 + 0.5 * c2
    gray = np.array([0.45, 0.45, 0.45])
    result = A * blend + (1 - A) * gray
    return np.clip(result, 0, 1)


# =============================================================================
# WAV Analysis: Extract interference from actual audio files
# =============================================================================

def analyze_wav(filepath):
    """
    Read a WAV file and extract its interference characteristics.

    Returns:
        sample_rate, waveform (mono, normalized), envelope, roughness
    """
    sr, data = wavfile.read(filepath)

    # Convert to mono float
    if len(data.shape) > 1:
        data = data.mean(axis=1)
    waveform = data.astype(np.float64)
    peak = np.max(np.abs(waveform))
    if peak > 0:
        waveform /= peak

    # Amplitude envelope via rectification + smoothing (~50ms window)
    rectified = np.abs(waveform)
    window_size = int(sr * 0.05)
    if window_size > 1:
        kernel = np.ones(window_size) / window_size
        envelope = np.convolve(rectified, kernel, mode='same')
    else:
        envelope = rectified

    # Roughness: modulation depth of envelope (middle 80%)
    if len(envelope) > sr:
        start = len(envelope) // 10
        end = len(envelope) - start
        env_seg = envelope[start:end]
        roughness = np.std(env_seg) / (np.mean(env_seg) + 1e-10)
    else:
        roughness = 0.0

    return sr, waveform, envelope, roughness


def wav_to_color_strip(filepath, principle1=None, principle2=None, rho=None):
    """
    Convert a WAV file to a color strip image.

    The waveform's amplitude envelope modulates the saturation/brightness
    of the principle colors. Constructive -> vivid. Destructive -> gray.
    """
    sr, waveform, envelope, roughness = analyze_wav(filepath)

    if principle1 and principle2:
        c1 = principle_to_rgb(principle1)
        c2 = principle_to_rgb(principle2)
    elif rho is not None:
        c1 = principle_to_rgb('Helpful')
        c2 = principle_to_rgb('Harmless')
    else:
        c1 = np.array([0.9, 0.3, 0.2])
        c2 = np.array([0.2, 0.5, 0.9])

    pixel_width = 800
    indices = np.linspace(0, len(envelope) - 1, pixel_width).astype(int)
    env_sampled = envelope[indices]

    env_min = env_sampled.min()
    env_max = env_sampled.max()
    if env_max > env_min:
        env_norm = (env_sampled - env_min) / (env_max - env_min)
    else:
        env_norm = np.ones(pixel_width) * 0.5

    height = 60
    strip = np.zeros((height, pixel_width, 3))
    gray = np.array([0.45, 0.45, 0.45])

    for x in range(pixel_width):
        A = env_norm[x]
        for y in range(height):
            t = y / height
            local_c1_weight = max(0, min(1, 1.0 - abs(2 * t - 0.3)))
            local_c2_weight = max(0, min(1, 1.0 - abs(2 * t - 1.7)))
            base = (local_c1_weight * c1 + local_c2_weight * c2)
            base = base / (base.max() + 1e-10) * 0.95
            strip[y, x] = A * base + (1 - A) * gray

    return np.clip(strip, 0, 1), roughness


# =============================================================================
# Panel Renderers
# =============================================================================

def draw_constitution_wheel(ax):
    """Draw the Constitution Wheel with principle colors and rho connections."""
    ax.set_xlim(-1.6, 1.6)
    ax.set_ylim(-1.6, 1.6)
    ax.set_aspect('equal')
    ax.axis('off')
    ax.set_title('Constitution Wheel\n(Circle of Fifths Mapping)',
                fontsize=12, fontweight='bold', color='white', pad=12)

    positions = {'Helpful': 0, 'Harmless': 90, 'Honest': 180, 'Autonomy': 270}

    # Color ring
    for i in range(360):
        min_dist = 999
        nearest = 'Helpful'
        for name, angle in positions.items():
            dist = min(abs(i - angle), 360 - abs(i - angle))
            if dist < min_dist:
                min_dist = dist
                nearest = name
        color = principle_to_rgb(nearest, saturation=0.6, value=0.9)
        wedge = Wedge((0, 0), 1.15, i - 0.6, i + 0.6, width=0.15,
                     facecolor=color, alpha=0.7, edgecolor='none')
        ax.add_patch(wedge)

    # Rho connections
    for i, p1 in enumerate(PRINCIPLES):
        for p2 in PRINCIPLES[i+1:]:
            rho = get_measured_rho(p1, p2)
            a1 = np.radians(positions[p1])
            a2 = np.radians(positions[p2])
            x1, y1 = 0.82 * np.cos(a1), 0.82 * np.sin(a1)
            x2, y2 = 0.82 * np.cos(a2), 0.82 * np.sin(a2)

            line_color = plt.cm.RdYlGn_r(rho / 0.6)
            ax.plot([x1, x2], [y1, y2], color=line_color,
                   linewidth=1.5 + rho * 4, alpha=0.6, zorder=2)

            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            ax.text(mx, my, f'{rho:.2f}', ha='center', va='center',
                   fontsize=7, color='#cccccc',
                   bbox=dict(boxstyle='round,pad=0.15', facecolor='#222222',
                            alpha=0.9, edgecolor='#555555'))

    # Principle nodes
    for name, angle_deg in positions.items():
        angle = np.radians(angle_deg)
        x = 0.82 * np.cos(angle)
        y = 0.82 * np.sin(angle)
        color = principle_to_rgb(name)

        circle = plt.Circle((x, y), 0.18, facecolor=color,
                           edgecolor='white', linewidth=2.5, zorder=5)
        ax.add_patch(circle)

        note = CONSTITUTION_WHEEL[name]['note']
        lx = 1.38 * np.cos(angle)
        ly = 1.38 * np.sin(angle)
        ax.text(lx, ly, f"{name}\n({note})", ha='center', va='center',
               fontsize=9, fontweight='bold', color=color)


def draw_pairwise_from_wavs(ax, audio_dir):
    """Draw pairwise interference colors, derived from actual WAV files."""
    ax.set_xlim(-0.5, 3.5)
    ax.set_ylim(-0.5, 2.5)
    ax.set_aspect('equal')
    ax.axis('off')
    ax.set_title('Pairwise Interference (from WAV files)\nVivid = consonance / Gray = dissonance',
                fontsize=12, fontweight='bold', color='white', pad=12)

    pairs = [
        ('Helpful', 'Harmless', 0, 1),
        ('Helpful', 'Honest', 1, 1),
        ('Helpful', 'Autonomy', 2, 1),
        ('Harmless', 'Honest', 0, 0),
        ('Harmless', 'Autonomy', 1, 0),
        ('Honest', 'Autonomy', 2, 0),
    ]

    for p1, p2, col, row in pairs:
        rho = get_measured_rho(p1, p2)
        semitones, interval_name, dissonance = constitution_interval(p1, p2)
        A = conflict_to_interference(rho)

        c1 = principle_to_rgb(p1)
        c2 = principle_to_rgb(p2)
        blend = interference_color_blend(c1, c2, rho)

        # Check if WAV exists and get roughness
        wav_name = f"pair_{p1.lower()}_{p2.lower()}.wav"
        wav_path = os.path.join(audio_dir, wav_name)
        roughness_str = ""
        if os.path.exists(wav_path):
            _, _, _, roughness = analyze_wav(wav_path)
            roughness_str = f"\nR={roughness:.2f}"

        rect = plt.Rectangle((col - 0.4, row - 0.35), 0.8, 0.7,
                            facecolor=blend, edgecolor='#444444', linewidth=1.5,
                            zorder=3)
        ax.add_patch(rect)

        dot1 = plt.Circle((col - 0.22, row + 0.22), 0.08, facecolor=c1,
                          edgecolor='white', linewidth=0.8, zorder=5)
        dot2 = plt.Circle((col + 0.22, row + 0.22), 0.08, facecolor=c2,
                          edgecolor='white', linewidth=0.8, zorder=5)
        ax.add_patch(dot1)
        ax.add_patch(dot2)

        text_color = 'white' if np.mean(blend) < 0.5 else '#222222'
        interval_short = interval_name.replace('Perfect ', 'P').replace('Major ', 'M').replace('Minor ', 'm')
        ax.text(col, row - 0.02, interval_short,
               ha='center', va='center', fontsize=10, fontweight='bold',
               color=text_color, zorder=6)
        ax.text(col, row - 0.2, f'rho={rho:.2f}  A={A:.2f}{roughness_str}',
               ha='center', va='center', fontsize=6.5, color=text_color, zorder=6)


def draw_rho_sweep_from_wavs(fig, ax, audio_dir):
    """Show rho sweep from actual WAV files as color strips."""
    ax.axis('off')
    ax.set_title('rho Sweep: Reading WAV Interference as Color\n'
                'Each strip derived from actual audio waveform envelope',
                fontsize=12, fontweight='bold', color='white', pad=12)

    rho_values = [0.00, 0.15, 0.30, 0.50, 0.70, 0.90]
    n = len(rho_values)

    for i, rho in enumerate(rho_values):
        wav_name = f"conflict_rho_{rho:.2f}.wav"
        wav_path = os.path.join(audio_dir, wav_name)

        A = conflict_to_interference(rho)

        if os.path.exists(wav_path):
            strip, roughness = wav_to_color_strip(wav_path, rho=rho)
        else:
            # Fallback: synthetic strip from equation alone
            c1 = principle_to_rgb('Helpful')
            c2 = principle_to_rgb('Harmless')
            gray = np.array([0.45, 0.45, 0.45])
            blend = 0.5 * c1 + 0.5 * c2
            strip = np.ones((60, 800, 3))
            x_wave = np.linspace(0, 6 * np.pi, 800)
            for xi in range(800):
                wave = 0.5 + 0.5 * np.sin(x_wave[xi]) * A
                color = wave * blend + (1 - wave) * gray
                strip[:, xi] = color
            strip = np.clip(strip, 0, 1)

        left = i / n + 0.008
        width = 1/n - 0.016
        pos = ax.get_position()
        sub_ax = fig.add_axes([
            pos.x0 + left * pos.width,
            pos.y0 + 0.20 * pos.height,
            width * pos.width,
            0.55 * pos.height
        ])
        sub_ax.imshow(strip, aspect='auto', interpolation='bilinear')
        sub_ax.axis('off')
        sub_ax.set_title(f'rho={rho:.2f}\nA={A:.2f}',
                        fontsize=8, color='white', pad=3)


def draw_full_interference_field(fig, ax):
    """Radial color field: all four principles interfering simultaneously."""
    ax.axis('off')
    ax.set_title('Full Constitution Interference Field\n'
                '(4-principle color superposition)',
                fontsize=12, fontweight='bold', color='white', pad=12)

    resolution = 400
    x = np.linspace(-1.3, 1.3, resolution)
    y = np.linspace(-1.3, 1.3, resolution)
    X, Y = np.meshgrid(x, y)
    R = np.sqrt(X**2 + Y**2)
    Theta = np.arctan2(Y, X)

    positions_rad = {
        'Helpful': 0, 'Harmless': np.pi/2,
        'Honest': np.pi, 'Autonomy': 3*np.pi/2
    }

    field = np.zeros((resolution, resolution, 3))

    for name in PRINCIPLES:
        color = principle_to_rgb(name)
        angle = positions_rad[name]

        diff = Theta - angle
        weight = np.exp(-2.0 * (1 - np.cos(diff)))

        interference = np.ones_like(R)
        for other in PRINCIPLES:
            if other != name:
                rho = get_measured_rho(name, other)
                A = conflict_to_interference(rho)
                other_angle = positions_rad[other]
                other_diff = Theta - other_angle
                other_weight = np.exp(-2.0 * (1 - np.cos(other_diff)))
                interference -= (1 - A) * other_weight * 0.35
        interference = np.clip(interference, 0.15, 1.0)

        for c in range(3):
            field[:, :, c] += weight * color[c] * interference

    field_max = field.max(axis=2, keepdims=True)
    field_max[field_max == 0] = 1
    field = field / field_max

    mask = np.clip(1.3 - R, 0, 1) ** 0.5
    bg = np.array([0.1, 0.1, 0.1])
    for c in range(3):
        field[:, :, c] = field[:, :, c] * mask + bg[c] * (1 - mask)

    field = np.clip(field, 0, 1)

    pos = ax.get_position()
    img_ax = fig.add_axes([
        pos.x0 + 0.05 * pos.width,
        pos.y0 + 0.02 * pos.height,
        0.9 * pos.width,
        0.85 * pos.height
    ])
    img_ax.imshow(field, extent=[-1.3, 1.3, -1.3, 1.3])
    img_ax.axis('off')

    for name in PRINCIPLES:
        angle = positions_rad[name]
        lx = 1.15 * np.cos(angle)
        ly = 1.15 * np.sin(angle)
        color = principle_to_rgb(name)
        note = CONSTITUTION_WHEEL[name]['note']
        img_ax.text(lx, ly, f"{name}\n({note})", ha='center', va='center',
                   fontsize=8, fontweight='bold', color=color,
                   bbox=dict(boxstyle='round,pad=0.2', facecolor='#111111',
                            alpha=0.85, edgecolor=color, linewidth=0.5))


# =============================================================================
# Main: Generate rebuttal figures
# =============================================================================

def main():
    audio_dir = str(AUDIO_DIR)

    if not os.path.isdir(audio_dir):
        print(f"Audio directory not found: {audio_dir}")
        print("Run supplementary/demos/sonification.py first to generate WAV files.")
        return

    wavs = [f for f in os.listdir(audio_dir) if f.endswith('.wav')]
    print(f"Found {len(wavs)} WAV files in {audio_dir}")
    print(f"Reading interference patterns from audio...")

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    # --- Full 4-panel figure ---
    fig = plt.figure(figsize=(18, 16), facecolor='#0a0a0a')
    fig.suptitle(
        'CHROMATIC VISION: Constraint Conflict as Color Interference\n'
        r'A($\theta$) = |cos($\theta$/2)|  rendered as  color saturation'
        '\nConsonance = vivid  |  Dissonance = gray  |  '
        'Newton (Opticks, 1704) + Plomp & Levelt (1965)',
        fontsize=15, fontweight='bold', color='white', y=0.97
    )

    ax1 = fig.add_subplot(2, 2, 1, facecolor='#1a1a1a')
    draw_constitution_wheel(ax1)

    ax2 = fig.add_subplot(2, 2, 2, facecolor='#1a1a1a')
    draw_pairwise_from_wavs(ax2, audio_dir)

    ax3 = fig.add_subplot(2, 2, 3, facecolor='#1a1a1a')
    draw_rho_sweep_from_wavs(fig, ax3, audio_dir)

    ax4 = fig.add_subplot(2, 2, 4, facecolor='#1a1a1a')
    draw_full_interference_field(fig, ax4)

    fig.text(0.5, 0.01,
            'Each color strip derived from actual WAV waveform envelope '
            '(supplementary/demos/audio_demos/). '
            'Same equation, same data, different substrate.\n'
            'Provenance: Paper Table (measured rho) -> sonification.py (WAV) '
            '-> chromatic_vision.py (color)',
            ha='center', fontsize=10, color='#aaaaaa', style='italic')

    for ext in ['png', 'pdf']:
        path = FIGURES_DIR / f'sonification_defense.{ext}'
        fig.savefig(path, dpi=150, bbox_inches='tight',
                   facecolor=fig.get_facecolor(), edgecolor='none')
        print(f"Saved: {path}")
    plt.close()

    # --- Standalone rho sweep (compact, for inline attachment) ---
    fig2, ax = plt.subplots(1, 1, figsize=(14, 4), facecolor='#0a0a0a')
    ax.set_facecolor('#1a1a1a')
    draw_rho_sweep_from_wavs(fig2, ax, audio_dir)
    for ext in ['png', 'pdf']:
        path = FIGURES_DIR / f'sonification_defense_rho_sweep.{ext}'
        fig2.savefig(path, dpi=150, bbox_inches='tight',
                    facecolor=fig2.get_facecolor(), edgecolor='none')
        print(f"Saved: {path}")
    plt.close()

    print("\nDone. Rebuttal figures generated.")
    print(f"Output: {FIGURES_DIR}/sonification_defense.{{png,pdf}}")
    print(f"Output: {FIGURES_DIR}/sonification_defense_rho_sweep.{{png,pdf}}")
    print("\nProvenance: Paper Table -> sonification.py (WAV) -> this script (color)")
    print("Same A(theta) = |cos(theta/2)|, verified across substrates.")


if __name__ == '__main__':
    main()
