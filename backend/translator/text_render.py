import os
import re
import unicodedata
import cv2
import numpy as np
import freetype
import functools
import logging
from pathlib import Path
from typing import Tuple, Optional, List

try:
    from hyphen import Hyphenator
    from hyphen.dictools import LANGUAGES as HYPHENATOR_LANGUAGES
    try:
        HYPHENATOR_LANGUAGES.remove('fr')
        HYPHENATOR_LANGUAGES.append('fr_FR')
    except Exception:
        pass
    _HYPHEN_AVAILABLE = True
except ImportError:
    _HYPHEN_AVAILABLE = False
    HYPHENATOR_LANGUAGES = []

try:
    from langcodes import standardize_tag
    _LANGCODES_AVAILABLE = True
except ImportError:
    _LANGCODES_AVAILABLE = False
    def standardize_tag(lang):
        return lang

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(_THIS_DIR, 'fonts')

# ------- inlined from utils/generic2.py -------
def is_punctuation(ch):
    cp = ord(ch)
    if ((cp >= 33 and cp <= 47) or (cp >= 58 and cp <= 64) or
            (cp >= 91 and cp <= 96) or (cp >= 123 and cp <= 126)):
        return True
    cat = unicodedata.category(ch)
    if cat.startswith("P"):
        return True
    return False
# ----------------------------------------------

CJK_H2V = {
    "‥": "︰",
    "—": "︱",
    "―": "|",
    "–": "︲",
    "_": "︳",
    "_": "︴",
    "(": "︵",
    ")": "︶",
    "（": "︵",
    "）": "︶",
    "{": "︷",
    "}": "︸",
    "〔": "︹",
    "〕": "︺",
    "【": "︻",
    "】": "︼",
    "《": "︽",
    "》": "︾",
    "〈": "︿",
    "〉": "﹀",
    "⟨": "︿",
    "⟩": "﹀",
    "⟪": "︿",
    "⟫": "﹀",
    "「": "﹁",
    "」": "﹂",
    "『": "﹃",
    "』": "﹄",
    "﹑": "﹅",
    "﹆": "﹆",
    "[": "﹇",
    "]": "﹈",
    "⦅": "︵",
    "⦆": "︶",
    "❨": "︵",
    "❩": "︶",
    "❪": "︷",
    "❫": "︸",
    "❬": "﹇",
    "❭": "﹈",
    "❮": "︿",
    "❯": "﹀",
    "﹉": "﹉",
    "﹊": "﹊",
    "﹋": "﹋",
    "﹌": "﹌",
    "﹍": "﹍",
    "﹎": "﹎",
    "﹏": "﹏",
    "…": "⋮",
    "⋯": "︙",
    "⋰": "⋮",
    "⋱": "⋮",
    "\u201c": "﹁",
    "\u201d": "﹂",
    "\u2018": "﹁",
    "\u2019": "﹂",
    "″": "﹂",
    "‴": "﹂",
    "‶": "﹁",
    "‷": "﹁",
    "~": "︴",
    "〜": "︴",
    "～": "︴",
    "〰": "︴",
    "!": "︕",
    "?": "︖",
    "؟": "︖",
    "¿": "︖",
    "¡": "︕",
    ".": "︒",
    "。": "︒",
    ";": "︔",
    "；": "︔",
    ":": "︓",
    "：": "︓",
    ",": "︐",
    "，": "︐",
    "‚": "︐",
    "„": "︐",
    "-": "︲",
    "−": "︲",
    "・": "·",
}

CJK_V2H = {
    **dict(zip(CJK_H2V.items(), CJK_H2V.keys())),
}

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def CJK_Compatibility_Forms_translate(cdpt: str, direction: int):
    if cdpt == 'ー' and direction == 1:
        return 'ー', 90
    if cdpt in CJK_V2H:
        if direction == 0:
            return CJK_V2H[cdpt], 0
        else:
            return cdpt, 0
    elif cdpt in CJK_H2V:
        if direction == 1:
            return CJK_H2V[cdpt], 0
        else:
            return cdpt, 0
    return cdpt, 0


def compact_special_symbols(text: str) -> str:
    text = text.replace('...', '…')
    text = text.replace('..', '…')
    pattern = r'([^\w\s])[ \u3000]+'
    text = re.sub(pattern, r'\1', text)
    return text


