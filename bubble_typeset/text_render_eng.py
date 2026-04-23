import os
import sys
import cv2
import numpy as np
from PIL import Image
from typing import List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from text_render import get_char_glyph, put_char_horizontal, add_color
from ballon_extractor import extract_ballon_region

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
PUNSET_RIGHT_ENG = {'.', '?', '!', ':', ';', ')', '}', '"'}


# ------- minimal stand-in for utils.TextBlock -------
class SimpleTextBlock:
    """Minimal text region descriptor for render_textblock_list_eng."""

    def __init__(self, x: int, y: int, w: int, h: int,
                 translation: str,
                 font_size: int = 40,
                 angle: float = 0.0,
                 fg_color: Tuple[int, int, int] = (0, 0, 0),
                 stroke_color: Tuple[int, int, int] = (255, 255, 255)):
        self._x, self._y, self._w, self._h = x, y, w, h
        self.translation = translation
        self.font_size = font_size
        self.angle = angle
        self._fg_color = fg_color
        self._stroke_color = stroke_color
        # mutable fields set by renderer
        self.enlarge_ratio = 1
        self.enlarged_xyxy = self.xyxy.copy()

    @property
    def xyxy(self) -> np.ndarray:
        return np.array([self._x, self._y, self._x + self._w, self._y + self._h], dtype=np.int32)

    @property
    def xywh(self) -> np.ndarray:
        return np.array([self._x, self._y, self._w, self._h], dtype=np.int32)

    def get_font_colors(self):
        return self._fg_color, self._stroke_color
# ----------------------------------------------------


def _rect_distance(x1, y1, x1b, y1b, x2, y2, x2b, y2b):
    def dist(ax, ay, bx, by):
        return np.sqrt((ax - bx) ** 2 + (ay - by) ** 2)
    left = x2b < x1
    right = x1b < x2
    bottom = y2b < y1
    top = y1b < y2
    if top and left:
        return dist(x1, y1b, x2b, y2)
    elif left and bottom:
        return dist(x1, y1, x2b, y2b)
    elif bottom and right:
        return dist(x1b, y1, x2, y2b)
    elif right and top:
        return dist(x1b, y1b, x2, y2)
    elif left:
        return x1 - x2b
    elif right:
        return x2 - x1b
    elif bottom:
        return y1 - y2b
    elif top:
        return y2 - y1b
    else:
        return 0


class Textline:
    def __init__(self, text='', pos_x=0, pos_y=0, length=0.0, spacing=0):
        self.text = text
        self.pos_x = pos_x
        self.pos_y = pos_y
        self.length = int(length)
        self.num_words = 1 if text else 0
        self.spacing = 0
        self.add_spacing(spacing)

    def append_right(self, word, w_len, delimiter=''):
        self.text = self.text + delimiter + word
        if word:
            self.num_words += 1
        self.length += w_len

    def append_left(self, word, w_len, delimiter=''):
        self.text = word + delimiter + self.text
        if word:
            self.num_words += 1
        self.length += w_len

    def add_spacing(self, spacing):
        self.spacing = spacing
        self.pos_x -= spacing
        self.length += 2 * spacing

    def strip_spacing(self):
        self.length -= self.spacing * 2
        self.pos_x += self.spacing
        self.spacing = 0


