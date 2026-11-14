"""本地头像建库，移植自 watermellye/arena，GPL-3.0。

https://github.com/watermellye/arena/blob/master/record.py
"""
import os
from pathlib import Path
from collections import Counter
import re
import tempfile

import numpy as np
from PIL import Image
from hoshino import R


def load_best_attacks():
    """Rank the frequently used B-server attacks from validated cached results."""
    from .arena import load_cache_index, load_cached_results
    attacks = Counter()
    for key in load_cache_index():
        if key[-1] not in '12':
            continue
        for row in load_cached_results(key):
            team = tuple(sorted((unit['id'] // 100) * 100 + unit['star'] * 10
                                for unit in row['atk']))
            if len(team) == len({unit // 100 for unit in team}) == 5:
                attacks[team] += 1
    return [team for team, _ in attacks.most_common(200)]


def load_icon_data():
    """从 RES_DIR 的头像生成原版 uid_6 -> RGB array 字典，不读取 pickle。"""
    root = Path(R.img('priconne/unit/').path)
    if not root.is_dir():
        return {}
    result = {}
    for path in sorted(root.iterdir()):
        match = re.fullmatch(r'icon_unit_(\d{6})\.png', path.name)
        if not match:
            continue
        uid = int(match.group(1))
        if not 1000 <= uid // 100 < 1900:
            continue
        try:
            with Image.open(path) as image:
                result[uid] = np.array(image.convert('RGB').resize((128, 128)))
        except (OSError, ValueError):
            continue
    return result


def update_dic():
    """兼容原接口：重建 dic.npy 和进程中的识别缓存，不访问网络。"""
    from . import old_main
    data = load_icon_data()
    target = Path(__file__).with_name('dic.npy')
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.npy', delete=False) as fp:
        temporary = Path(fp.name)
        np.save(fp, data)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    old_main.data = data
    old_main.data_processed = None
    old_main._data_loaded = True
    return f'共收录{len(data)}个pcr头像进入识别库' if data else '未找到可用头像，请先安装角色头像资源'
