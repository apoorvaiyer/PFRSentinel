"""
Compass rose overlay for astrophotography images.

Renders an 8-point star compass rose with cardinal (N/S/E/W) and
ordinal (NE/SE/SW/NW) points, configurable rotation and position.
"""
import math
from PIL import Image, ImageDraw, ImageFont


# Default compass settings
DEFAULT_SIZE = 80
DEFAULT_COLOR = (255, 255, 255, 200)
DEFAULT_LABEL_COLOR = (255, 255, 255, 255)


def draw_compass(image, rotation=0, position='bottom-right',
                 size=DEFAULT_SIZE, color=DEFAULT_COLOR,
                 label_color=DEFAULT_LABEL_COLOR, margin=20,
                 cx=None, cy=None):
    """Draw an 8-point star compass rose on an image.

    Args:
        image: PIL Image (RGBA or RGB — will be converted to RGBA)
        rotation: Rotation angle in degrees (0 = North is up)
        position: One of 'center', 'top-left', 'top-right',
                  'bottom-left', 'bottom-right' (ignored if cx/cy given)
        size: Compass diameter in pixels
        color: RGBA tuple for compass lines/fill
        label_color: RGBA tuple for N/S/E/W labels
        margin: Pixel margin from image edge (ignored if cx/cy given)
        cx: Optional explicit center X coordinate
        cy: Optional explicit center Y coordinate

    Returns:
        Modified PIL Image (RGBA)
    """
    if image.mode != 'RGBA':
        image = image.convert('RGBA')

    img_w, img_h = image.size

    # Calculate center position
    if cx is not None and cy is not None:
        pass
    else:
        if img_w < size + margin * 2 or img_h < size + margin * 2:
            return image
        cx, cy = _get_center(img_w, img_h, size, position, margin)

    radius = size // 2

    # Create overlay for compositing
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    rot_rad = math.radians(rotation)

    # Derive colors from the base color
    fill_light = color
    fill_dark = (color[0] // 3, color[1] // 3, color[2] // 3, color[3])
    outline = (*color[:3], min(255, color[3] + 30))

    # --- Outer circle ---
    circle_r = radius * 0.72
    draw.ellipse(
        [cx - circle_r, cy - circle_r, cx + circle_r, cy + circle_r],
        outline=outline, width=max(1, size // 50)
    )

    # --- 8-point star ---
    # Cardinal points (N, E, S, W) are longer, ordinals are shorter
    cardinal_len = radius * 0.68   # tip of cardinal points
    ordinal_len = radius * 0.45    # tip of ordinal points
    half_base = radius * 0.12      # half-width of each diamond at the base

    for i, angle_deg in enumerate(range(0, 360, 45)):
        is_cardinal = (i % 2 == 0)
        tip_r = cardinal_len if is_cardinal else ordinal_len
        angle = math.radians(angle_deg) + rot_rad

        # Tip of this point
        tip_x = cx + tip_r * math.sin(angle)
        tip_y = cy - tip_r * math.cos(angle)

        # Two base vertices (perpendicular to the point direction)
        perp = angle + math.pi / 2
        base_x1 = cx + half_base * math.sin(perp)
        base_y1 = cy - half_base * math.cos(perp)
        base_x2 = cx - half_base * math.sin(perp)
        base_y2 = cy + half_base * math.cos(perp)

        # Each point is split into two triangles (light/dark halves)
        # Left half
        draw.polygon(
            [(cx, cy), (base_x1, base_y1), (tip_x, tip_y)],
            fill=fill_light, outline=outline
        )
        # Right half (darker)
        draw.polygon(
            [(cx, cy), (tip_x, tip_y), (base_x2, base_y2)],
            fill=fill_dark, outline=outline
        )

    # --- Inner circle (center dot) ---
    inner_r = radius * 0.07
    draw.ellipse(
        [cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r],
        fill=fill_light, outline=outline
    )

    # --- Cardinal labels (N, E, S, W) ---
    try:
        font = ImageFont.truetype("arial.ttf", max(10, size // 6))
    except (OSError, IOError):
        font = ImageFont.load_default()

    for label_text, angle_deg in [('N', 0), ('E', 90), ('S', 180), ('W', 270)]:
        angle = math.radians(angle_deg) + rot_rad
        label_r = radius * 0.88
        lx = cx + label_r * math.sin(angle)
        ly = cy - label_r * math.cos(angle)

        bbox = draw.textbbox((0, 0), label_text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        # Draw text with dark outline for readability on any background
        ox, oy = lx - tw / 2, ly - th / 2
        shadow = (0, 0, 0, min(200, color[3]))
        for dx, dy in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
            draw.text((ox + dx, oy + dy), label_text, fill=shadow, font=font)
        draw.text((ox, oy), label_text, fill=label_color, font=font)

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
