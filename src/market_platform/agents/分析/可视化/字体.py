from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from matplotlib import font_manager
from matplotlib.font_manager import FontProperties

logger = logging.getLogger(__name__)

# 常见 Linux 中文字体路径（按优先级）
_CJK_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]


@lru_cache(maxsize=1)
def get_cjk_font() -> FontProperties | None:
    """加载可用中文字体，返回 FontProperties。"""
    for path_str in _CJK_FONT_CANDIDATES:
        path = Path(path_str)
        if not path.exists():
            continue
        try:
            font_manager.fontManager.addfont(str(path))
            prop = FontProperties(fname=str(path))
            logger.debug("Using CJK font: %s (%s)", path, prop.get_name())
            return prop
        except Exception as exc:  # noqa: BLE001
            logger.debug("Skip font %s: %s", path, exc)
    logger.warning("No CJK font found; Chinese labels may not render correctly")
    return None


def setup_matplotlib_cjk() -> FontProperties | None:
    """配置 matplotlib 全局字体，避免中文乱码。"""
    import matplotlib.pyplot as plt

    prop = get_cjk_font()
    if prop is None:
        return None

    family = prop.get_name()
    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = [family, "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return prop
