import re
from PIL import Image, ImageDraw, ImageFont

import hoshino
from hoshino import Service, R
from hoshino.typing import *
from hoshino.util import FreqLimiter, pic2b64, filt_message

from .. import chara

sv_help = '''
[怎么拆] 接防守队角色名 查询竞技场解法
[怎么拆] 接阵容截图 使用本地头像库识别后查询
[竞技场更新卡池] 超级用户重建本地头像识别库
'''.strip()
sv = Service('pcr-arena', help_=sv_help, bundle='pcr查询')

from . import arena

lmt = FreqLimiter(5)

aliases = ('怎么拆', '怎么解', '怎么打', '如何拆', '如何解', '如何打', 'jjc查询')
aliases_b = tuple('b' + a for a in aliases) + tuple('B' + a for a in aliases)
aliases_tw = tuple('台' + a for a in aliases)
aliases_jp = tuple('日' + a for a in aliases)

def _load_feedback_icons():
    """绘图时再加载资源，供只使用查询接口的插件独立导入。"""
    global thumb_up_i, thumb_up_a, thumb_down_i, thumb_down_a
    if 'thumb_down_a' in globals():
        return
    thumb_up_i = R.img('priconne/gadget/thumb-up-i.png').open().resize((16, 16), Image.Resampling.LANCZOS)
    thumb_up_a = R.img('priconne/gadget/thumb-up-a.png').open().resize((16, 16), Image.Resampling.LANCZOS)
    thumb_down_i = R.img('priconne/gadget/thumb-down-i.png').open().resize((16, 16), Image.Resampling.LANCZOS)
    thumb_down_a = R.img('priconne/gadget/thumb-down-a.png').open().resize((16, 16), Image.Resampling.LANCZOS)

@sv.on_prefix(aliases)
async def arena_query(bot, ev):
    await _arena_query(bot, ev, region=1)

@sv.on_prefix(aliases_b)
async def arena_query_b(bot, ev):
    await _arena_query(bot, ev, region=2)

@sv.on_prefix(aliases_tw)
async def arena_query_tw(bot, ev):
    await _arena_query(bot, ev, region=3)

@sv.on_prefix(aliases_jp)
async def arena_query_jp(bot, ev):
    await _arena_query(bot, ev, region=4)


async def render_atk_def_teams(entries, border_pix=5):
    _load_feedback_icons()
    n = len(entries)
    icon_size = 64
    im = Image.new('RGBA', (5 * icon_size + 100, n * (icon_size + border_pix) - border_pix), (255, 255, 255, 255))
    font = ImageFont.truetype('msyh.ttc', 16)
    draw = ImageDraw.Draw(im)
    for i, e in enumerate(entries):
        y1 = i * (icon_size + border_pix)
        y2 = y1 + icon_size
        for j, c in enumerate(e['atk']):
            icon = await c.render_icon(icon_size)
            x1 = j * icon_size
            x2 = x1 + icon_size
            im.paste(icon, (x1, y1, x2, y2), icon)
        thumb_up = thumb_up_a if e['user_like'] > 0 else thumb_up_i
        thumb_down = thumb_down_a if e['user_like'] < 0 else thumb_down_i
        x1 = 5 * icon_size + 5
        x2 = x1 + 16
        im.paste(thumb_up, (x1, y1+22, x2, y1+38), thumb_up)
        im.paste(thumb_down, (x1, y1+44, x2, y1+60), thumb_down)
        draw.text((x1, y1), e['qkey'], (0, 0, 0, 255), font)
        draw.text((x1+16, y1+20), f"{e['up']}+{e['my_up']}" if e['my_up'] else f"{e['up']}", (0, 0, 0, 255), font)
        draw.text((x1+16, y1+40), f"{e['down']}+{e['my_down']}" if e['my_down'] else f"{e['down']}", (0, 0, 0, 255), font)
    return im


