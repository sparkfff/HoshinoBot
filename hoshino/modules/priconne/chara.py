import asyncio
import ast
import os
import tempfile
import threading
from io import BytesIO

import pygtrie
from fuzzywuzzy import process
from PIL import Image

import hoshino
from hoshino import R, log, sucmd, util, aiorequests
from hoshino.typing import CommandSession

from . import _pcr_data

logger = log.new_logger('chara', hoshino.config.DEBUG)
UNKNOWN = 1000

_render_assets = {}


def parse_data_source(source):
    """Read literal roster constants without executing downloaded Python."""
    if len(source.encode('utf8')) > 2 * 1024 * 1024:
        raise ValueError('花名册超过 2 MiB')
    values = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue  # module documentation
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            raise ValueError('花名册只能包含常量赋值')
        name = node.targets[0].id
        if name.startswith('_') or name in values:
            raise ValueError('花名册含非法或重复常量')
        values[name] = ast.literal_eval(node.value)
    names = values.get('CHARA_NAME')
    if not isinstance(names, dict) or UNKNOWN not in names:
        raise ValueError('花名册缺少角色名称或未知角色')
    for idx, aliases in names.items():
        if type(idx) is not int or idx <= 0 or not isinstance(aliases, (list, tuple)) or not aliases:
            raise ValueError('角色编号或别称无效')
        if any(not isinstance(n, str) or not util.normalize_str(n).strip() for n in aliases):
            raise ValueError('角色别称不能为空')
    unavailable = values.get('UnavailableChara')
    if not isinstance(unavailable, (set, list, tuple)) or any(type(i) is not int for i in unavailable):
        raise ValueError('UnavailableChara 无效')
    profiles = values.get('CHARA_PROFILE')
    if not isinstance(profiles, dict) or any(type(i) is not int or not isinstance(p, dict) for i, p in profiles.items()):
        raise ValueError('CHARA_PROFILE 无效')
    if any(not isinstance(k, str) or not isinstance(v, str) for p in profiles.values() for k, v in p.items()):
        raise ValueError('CHARA_PROFILE 的资料必须为文本')
    return values


def _get_render_asset(name):
    """仅在第三方插件需要绘制星级/专武时加载素材。"""
    if name not in _render_assets:
        _render_assets[name] = R.img(f'priconne/gadget/{name}.png').open()
    return _render_assets[name]


class Roster:

    def __init__(self):
        self._roster = pygtrie.CharTrie()
        self.update()

    def update(self):
        with open(_pcr_data.__file__, encoding='utf8') as source:
            values = parse_data_source(source.read())
        trie, result = self.prepare(values)
        self.install(values, trie)
        return result

    @staticmethod
    def prepare(values):
        trie = pygtrie.CharTrie()
        result = {'success': 0, 'duplicate': 0}
        for idx, names in values['CHARA_NAME'].items():
            for n in names:
                n = util.normalize_str(n)
                if n not in trie:
                    trie[n] = idx
                    result['success'] += 1
                else:
                    result['duplicate'] += 1
                    logger.warning(f'priconne.chara.Roster: 出现重名{n}于id{idx}与id{trie[n]}')
        return trie, result

    def install(self, values, trie):
        # No awaits or user code between publishing module constants and the trie.
        _pcr_data.__dict__.update(values)
        self._roster = trie

    def get_id(self, name):
        name = util.normalize_str(name)
        return self._roster[name] if name in self._roster else UNKNOWN

    def guess_id(self, name):
        """@return: id, name, score"""
        name, score = process.extractOne(name, self._roster.keys(), processor=util.normalize_str)
        return self._roster[name], name, score

    def parse_team(self, namestr):
        """@return: List[ids], unknown_namestr"""
        namestr = util.normalize_str(namestr.strip())
        team = []
        unknown = []
        while namestr:
            item = self._roster.longest_prefix(namestr)
            if not item:
                unknown.append(namestr[0])
                namestr = namestr[1:].lstrip()
            else:
                team.append(item.value)
                namestr = namestr[len(item.key):].lstrip()
        return team, ''.join(unknown)


roster = Roster()


def name2id(name):
    return roster.get_id(name)


def fromid(id_, star=0, equip=0):
    return Chara(id_, star, equip)


def fromname(name, star=0, equip=0):
    id_ = name2id(name)
    return Chara(id_, star, equip)


def guess_id(name):
    """@return: id, name, score"""
    return roster.guess_id(name)