def render_lines(textlines, canvas_h, canvas_w, font_size, stroke_width,
                 line_spacing=0.01, fg=(0, 0, 0), bg=(255, 255, 255)):
    bg_size = stroke_width
    spacing_y = int(font_size * (line_spacing or 0.01))

    canvas_w = max([l.length for l in textlines]) + (font_size + bg_size) * 2
    canvas_h = font_size * len(textlines) + spacing_y * (len(textlines) - 1) + (font_size + bg_size) * 2
    canvas_text = np.zeros((canvas_h, canvas_w), dtype=np.uint8)
    canvas_border = canvas_text.copy()

    pen_orig = [font_size + bg_size, font_size + bg_size]
    for line in textlines:
        pen_line = pen_orig.copy()
        pen_line[0] += line.pos_x
        for c in line.text:
            offset_x = put_char_horizontal(font_size, c, pen_line, canvas_text, canvas_border, border_size=bg_size)
            pen_line[0] += offset_x
        pen_orig[1] += spacing_y + font_size

    canvas_border = np.clip(canvas_border, 0, 255)
    line_box = add_color(canvas_text, fg, canvas_border, bg)
    x, y, width, height = cv2.boundingRect(canvas_border)
    return Image.fromarray(line_box[y:y+height, x:x+width])


def seg_eng(text: str) -> List[str]:
    text = text.strip().upper().replace('  ', ' ').replace(' .', '.').replace('\n', ' ')
    processed_text = ''
    text_len = len(text)
    for ii, c in enumerate(text):
        if c in PUNSET_RIGHT_ENG and ii < text_len - 1:
            next_c = text[ii + 1]
            if next_c.isalpha() or next_c.isnumeric():
                processed_text += c + ' '
            else:
                processed_text += c
        else:
            processed_text += c

    word_list = processed_text.split(' ')
    word_num = len(word_list)
    if word_num <= 1:
        return word_list

    words = []
    skip_next = False
    for ii, word in enumerate(word_list):
        if skip_next:
            skip_next = False
            continue
        if len(word) < 3:
            append_left, append_right = False, False
            len_word = len(word)
            len_next = len(word_list[ii + 1]) if ii < word_num - 1 else -1
            len_prev = len(words[-1]) if ii > 0 else -1
            cond_next = (len_word == 2 and len_next <= 4) or len_word == 1
            cond_prev = (len_word == 2 and len_prev <= 4) or len_word == 1
            if len_next > 0 and len_prev > 0:
                if len_next < len_prev:
                    append_right = cond_next
                else:
                    append_left = cond_prev
            elif len_next > 0:
                append_right = cond_next
            elif len_prev:
                append_left = cond_prev

            if append_left:
                words[-1] = words[-1] + ' ' + word
            elif append_right:
                words.append(word + ' ' + word_list[ii + 1])
                skip_next = True
            else:
                words.append(word)
            continue
        words.append(word)
    return words