def rotate_image(image, angle):
    if angle == 0:
        return image, (0, 0)
    image_exp = np.zeros((round(image.shape[0] * 1.5), round(image.shape[1] * 1.5), image.shape[2]), dtype=np.uint8)
    diff_i = (image_exp.shape[0] - image.shape[0]) // 2
    diff_j = (image_exp.shape[1] - image.shape[1]) // 2
    image_exp[diff_i:diff_i+image.shape[0], diff_j:diff_j+image.shape[1]] = image
    image_center = tuple(np.array(image_exp.shape[1::-1]) / 2)
    rot_mat = cv2.getRotationMatrix2D(image_center, angle, 1.0)
    result = cv2.warpAffine(image_exp, rot_mat, image_exp.shape[1::-1], flags=cv2.INTER_LINEAR)
    if angle == 90:
        return result, (0, 0)
    return result, (diff_i, diff_j)


def add_color(bw_char_map, color, stroke_char_map, stroke_color):
    if bw_char_map.size == 0:
        fg = np.zeros((bw_char_map.shape[0], bw_char_map.shape[1], 4), dtype=np.uint8)
        return fg

    if stroke_color is None:
        x, y, w, h = cv2.boundingRect(bw_char_map)
    else:
        x, y, w, h = cv2.boundingRect(stroke_char_map)

    fg = np.zeros((h, w, 4), dtype=np.uint8)
    fg[:,:,0] = color[0]
    fg[:,:,1] = color[1]
    fg[:,:,2] = color[2]
    fg[:,:,3] = bw_char_map[y:y+h, x:x+w]

    if stroke_color is None:
        stroke_color = color
    bg = np.zeros((stroke_char_map.shape[0], stroke_char_map.shape[1], 4), dtype=np.uint8)
    bg[:,:,0] = stroke_color[0]
    bg[:,:,1] = stroke_color[1]
    bg[:,:,2] = stroke_color[2]
    bg[:,:,3] = stroke_char_map

    fg_alpha = fg[:, :, 3] / 255.0
    bg_alpha = 1.0 - fg_alpha
    bg[y:y+h, x:x+w, :] = (fg_alpha[:, :, np.newaxis] * fg[:, :, :] + bg_alpha[:, :, np.newaxis] * bg[y:y+h, x:x+w, :])
    return bg


FALLBACK_FONTS = [
    os.path.join(FONTS_DIR, 'Arial-Unicode-Regular.ttf'),
    os.path.join(FONTS_DIR, 'msyh.ttc'),
    os.path.join(FONTS_DIR, 'msgothic.ttc'),
]
FONT_SELECTION: List[freetype.Face] = []
font_cache = {}


def get_cached_font(path: str) -> freetype.Face:
    path = path.replace('\\', '/')
    if not font_cache.get(path):
        font_cache[path] = freetype.Face(Path(path).open('rb'))
    return font_cache[path]


def set_font(font_path: str):
    global FONT_SELECTION
    if font_path:
        selection = [font_path] + FALLBACK_FONTS
    else:
        selection = FALLBACK_FONTS
    FONT_SELECTION = [get_cached_font(p) for p in selection if os.path.exists(p)]
    if not FONT_SELECTION:
        raise FileNotFoundError(f"No fonts found. Looked in: {FONTS_DIR}")


class namespace:
    pass


class Glyph:
    def __init__(self, glyph):
        self.bitmap = namespace()
        self.bitmap.buffer = glyph.bitmap.buffer
        self.bitmap.rows = glyph.bitmap.rows
        self.bitmap.width = glyph.bitmap.width
        self.advance = namespace()
        self.advance.x = glyph.advance.x
        self.advance.y = glyph.advance.y
        self.bitmap_left = glyph.bitmap_left
        self.bitmap_top = glyph.bitmap_top
        self.metrics = namespace()
        self.metrics.vertBearingX = glyph.metrics.vertBearingX
        self.metrics.vertBearingY = glyph.metrics.vertBearingY
        self.metrics.horiBearingX = glyph.metrics.horiBearingX
        self.metrics.horiBearingY = glyph.metrics.horiBearingY
        self.metrics.horiAdvance = glyph.metrics.horiAdvance
        self.metrics.vertAdvance = glyph.metrics.vertAdvance


