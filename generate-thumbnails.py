#!/usr/bin/env python3
"""Generate YouTube thumbnails for Bubble Sort, Methods, and Array Sum tutorials."""

from PIL import Image, ImageDraw, ImageFont
import os

OUT = "/home/mtjikuzu/dev/autonomous-recording/output"
W, H = 1280, 720


# Font paths
def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


FONT_HEAVY = "/usr/share/fonts/TTF/JetBrainsMonoNLNerdFont-ExtraBold.ttf"
FONT_BOLD = "/usr/share/fonts/TTF/JetBrainsMonoNLNerdFont-Bold.ttf"
FONT_SANS = "/usr/share/fonts/liberation/LiberationSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/liberation/LiberationMono-Bold.ttf"

BG = "#0D1117"
PANEL_BG = "#161B22"
WHITE = "#FFFFFF"
DARK_TEXT = "#0D1117"
KW_COLOR = "#FF7B72"  # keywords
VAL_COLOR = "#79C0FF"  # values / types
BASE_COLOR = "#E6EDF3"  # plain code text
COMMENT_CLR = "#8B949E"  # comments


def draw_shadow_text(
    draw, pos, text, font, color, shadow_color="#000000", offset=(4, 4)
):
    sx, sy = pos[0] + offset[0], pos[1] + offset[1]
    draw.text((sx, sy), text, font=font, fill=shadow_color + "88")
    draw.text(pos, text, font=font, fill=color)


def draw_badge(draw, x, y, text, accent):
    f = load_font(FONT_SANS, 34)
    bbox = draw.textbbox((0, 0), text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 20, 10
    rx0, ry0 = x, y
    rx1, ry1 = x + tw + pad_x * 2, y + th + pad_y * 2
    draw.rounded_rectangle([rx0, ry0, rx1, ry1], radius=10, fill=accent)
    draw.text((rx0 + pad_x, ry0 + pad_y), text, font=f, fill=DARK_TEXT)


def draw_code_panel(draw, x, y, w, h, lines_data, font_size=24):
    """lines_data: list of (text, color) tuples per line."""
    draw.rounded_rectangle([x, y, x + w, y + h], radius=16, fill=PANEL_BG)
    # Dot decorations (macOS-style)
    for i, dot_color in enumerate(["#FF5F56", "#FFBD2E", "#27C93F"]):
        draw.ellipse([x + 18 + i * 22, y + 14, x + 30 + i * 22, y + 26], fill=dot_color)

    f = load_font(FONT_MONO, font_size)
    line_h = font_size + 8
    cy = y + 46
    for text, color in lines_data:
        draw.text((x + 18, cy), text, font=f, fill=color)
        cy += line_h


def make_thumbnail(path, accent, title_lines, code_lines, bottom_label):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Background gradient (subtle)
    for row in range(H):
        alpha = int(row / H * 30)
        r, g, b = 0x0D + alpha // 3, 0x11 + alpha // 3, 0x17 + alpha // 3
        draw.line([(0, row), (W, row)], fill=(r, g, b))

    # Left accent stripe
    draw.rectangle([(0, 0), (18, H)], fill=accent)

    # JAVA badge
    draw_badge(draw, 46, 38, "JAVA", accent)

    # Main title — left side
    f_big = load_font(FONT_HEAVY, title_lines[0][1])
    f_sub = load_font(FONT_HEAVY, title_lines[1][1]) if len(title_lines) > 1 else None

    draw_shadow_text(draw, (46, title_lines[0][2]), title_lines[0][0], f_big, WHITE)
    if f_sub:
        draw_shadow_text(draw, (46, title_lines[1][2]), title_lines[1][0], f_sub, WHITE)

    # Bottom-left label
    f_label = load_font(FONT_SANS, 30)
    draw_shadow_text(draw, (46, 648), bottom_label, f_label, accent)

    # Right code panel
    panel_x, panel_y = 660, 60
    panel_w, panel_h = 590, 590
    draw_code_panel(draw, panel_x, panel_y, panel_w, panel_h, code_lines, font_size=23)

    img.save(path, "PNG")
    print(f"DONE: {path}")


# ── Bubble Sort thumbnail ──────────────────────────────────────────────────────
bs_code = [
    ("for (int i = 0; i < n-1; i++) {", KW_COLOR),
    ("  for (int j = 0; j < n-i-1; j++) {", KW_COLOR),
    ("    if (arr[j] > arr[j+1]) {", KW_COLOR),
    ("      int temp = arr[j];", VAL_COLOR),
    ("      arr[j] = arr[j+1];", BASE_COLOR),
    ("      arr[j+1] = temp;", BASE_COLOR),
    ("    }", BASE_COLOR),
    ("  }", BASE_COLOR),
    ("}", BASE_COLOR),
    ("", BASE_COLOR),
    ("// Input:  {64,34,25,12,22,11,90}", COMMENT_CLR),
    ("// Output: {11,12,22,25,34,64,90}", COMMENT_CLR),
]

make_thumbnail(
    path=f"{OUT}/bubblesort-thumbnail.png",
    accent="#F7C948",
    title_lines=[
        ("BUBBLE", 160, 150),
        ("SORT", 160, 320),
    ],
    code_lines=bs_code,
    bottom_label="O(n\u00b2) Time Complexity",
)

# ── Methods thumbnail ──────────────────────────────────────────────────────────
mt_code = [
    ("void greet() {", KW_COLOR),
    ('  println("Hello!");', VAL_COLOR),
    ("}", BASE_COLOR),
    ("", BASE_COLOR),
    ("int add(int a, int b) {", KW_COLOR),
    ("  return a + b;", VAL_COLOR),
    ("}", BASE_COLOR),
    ("", BASE_COLOR),
    ("int factorial(int n) {", KW_COLOR),
    ("  if (n <= 1) return 1;", KW_COLOR),
    ("  return n * factorial(n-1);", VAL_COLOR),
    ("}", BASE_COLOR),
]

make_thumbnail(
    path=f"{OUT}/methods-thumbnail.png",
    accent="#3FB950",
    title_lines=[
        ("METHODS", 130, 150),
        ("& FUNCTIONS", 80, 300),
    ],
    code_lines=mt_code,
    bottom_label="Overloading \u00b7 Recursion \u00b7 OOP",
)


# ── Array Sum thumbnail ───────────────────────────────────────────────────────
as_code = [
    ("int total = 0;", VAL_COLOR),
    ("for (int num : arr) {", KW_COLOR),
    ("    total += num;", VAL_COLOR),
    ("}", BASE_COLOR),
    ("return total;", KW_COLOR),
    ("", BASE_COLOR),
    ("// safeSum with guard clause", COMMENT_CLR),
    ("if (arr == null) return 0;", KW_COLOR),
    ("", BASE_COLOR),
    ("// {1,2,3,4,5,6} \u2192 21", COMMENT_CLR),
    ("// O(n) Linear Time", COMMENT_CLR),
]

make_thumbnail(
    path=f"{OUT}/arrays-total-thumbnail.png",
    accent="#58A6FF",
    title_lines=[
        ("ARRAY", 160, 150),
        ("SUM", 160, 320),
    ],
    code_lines=as_code,
    bottom_label="O(n) Linear Time \u00b7 Accumulator Pattern",
)