async def _arena_query(bot, ev: CQEvent, region: int, _skip_limiter=False):

    arena.refresh_quick_key_dic()
    uid = ev.user_id

    if not _skip_limiter:
        if not lmt.check(uid):
            await bot.finish(ev, '您查询得过于频繁，请稍等片刻', at_sender=True)
        lmt.start_cd(uid)

    for segment in ev.message:
        if segment.type == 'image' and segment.data.get('url'):
            from .old_main import _QueryArenaImageAsync
            await _QueryArenaImageAsync(segment.data['url'], region, bot, ev)
            return

    # 处理输入数据
    defen = ev.message.extract_plain_text()
    defen = re.sub(r'[?？，,_]', '', defen)
    defen, unknown = chara.roster.parse_team(defen)

    if unknown:
        _, name, score = chara.guess_id(unknown)
        if score < 70 and not defen:
            return  # 忽略无关对话
        unknown = filt_message(unknown)
        msg = f'无法识别"{unknown}"' if score < 70 else f'无法识别"{unknown}" 您说的有{score}%可能是{name}'
        await bot.finish(ev, msg)
    if not defen:
        await bot.finish(ev, '查询请发送"怎么拆+防守队伍"，无需+号', at_sender=True)
    if len(defen) > 5:
        await bot.finish(ev, '编队不能多于5名角色', at_sender=True)
    if len(defen) < 5:
        await bot.finish(ev, '由于数据库限制，少于5名角色的检索条件请移步pcrdfans.com进行查询', at_sender=True)
    if len(defen) != len(set(defen)):
        await bot.finish(ev, '编队中含重复角色', at_sender=True)
    if any(chara.is_npc(i) for i in defen):
        await bot.finish(ev, '编队中含未实装角色', at_sender=True)
    if 1004 in defen:
        await bot.send(ev, '\n⚠️您正在查询普通版炸弹人\n※万圣版可用万圣炸弹人/瓜炸等别称', at_sender=True)

    # 执行查询
    sv.logger.info('Doing query...')
    res = None
    try:
        res = await arena.do_query(defen, uid, region)
    except hoshino.aiorequests.HTTPError as e:
        response = e.response
        code = response.get('code') if isinstance(response, dict) else None
        if code == 117 or code == -429:
            await bot.finish(ev, "高峰期服务器限流！请前往pcrdfans.com/battle")
        else:
            await bot.finish(ev, '竞技场服务暂时无法查询，请稍后重试或前往pcrdfans.com', at_sender=True)
    except (arena.ArenaResponseError, TypeError, ValueError) as e:
        sv.logger.warning(f'Arena returned unusable data: {e}')
        await bot.finish(ev, '竞技场服务返回的数据无法解析，请稍后重试或前往pcrdfans.com', at_sender=True)
    sv.logger.info('Got response!')

    # 处理查询结果
    if res is None:
        await bot.finish(ev, '数据库未返回数据，请再次尝试查询或前往pcrdfans.com', at_sender=True)
    if not len(res):
        await bot.finish(ev, '抱歉没有查询到解法\n※没有作业说明随便拆 发挥你的想象力～★\n作业上传请前往pcrdfans.com', at_sender=True)
    res = res[:min(6, len(res))]    # 限制显示数量，截断结果

    # 发送回复
    sv.logger.info('Arena generating picture...')
    try:
        teams = await render_atk_def_teams(res)
        teams = str(MessageSegment.image(pic2b64(teams)))
    except Exception as e:
        sv.logger.warning(f'Arena image rendering failed; using text: {e}')
        teams = '图片资源不可用，以下为文字结果：\n' + '\n'.join(
            f"{i}. " + ' '.join(
                f"{c.name}{c.star if c.star else ''}{'专' if c.equip else ''}"
                for c in entry['atk']) + f"（赞{entry['up']}，踩{entry['down']}）"
            for i, entry in enumerate(res, 1))
    sv.logger.info('Arena picture ready!')
    defen = [ chara.fromid(x).name for x in defen ]
    defen = f"防守方【{' '.join(defen)}】"
    at = str(MessageSegment.at(ev.user_id))

    msg = [
        defen,
        f'已为骑士{at}查询到以下进攻方案：',
        teams,
    ]
    if region == 1:
        msg.append('※使用"b怎么拆"或"台怎么拆"可按服过滤')
    msg.append('Support by pcrdfans_com')

    sv.logger.debug('Arena sending result...')
    await bot.send(ev, '\n'.join(msg))
    sv.logger.debug('Arena result sent!')

# @sv.on_prefix('点赞')
async def arena_like(bot, ev):
    await _arena_feedback(bot, ev, 1)


# @sv.on_prefix('点踩')
async def arena_dislike(bot, ev):
    await _arena_feedback(bot, ev, -1)


rex_qkey = re.compile(r'^[0-9a-zA-Z]{5}$')
async def _arena_feedback(bot, ev: CQEvent, action: int):
    action_tip = '赞' if action > 0 else '踩'
    qkey = ev.message.extract_plain_text().strip()
    if not qkey:
        await bot.finish(ev, f'请发送"点{action_tip}+作业id"，如"点{action_tip}ABCDE"，不分大小写', at_sender=True)
    if not rex_qkey.match(qkey):
        await bot.finish(ev, f'您要点{action_tip}的作业id不合法', at_sender=True)
    try:
        await arena.do_like(qkey, ev.user_id, action)
    except KeyError:
        await bot.finish(ev, '无法找到作业id！您只能评价您最近查询过的作业', at_sender=True)
    except OSError as e:
        sv.logger.warning(f'Arena feedback could not be saved: {e}')
        await bot.finish(ev, '反馈暂时无法保存，请稍后重试', at_sender=True)
    await bot.send(ev, '感谢您的反馈！', at_sender=True)


from . import old_main