@functools.lru_cache(maxsize=1024, typed=True)
def get_char_glyph(cdpt: str, font_size: int, direction: int) -> Glyph:
    global FONT_SELECTION
    for i, face in enumerate(FONT_SELECTION):
        if face.get_char_index(cdpt) == 0 and i != len(FONT_SELECTION) - 1:
            continue
        if direction == 0:
            face.set_pixel_sizes(0, font_size)
        elif direction == 1:
            face.set_pixel_sizes(font_size, 0)
        face.load_char(cdpt)
        return Glyph(face.glyph)


def get_char_border(cdpt: str, font_size: int, direction: int):
    global FONT_SELECTION
    for i, face in enumerate(FONT_SELECTION):
        if face.get_char_index(cdpt) == 0 and i != len(FONT_SELECTION) - 1:
            continue
        if direction == 0:
            face.set_pixel_sizes(0, font_size)
        elif direction == 1:
            face.set_pixel_sizes(font_size, 0)
        face.load_char(cdpt, freetype.FT_LOAD_DEFAULT | freetype.FT_LOAD_NO_BITMAP)
        slot_border = face.glyph
        return slot_border.get_glyph()


def get_char_offset_x(font_size: int, cdpt: str):
    c, rot_degree = CJK_Compatibility_Forms_translate(cdpt, 0)
    glyph = get_char_glyph(c, font_size, 0)
    bitmap = glyph.bitmap
    if bitmap.rows * bitmap.width == 0 or len(bitmap.buffer) != bitmap.rows * bitmap.width:
        char_offset_x = glyph.advance.x >> 6
    else:
        char_offset_x = glyph.metrics.horiAdvance >> 6
    return char_offset_x


def get_string_width(font_size: int, text: str):
    return sum([get_char_offset_x(font_size, c) for c in text])


def calc_horizontal(font_size: int, text: str, max_width: int, max_height: int, language: str = 'en_US', hyphenate: bool = True) -> Tuple[List[str], List[int]]:
    max_width = max(max_width, 2 * font_size)
    whitespace_offset_x = get_char_offset_x(font_size, ' ')
    hyphen_offset_x = get_char_offset_x(font_size, '-')

    words = re.split(r'\s+', text)
    word_widths = [get_string_width(font_size, w) for w in words]

    while True:
        max_lines = max_height // font_size + 1
        expected_size = sum(word_widths) + max((len(word_widths) - 1) * whitespace_offset_x - (max_lines - 1) * hyphen_offset_x, 0)
        max_size = max_width * max_lines
        if max_size < expected_size:
            multiplier = np.sqrt(expected_size / max_size)
            max_width *= max(multiplier, 1.05)
            max_height *= multiplier
        else:
            break

    syllables = []
    hyphenator = select_hyphenator(language) if hyphenate else None
    for i, word in enumerate(words):
        new_syls = []
        if hyphenator and len(word) <= 100:
            try:
                new_syls = hyphenator.syllables(word)
            except Exception:
                new_syls = []
        if len(new_syls) == 0:
            new_syls = [word] if len(word) <= 3 else list(word)

        normalized_syls = []
        for syl in new_syls:
            syl_width = get_string_width(font_size, syl)
            if syl_width > max_width:
                normalized_syls.extend(list(syl))
            else:
                normalized_syls.append(syl)
        syllables.append(normalized_syls)

    line_words_list = []
    line_width_list = []
    hyphenation_idx_list = []
    line_words = []
    line_width = 0
    hyphenation_idx = 0

    def break_line():
        nonlocal line_words, line_width, hyphenation_idx
        line_words_list.append(line_words)
        line_width_list.append(line_width)
        hyphenation_idx_list.append(hyphenation_idx)
        line_words = []
        line_width = 0
        hyphenation_idx = 0

    i = 0
    while True:
        if i >= len(words):
            if line_width > 0:
                break_line()
            break
        current_width = whitespace_offset_x if line_width > 0 else 0
        if line_width + current_width + word_widths[i] <= max_width + hyphen_offset_x:
            line_words.append(i)
            line_width += current_width + word_widths[i]
            i += 1
        elif word_widths[i] > max_width:
            j = 0
            hyphenation_idx = 0
            while j < len(syllables[i]):
                syl = syllables[i][j]
                syl_width = get_string_width(font_size, syl)
                if line_width + current_width + syl_width <= max_width:
                    current_width += syl_width
                    j += 1
                    hyphenation_idx = j
                else:
                    if hyphenation_idx > 0:
                        line_words.append(i)
                        line_width += current_width
                    current_width = 0
                    break_line()
            line_words.append(i)
            line_width += current_width
            i += 1
        else:
            break_line()

    # assemble line_text_list (simplified - no hyphenation back-pass)
    line_text_list = []
    for i, line in enumerate(line_words_list):
        parts = []
        for j, word_idx in enumerate(line):
            syl_start = 0
            syl_end = len(syllables[word_idx])
            if i > 0 and j == 0 and line_words_list[i-1][-1] == word_idx:
                syl_start = hyphenation_idx_list[i-1]
            if i < len(line_words_list) - 1 and j == len(line) - 1 and line_words_list[i+1][0] == word_idx:
                syl_end = hyphenation_idx_list[i]
            parts.append(''.join(syllables[word_idx][syl_start:syl_end]))
            if j < len(line) - 1:
                parts.append(' ')
        line_text = ''.join(parts)
        line_width_list[i] = get_string_width(font_size, line_text)
        line_text_list.append(line_text)

    return line_text_list, line_width_list