def is_npc(id_):
    if id_ in _pcr_data.UnavailableChara:
        return True
    else:
        return not (1000 < id_ < 1900)


async def gen_team_pic(team, size=64, star_slot_verbose=True):
    num = len(team)
    des = Image.new('RGBA', (num*size, size), (255, 255, 255, 255))
    for i, chara in enumerate(team):
        src = await chara.render_icon(size, star_slot_verbose)
        des.paste(src, (i * size, 0), src)
    return des


async def download_chara_icon(id_, star):
    url = f'https://redive.estertion.win/icon/unit/{id_}{star}1.webp'
    save_path = R.img(f'priconne/unit/icon_unit_{id_}{star}1.png').path
    logger.info(f'Downloading chara icon from {url}')
    rsp = None
    try:
        rsp = await aiorequests.get(url, stream=True, timeout=5)
        if 200 == rsp.status_code:
            body = await rsp.content
            def save():
                temporary = None
                try:
                    with Image.open(BytesIO(body)) as img:
                        os.makedirs(os.path.dirname(save_path), exist_ok=True)
                        with tempfile.NamedTemporaryFile(dir=os.path.dirname(save_path),
                                suffix='.tmp', delete=False) as output:
                            temporary = output.name
                            img.save(output, format='PNG')
                        os.replace(temporary, save_path)
                        temporary = None
                finally:
                    if temporary is not None:
                        os.unlink(temporary)
            await asyncio.to_thread(save)
            logger.info(f'Saved to {save_path}')
            return 0    # ok
        elif rsp.status_code == 404:
            return 2    # this star variant does not exist
        else:
            logger.error(f'Failed to download {url}. HTTP {rsp.status_code}')
            return 1        # error
    except Exception as e:
        logger.error(f'Failed to download {url}. {type(e)}')
        logger.exception(e)
        return 1        # error
    finally:
        if rsp is not None:
            rsp.raw_response.close()


class Chara:

    def __init__(self, id_, star=0, equip=0):
        self.id = id_
        self.star = star
        self.equip = equip

    @property
    def name(self):
        return _pcr_data.CHARA_NAME[self.id][0] if self.id in _pcr_data.CHARA_NAME else _pcr_data.CHARA_NAME[UNKNOWN][0]

    @property
    def is_npc(self) -> bool:
        return is_npc(self.id)

    @property
    def icon(self):
        import warnings
        warnings.warn(
            "`Chara.icon` is deprecated and will be removed in the next version. Use async method `Chara.get_icon()` instead.",
            DeprecationWarning
        )
        star = '3' if 1 <= self.star <= 5 else '6'
        res = R.img(f'priconne/unit/icon_unit_{self.id}{star}1.png')
        if not res.exist:
            res = R.img(f'priconne/unit/icon_unit_{self.id}31.png')
        if not res.exist:
            res = R.img(f'priconne/unit/icon_unit_{self.id}11.png')
        # This synchronous compatibility property only checks local resources.
        # Plugins that need downloads must await get_icon().
        if not res.exist:
            res = R.img(f'priconne/unit/icon_unit_{UNKNOWN}31.png')
        return res

    async def get_icon(self, star=0) -> R.ResImg:
        star = star or self.star
        star = 1 if 1 <= star < 3 else 3 if 3 <= star < 6 else 6
        res = R.img(f'priconne/unit/icon_unit_{self.id}{star}1.png')
        if not res.exist:
            res = R.img(f'priconne/unit/icon_unit_{self.id}31.png')
        if not res.exist:
            res = R.img(f'priconne/unit/icon_unit_{self.id}11.png')
        if not res.exist:
            await asyncio.gather(
                download_chara_icon(self.id, 6),
                download_chara_icon(self.id, 3),
                download_chara_icon(self.id, 1),
            )
            res = R.img(f'priconne/unit/icon_unit_{self.id}{star}1.png')
        if not res.exist:
            res = R.img(f'priconne/unit/icon_unit_{self.id}31.png')
        if not res.exist:
            res = R.img(f'priconne/unit/icon_unit_{self.id}11.png')
        if not res.exist:
            res = R.img(f'priconne/unit/icon_unit_{UNKNOWN}31.png')
        return res

    async def get_icon_cqcode(self, star=0):
        return (await self.get_icon(star)).cqcode

    async def render_icon(self, size, star_slot_verbose=True) -> Image:
        icon = await self.get_icon()
        pic = icon.open().convert('RGBA').resize((size, size), Image.Resampling.LANCZOS)

        l = size // 6
        star_lap = round(l * 0.15)
        margin_x = (size - 6*l) // 2
        margin_y = round(size * 0.05)
        if self.star:
            for i in range(5 if star_slot_verbose else min(self.star, 5)):
                a = i*(l-star_lap) + margin_x
                b = size - l - margin_y
                s = _get_render_asset('star' if self.star > i else 'star_disabled')
                s = s.resize((l, l), Image.Resampling.LANCZOS)
                pic.paste(s, (a, b, a+l, b+l), s)
            if 6 == self.star:
                a = 5*(l-star_lap) + margin_x
                b = size - l - margin_y
                s = _get_render_asset('star_pink')
                s = s.resize((l, l), Image.Resampling.LANCZOS)
                pic.paste(s, (a, b, a+l, b+l), s)
        if self.equip:
            l = round(l * 1.5)
            a = margin_x
            b = margin_x
            s = _get_render_asset('equip').resize((l, l), Image.Resampling.LANCZOS)
            pic.paste(s, (a, b, a+l, b+l), s)
        return pic