def layout_lines_aligncenter(mask, words, word_lengths, delimiter_len, line_height,
                              spacing=0, delimiter=' ', max_central_width=np.inf,
                              word_break=False) -> List[Textline]:
    m = cv2.moments(mask)
    mask = 255 - mask
    centroid_y = int(m['m01'] / m['m00'])
    centroid_x = int(m['m10'] / m['m00'])

    num_words = len(words)
    wlst_left, wlst_right = [], []
    len_left, len_right = [], []
    sum_left, sum_right = 0, 0

    if num_words > 1:
        wl_array = np.array(word_lengths, dtype=np.float64)
        wl_cumsums = np.cumsum(wl_array)
        wl_cumsums = wl_cumsums - wl_cumsums[-1] / 2 - wl_array / 2
        central_index = int(np.argmin(np.abs(wl_cumsums)))
        if central_index > 0:
            wlst_left = words[:central_index]
            len_left = word_lengths[:central_index]
            sum_left = int(np.sum(len_left))
        if central_index < num_words - 1:
            wlst_right = words[central_index + 1:]
            len_right = word_lengths[central_index + 1:]
            sum_right = int(np.sum(len_right))
    else:
        central_index = 0

    pos_y = centroid_y - line_height // 2
    pos_x = centroid_x - word_lengths[central_index] // 2

    bh, bw = mask.shape[:2]
    central_line = Textline(words[central_index], pos_x, pos_y, word_lengths[central_index], spacing)
    line_bottom = pos_y + line_height

    while sum_left > 0 or sum_right > 0:
        left_valid, right_valid = False, False
        if sum_left > 0:
            new_len_l = central_line.length + len_left[-1] + delimiter_len
            new_x_l = centroid_x - new_len_l // 2
            new_r_l = new_x_l + new_len_l
            if new_x_l > 0 and new_r_l < bw:
                if mask[pos_y: line_bottom, new_x_l].sum() == 0 and mask[pos_y: line_bottom, new_r_l].sum() == 0:
                    left_valid = True
        if sum_right > 0:
            new_len_r = central_line.length + len_right[0] + delimiter_len
            new_x_r = centroid_x - new_len_r // 2
            new_r_r = new_x_r + new_len_r
            if new_x_r > 0 and new_r_r < bw:
                if mask[pos_y: line_bottom, new_x_r].sum() == 0 and mask[pos_y: line_bottom, new_r_r].sum() == 0:
                    right_valid = True

        insert_left = False
        if left_valid and right_valid:
            insert_left = sum_left > sum_right
        elif left_valid:
            insert_left = True
        elif not right_valid:
            break

        if insert_left:
            central_line.append_left(wlst_left.pop(-1), len_left[-1] + delimiter_len, delimiter)
            sum_left -= len_left.pop(-1)
            central_line.pos_x = new_x_l
        else:
            central_line.append_right(wlst_right.pop(0), len_right[0] + delimiter_len, delimiter)
            sum_right -= len_right.pop(0)
            central_line.pos_x = new_x_r
        if central_line.length > max_central_width:
            break

    central_line.strip_spacing()
    lines = [central_line]

    if sum_right > 0:
        w, wl = wlst_right.pop(0), len_right.pop(0)
        pos_x = centroid_x - wl // 2
        pos_y = centroid_y + line_height // 2
        line_bottom = pos_y + line_height
        line = Textline(w, pos_x, pos_y, wl, spacing)
        lines.append(line)
        sum_right -= wl
        while sum_right > 0:
            w, wl = wlst_right.pop(0), len_right.pop(0)
            sum_right -= wl
            new_len = line.length + wl + delimiter_len
            new_x = centroid_x - new_len // 2
            right_x = new_x + new_len
            line_valid = (new_x > 0 and right_x < bw and
                          mask[pos_y: line_bottom, new_x].sum() == 0 and
                          mask[pos_y: line_bottom, right_x].sum() == 0)
            if line_valid:
                line.append_right(w, wl + delimiter_len, delimiter)
                line.pos_x = new_x
                if new_len > max_central_width:
                    if sum_right > 0:
                        w, wl = wlst_right.pop(0), len_right.pop(0)
                        sum_right -= wl
                    else:
                        line.strip_spacing()
                        break
            if not line_valid:
                pos_x = centroid_x - wl // 2
                pos_y = line_bottom
                line_bottom += line_height
                line.strip_spacing()
                line = Textline(w, pos_x, pos_y, wl, spacing)
                lines.append(line)

    if sum_left > 0:
        w, wl = wlst_left.pop(-1), len_left.pop(-1)
        pos_x = centroid_x - wl // 2
        pos_y = centroid_y - line_height // 2 - line_height
        line_bottom = pos_y + line_height
        line = Textline(w, pos_x, pos_y, wl, spacing)
        lines.insert(0, line)
        sum_left -= wl
        while sum_left > 0:
            w, wl = wlst_left.pop(-1), len_left.pop(-1)
            sum_left -= wl
            new_len = line.length + wl + delimiter_len
            new_x = centroid_x - new_len // 2
            right_x = new_x + new_len
            line_valid = (new_x > 0 and right_x < bw and
                          mask[pos_y: line_bottom, new_x].sum() == 0 and
                          mask[pos_y: line_bottom, right_x].sum() == 0)
            if line_valid:
                line.append_left(w, wl + delimiter_len, delimiter)
                line.pos_x = new_x
                if new_len > max_central_width:
                    if sum_left > 0:
                        w, wl = wlst_left.pop(-1), len_left.pop(-1)
                        sum_left -= wl
                    else:
                        line.strip_spacing()
                        break
            if not line_valid:
                pos_x = centroid_x - wl // 2
                pos_y -= line_height
                line_bottom = pos_y + line_height
                line.strip_spacing()
                line = Textline(w, pos_x, pos_y, wl, spacing)
                lines.insert(0, line)

    return lines


