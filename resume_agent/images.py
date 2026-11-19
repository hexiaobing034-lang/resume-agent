# -*- coding: utf-8 -*-
"""图片嗅探：不装 Pillow 也能拿到格式和尺寸，防止把任意文件塞进 assets。"""
import struct
from typing import Tuple

PNG_SIG = b"\x89PNG\r\n\x1a\n"
JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def sniff(data: bytes) -> Tuple[str, int, int]:
    """返回 (扩展名, 宽, 高)；不是受支持的图片就抛 ValueError。"""
    if len(data) < 12:
        raise ValueError("图片文件太小，读不出内容。")

    if data[:8] == PNG_SIG and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return ".png", int(width), int(height)

    if data[:2] == b"\xff\xd8":
        index = 2
        size = len(data)
        while index + 9 < size:
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            if index + 4 > size:
                break
            length = struct.unpack(">H", data[index + 2:index + 4])[0]
            if marker in JPEG_SOF:
                height, width = struct.unpack(">HH", data[index + 5:index + 9])
                return ".jpg", int(width), int(height)
            index += 2 + length
        raise ValueError("这个 JPG 读不出版本信息，换一张试试。")

    if data[:6] in (b"GIF87a", b"GIF89a"):
        width, height = struct.unpack("<HH", data[6:10])
        return ".gif", int(width), int(height)

    raise ValueError("只支持 PNG / JPG / GIF 图片（证照建议用 JPG）。")