@sucmd('download-pcr-chara-icon', force_private=False, aliases=('下载角色头像'))
async def download_pcr_chara_icon(sess: CommandSession):
    '''
    覆盖更新1、3、6星头像
    '''
    try:
        ch = fromname(sess.current_arg_text.strip())
        assert ch.id != UNKNOWN, '未知角色名'
        await asyncio.gather(
            download_chara_icon(ch.id, 6),
            download_chara_icon(ch.id, 3),
            download_chara_icon(ch.id, 1),
        )
        msg = await ch.get_icon_cqcode(6) + \
            await ch.get_icon_cqcode(3) + \
            await ch.get_icon_cqcode(1)
        await sess.send(msg)
    except Exception as e:
        logger.exception(e)
        await sess.send(f'Error: {type(e)}')


@sucmd('download-star6-chara-icon', force_private=False, aliases=('下载六星头像', '更新六星头像'))
async def download_star6_chara_icon(sess: CommandSession):
    '''
    尝试下载缺失的六星头像，已有头像不会被覆盖
    '''
    await _download_library_command(sess, (6,))


_library_download_lock = threading.Lock()


def _icon_is_valid(id_, star):
    path = R.img(f'priconne/unit/icon_unit_{id_}{star}1.png').path
    try:
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, ValueError):
        return False


async def download_icon_library(stars=(1, 3, 6), progress=None):
    """Download missing roster icons with two workers; existing PNGs resume a run."""
    jobs = asyncio.Queue()
    for id_ in sorted(_pcr_data.CHARA_NAME):
        if not is_npc(id_):
            for star in stars:
                jobs.put_nowait((id_, star))
    total = jobs.qsize()
    counts = {'downloaded': 0, 'existing': 0, 'unavailable': 0, 'failed': 0}

    async def worker():
        while not jobs.empty():
            id_, star = jobs.get_nowait()
            try:
                if await asyncio.to_thread(_icon_is_valid, id_, star):
                    counts['existing'] += 1
                else:
                    status = await download_chara_icon(id_, star)
                    counts[{0: 'downloaded', 2: 'unavailable'}.get(status, 'failed')] += 1
                    await asyncio.sleep(0.5)
                completed = sum(counts.values())
                if progress and completed % 100 == 0:
                    await progress(completed, total)
            finally:
                jobs.task_done()

    tasks = [asyncio.create_task(worker()) for _ in range(2)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return counts


async def _download_library_command(sess, stars):
    if not _library_download_lock.acquire(blocking=False):
        await sess.send('头像库正在下载，请等待当前任务完成')
        return
    try:
        await sess.send('开始补全当前花名册头像，已有有效图片会跳过；可能需要数分钟。')
        async def progress(done, total):
            try:
                await sess.send(f'头像库进度：{done}/{total}')
            except Exception as exc:
                logger.warning('头像下载进度发送失败：%s', exc)
        counts = await download_icon_library(stars, progress)
        await sess.send(f"下载完成：新增{counts['downloaded']}，已有{counts['existing']}，"
                        f"源站无此星级{counts['unavailable']}，失败{counts['failed']}。"
                        '失败项可再次运行命令补全。')
        from .arena.old_main import _update_dic
        await _update_dic.__wrapped__(sess)
    finally:
        _library_download_lock.release()


@sucmd('download-pcr-icon-library', force_private=False,
       aliases=('下载完整头像库', '补全头像库'))
async def download_full_icon_library(sess: CommandSession):
    await _download_library_command(sess, (1, 3, 6))