def put_char_horizontal(font_size: int, cdpt: str, pen_l: Tuple[int, int], canvas_text: np.ndarray, canvas_border: np.ndarray, border_size: int):
    pen = list(pen_l)
    cdpt, rot_degree = CJK_Compatibility_Forms_translate(cdpt, 0)
    slot = get_char_glyph(cdpt, font_size, 0)
    bitmap = slot.bitmap

    if hasattr(slot, 'metrics') and hasattr(slot.metrics, 'horiAdvance') and slot.metrics.horiAdvance:
        char_offset_x = slot.metrics.horiAdvance >> 6
    elif hasattr(slot, 'advance') and slot.advance.x:
        char_offset_x = slot.advance.x >> 6
    elif bitmap.width > 0 and hasattr(slot, 'bitmap_left'):
        char_offset_x = slot.bitmap_left + bitmap.width
    else:
        char_offset_x = font_size // 2

    if bitmap.rows * bitmap.width == 0 or len(bitmap.buffer) != bitmap.rows * bitmap.width:
        return char_offset_x

    bitmap_char = np.array(bitmap.buffer, dtype=np.uint8).reshape((bitmap.rows, bitmap.width))
    char_place_x = pen[0] + slot.bitmap_left
    char_place_y = pen[1] - slot.bitmap_top

    paste_y_start = max(0, char_place_y)
    paste_x_start = max(0, char_place_x)
    paste_y_end = min(canvas_text.shape[0], char_place_y + bitmap.rows)
    paste_x_end = min(canvas_text.shape[1], char_place_x + bitmap.width)

    bsy = paste_y_start - char_place_y
    bsx = paste_x_start - char_place_x
    bey = bsy + (paste_y_end - paste_y_start)
    bex = bsx + (paste_x_end - paste_x_start)
    bitmap_char_slice = bitmap_char[bsy:bey, bsx:bex]

    if (bitmap_char_slice.size > 0 and
            bitmap_char_slice.shape == (paste_y_end - paste_y_start, paste_x_end - paste_x_start)):
        canvas_text[paste_y_start:paste_y_end, paste_x_start:paste_x_end] = bitmap_char_slice

    if border_size > 0:
        glyph_border = get_char_border(cdpt, font_size, 0)
        stroker = freetype.Stroker()
        stroke_radius = 64 * max(int(0.07 * font_size), 1)
        stroker.set(stroke_radius, freetype.FT_STROKER_LINEJOIN_ROUND, freetype.FT_STROKER_LINECAP_ROUND, 0)
        glyph_border.stroke(stroker, destroy=True)
        blyph = glyph_border.to_bitmap(freetype.FT_RENDER_MODE_NORMAL, freetype.Vector(0, 0), True)
        bitmap_b = blyph.bitmap

        border_bitmap_rows = bitmap_b.rows
        border_bitmap_width = bitmap_b.width

        if border_bitmap_rows * border_bitmap_width > 0 and len(bitmap_b.buffer) == border_bitmap_rows * border_bitmap_width:
            bitmap_border = np.array(bitmap_b.buffer, dtype=np.uint8).reshape((border_bitmap_rows, border_bitmap_width))

            char_center_on_canvas_x = char_place_x + bitmap.width / 2.0
            char_center_on_canvas_y = char_place_y + bitmap.rows / 2.0
            pen_border_x = int(round(char_center_on_canvas_x - border_bitmap_width / 2.0))
            pen_border_y = int(round(char_center_on_canvas_y - border_bitmap_rows / 2.0))

            pby_start = max(0, pen_border_y)
            pbx_start = max(0, pen_border_x)
            pby_end = min(canvas_border.shape[0], pen_border_y + border_bitmap_rows)
            pbx_end = min(canvas_border.shape[1], pen_border_x + border_bitmap_width)

            border_slice = bitmap_border[pby_start - pen_border_y:pby_end - pen_border_y,
                                         pbx_start - pen_border_x:pbx_end - pen_border_x]
            target_slice = canvas_border[pby_start:pby_end, pbx_start:pbx_end]
            if border_slice.size > 0 and target_slice.shape == border_slice.shape:
                canvas_border[pby_start:pby_end, pbx_start:pbx_end] = cv2.add(target_slice, border_slice)

    return char_offset_x


