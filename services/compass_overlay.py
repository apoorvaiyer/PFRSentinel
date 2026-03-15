"""
Compass rose overlay for astrophotography images.

Renders a compass showing cardinal directions (N/S/E/W) with
configurable rotation angle and position.
"""
import math
from PIL import Image, ImageDraw, ImageFont


# Default compass settings
DEFAULT_SIZE = 80
DEFAULT_COLOR = (255, 255, 255, 200)
DEFAULT_LABEL_COLOR = (255, 255, 255, 255)


def draw_compass(image, rotation=0, position='bottom-right',
                 size=DEFAULT_SIZE, color=DEFAULT_COLOR,
                 label_color=DEFAULT_LABEL_COLOR, margin=20):
    """Draw a compass rose on an image.

    Args:
        image: PIL Image (RGBA or RGB — will be converted to RGBA)
        rotation: Rotation angle in degrees (0 = North is up)
        position: One of 'center', 'top-left', 'top-right',
                  'bottom-left', 'bottom-right'
        size: Compass diameter in pixels
        color: RGBA tuple for compass lines
        label_color: RGBA tuple for N/S/E/W labels
        margin: Pixel margin from image edge

    Returns:
        Modified PIL Image (RGBA)
    """
    if image.mode != 'RGBA':
        image = image.convert('RGBA')

    img_w, img_h = image.size

    # Don't draw if image is too small
    if img_w < size + margin * 2 or img_h < size + margin * 2:
        return image

    # Calculate center position
    cx, cy = _get_center(img_w, img_h, size, position, margin)
    radius = size // 2

    # Create overlay for compositing
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Draw compass circle
    circle_color = (*color[:3], color[3] // 3)
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        outline=color, width=2
    )

    # Draw cardinal direction lines
    rot_rad = math.radians(rotation)
    directions = [
        ('N', 0), ('E', 90), ('S', 180), ('W', 270)
    ]

    try:
        font = ImageFont.truetype("arial.ttf", max(12, size // 5))
    except (OSError, IOError):
        font = ImageFont.load_default()

    for label, angle_deg in directions:
        angle_rad = math.radians(angle_deg) + rot_rad

        # Line from center toward direction
        inner_r = radius * 0.3
        outer_r = radius * 0.75
        x_inner = cx + inner_r * math.sin(angle_rad)
        y_inner = cy - inner_r * math.cos(angle_rad)
        x_outer = cx + outer_r * math.sin(angle_rad)
        y_outer = cy - outer_r * math.cos(angle_rad)

        # North line is thicker
        width = 3 if label == 'N' else 1
        draw.line([(x_inner, y_inner), (x_outer, y_outer)],
                  fill=color, width=width)

        # Draw label outside the circle
        label_r = radius * 0.92
        lx = cx + label_r * math.sin(angle_rad)
        ly = cy - label_r * math.cos(angle_rad)

        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((lx - tw / 2, ly - th / 2), label,
                  fill=label_color, font=font)

    # Composite onto original
    image = Image.alpha_composite(image, overlay)
    return image


def _get_center(img_w, img_h, size, position, margin):
    """Calculate compass center coordinates for the given position."""
    radius = size // 2
    positions = {
        'center': (img_w // 2, img_h // 2),
        'top-left': (margin + radius, margin + radius),
        'top-right': (img_w - margin - radius, margin + radius),
        'bottom-left': (margin + radius, img_h - margin - radius),
        'bottom-right': (img_w - margin - radius, img_h - margin - radius),
    }
    return positions.get(position, positions['bottom-right'])