def render_textblock_list_eng(
    img: np.ndarray,
    text_regions: List[SimpleTextBlock],
    font_color=(0, 0, 0),
    stroke_color=(255, 255, 255),
    delimiter: str = ' ',
    line_spacing: int = 0.01,
    stroke_width: float = 0.1,
    size_tol: float = 1.0,
    ballonarea_thresh: float = 2,
    downscale_constraint: float = 0.7,
    original_img: np.ndarray = None,
    disable_font_border: bool = False
) -> np.ndarray:

    def calculate_font_values(font_size, words):
        font_size = int(font_size)
        sw = int(font_size * stroke_width)
        line_height = int(font_size * 0.8)
        delimiter_glyph = get_char_glyph(delimiter, font_size, 0)
        delimiter_len = delimiter_glyph.advance.x >> 6
        base_length = -1
        word_lengths = []
        for word in words:
            word_length = 0
            for cdpt in word:
                glyph = get_char_glyph(cdpt, font_size, 0)
                word_length += glyph.metrics.horiAdvance >> 6
            word_lengths.append(word_length)
            if word_length > base_length:
                base_length = word_length
        return font_size, sw, line_height, delimiter_len, base_length, word_lengths

    img_pil = Image.fromarray(img)

    for region in text_regions:
        region.enlarge_ratio = 1
        region.enlarged_xyxy = region.xyxy.copy()

    def update_enlarged_xyxy(region):
        region.enlarged_xyxy = region.xyxy.copy()
        w_diff, h_diff = ((region.xywh[2:] * region.enlarge_ratio) - region.xywh[2:].astype(np.float64)) // 2
        region.enlarged_xyxy[0] -= w_diff
        region.enlarged_xyxy[2] += w_diff
        region.enlarged_xyxy[1] -= h_diff
        region.enlarged_xyxy[3] += h_diff

    for region in text_regions:
        if region.enlarge_ratio == 1:
            region.enlarge_ratio = min(max(region.xywh[2] / region.xywh[3], region.xywh[3] / region.xywh[2]) * 1.5, 3)
            update_enlarged_xyxy(region)

        for region2 in text_regions:
            if region is region2:
                continue
            if _rect_distance(*region.enlarged_xyxy, *region2.enlarged_xyxy) == 0:
                d = _rect_distance(*region.xyxy, *region2.xyxy)
                l1 = (region.xywh[2] + region.xywh[3]) / 2
                l2 = (region2.xywh[2] + region2.xywh[3]) / 2
                region.enlarge_ratio = d / (2 * l1) + 1
                region2.enlarge_ratio = d / (2 * l2) + 1
                update_enlarged_xyxy(region)
                update_enlarged_xyxy(region2)

    for region in text_regions:
        words = seg_eng(region.translation)
        if not words:
            continue

        font_size, sw, line_height, delimiter_len, base_length, word_lengths = calculate_font_values(region.font_size, words)

        ballon_mask, xyxy = extract_ballon_region(original_img, region.xywh, enlarge_ratio=region.enlarge_ratio)
        ballon_area = (ballon_mask > 0).sum()
        rotated, rx, ry = False, 0, 0

        if abs(region.angle) > 3:
            rotated = True
            region_angle_rad = np.deg2rad(region.angle)
            region_angle_sin = np.sin(region_angle_rad)
            region_angle_cos = np.cos(region_angle_rad)
            rotated_ballon_mask = Image.fromarray(ballon_mask).rotate(region.angle, expand=True)
            rotated_ballon_mask = np.array(rotated_ballon_mask)
            region.angle %= 360
            if region.angle > 0 and region.angle <= 90:
                ry = abs(ballon_mask.shape[1] * region_angle_sin)
            elif region.angle > 90 and region.angle <= 180:
                rx = abs(ballon_mask.shape[1] * region_angle_cos)
                ry = rotated_ballon_mask.shape[0]
            elif region.angle > 180 and region.angle <= 270:
                ry = abs(ballon_mask.shape[0] * region_angle_cos)
                rx = rotated_ballon_mask.shape[1]
            else:
                rx = abs(ballon_mask.shape[0] * region_angle_sin)
            ballon_mask = rotated_ballon_mask

        line_width = sum(word_lengths) + delimiter_len * (len(word_lengths) - 1)
        region_area = line_width * line_height + delimiter_len * (len(words) - 1) * line_height
        resize_ratio = 1

        region_x, region_y, region_w, region_h = cv2.boundingRect(cv2.findNonZero(ballon_mask))
        base_length_word = words[max(enumerate(word_lengths), key=lambda x: x[1])[0]]
        if len(base_length_word) == 0:
            continue

        lines_needed = len(region.translation) / len(base_length_word)
        lines_available = abs(xyxy[3] - xyxy[1]) // line_height + 1
        font_size_multiplier = max(min(region_w / (base_length + 2*sw), lines_available / lines_needed), downscale_constraint)
        if font_size_multiplier < 1:
            font_size = int(font_size * font_size_multiplier)
            font_size, sw, line_height, delimiter_len, base_length, word_lengths = calculate_font_values(font_size, words)

        textlines = layout_lines_aligncenter(ballon_mask, words, word_lengths, delimiter_len, line_height, delimiter=delimiter)

        line_cy = np.array([line.pos_y for line in textlines]).mean() + line_height / 2
        region_cy = region_y + region_h / 2
        y_offset = int(round(np.clip(region_cy - line_cy, -line_height, line_height)))

        lines_x1 = np.array([line.pos_x for line in textlines])
        lines_x2 = np.array([max(line.pos_x, 0) + line.length for line in textlines])
        canvas_x1 = lines_x1.min() - sw
        canvas_x2 = lines_x2.max() + sw
        canvas_y1 = textlines[0].pos_y - sw
        canvas_y2 = textlines[-1].pos_y + line_height + sw
        canvas_h = int(canvas_y2 - canvas_y1)
        canvas_w = int(canvas_x2 - canvas_x1)

        region_font_color, region_stroke_color = region.get_font_colors()
        for line in textlines:
            line.pos_x -= canvas_x1
            line.pos_y -= canvas_y1

        textlines_image = render_lines(textlines, canvas_h, canvas_w, font_size, sw, line_spacing,
                                       region_font_color, region_stroke_color)

        rel_cx = ((canvas_x1 + canvas_x2) / 2 - rx) / resize_ratio
        rel_cy = ((canvas_y1 + canvas_y2) / 2 - ry + y_offset) / resize_ratio

        if rotated:
            rcx = rel_cx * region_angle_cos - rel_cy * region_angle_sin
            rcy = rel_cx * region_angle_sin + rel_cy * region_angle_cos
            rel_cx = rcx
            rel_cy = rcy
            textlines_image = textlines_image.rotate(-region.angle, expand=True, resample=Image.BILINEAR)
            textlines_image = textlines_image.crop(textlines_image.getbbox())

        abs_cx = rel_cx + xyxy[0]
        abs_cy = rel_cy + xyxy[1]
        abs_x = int(abs_cx - textlines_image.width / 2)
        abs_y = int(abs_cy - textlines_image.height / 2)
        img_pil.paste(textlines_image, (abs_x, abs_y), mask=textlines_image)

    return np.array(img_pil)