def select_hyphenator(lang: str):
    if not _HYPHEN_AVAILABLE:
        return None
    if _LANGCODES_AVAILABLE:
        lang = standardize_tag(lang)
    if lang not in HYPHENATOR_LANGUAGES:
        for avail_lang in reversed(HYPHENATOR_LANGUAGES):
            if avail_lang.startswith(lang):
                lang = avail_lang
                break
        else:
            return None
    try:
        return Hyphenator(lang)
    except Exception:
        return None


def put_text_horizontal(font_size: int, text: str, width: int, height: int, alignment: str,
                        reversed_direction: bool, fg: Tuple[int, int, int], bg: Tuple[int, int, int],
                        lang: str = 'en_US', hyphenate: bool = True, line_spacing: int = 0):
    text = compact_special_symbols(text)
    if not text:
        return
    bg_size = int(max(font_size * 0.07, 1)) if bg is not None else 0
    spacing_y = int(font_size * (line_spacing or 0.01))

    line_text_list, line_width_list = calc_horizontal(font_size, text, width, height, lang, hyphenate)

    canvas_w = max(line_width_list) + (font_size + bg_size) * 2
    canvas_h = font_size * len(line_width_list) + spacing_y * (len(line_width_list) - 1) + (font_size + bg_size) * 2
    canvas_text = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
    canvas_border = canvas_text.copy()

    pen_orig = [font_size + bg_size, font_size + bg_size]
    if reversed_direction:
        pen_orig[0] = canvas_w - bg_size - 10

    for line_text, line_width in zip(line_text_list, line_width_list):
        pen_line = pen_orig.copy()
        if alignment == 'center':
            pen_line[0] += (max(line_width_list) - line_width) // 2 * (-1 if reversed_direction else 1)
        elif alignment == 'right' and not reversed_direction:
            pen_line[0] += max(line_width_list) - line_width
        elif alignment == 'left' and reversed_direction:
            pen_line[0] -= max(line_width_list) - line_width
            pen_line[0] = max(line_width, pen_line[0])

        for c in line_text:
            if reversed_direction:
                cdpt, _ = CJK_Compatibility_Forms_translate(c, 0)
                glyph = get_char_glyph(cdpt, font_size, 0)
                offset_x = glyph.metrics.horiAdvance >> 6
                pen_line[0] -= offset_x
            offset_x = put_char_horizontal(font_size, c, pen_line, canvas_text, canvas_border, border_size=bg_size)
            if not reversed_direction:
                pen_line[0] += offset_x
        pen_orig[1] += spacing_y + font_size

    canvas_border = np.clip(canvas_border, 0, 255)
    line_box = add_color(canvas_text, fg, canvas_border, bg)
    x, y, w, h = cv2.boundingRect(canvas_border)
    return line_box[y:y+h, x:x+w]
