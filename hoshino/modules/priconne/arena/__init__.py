from hoshino import CanceledException, Service
from hoshino.util import FreqLimiter

from .qq_context_requests import remember, pop_request, request_key, gs_seconds_to_wait

sv_help = '''
[怎么拆/bjjc/tjjc/rjjc] 接角色名或截图查询竞技场解法，可识别多队
[bjjc/tjjc/rjjc] 可先发命令，再发送截图
[竞技场更新卡池] 超级用户重建本地头像识别库
完整五人防守可补充 ONNX 模型候选并尝试 MZQ 模拟
源码：https://github.com/Lanly109/arena
'''.strip()
sv = Service('pcr-arena', help_=sv_help, bundle='pcr查询')

from . import arena, old_main
from .old_main import render_atk_def_teams

lmt = FreqLimiter(5)
aliases = ('怎么拆', '怎么解', '怎么打', '如何拆', '如何解', '如何打', 'jjc查询')


@sv.on_prefix(aliases)
async def arena_query(bot, ev):
    await _arena_query(bot, ev, 1)


@sv.on_prefix('bjjc', *(p + a for p in ('b', 'B') for a in aliases))
async def arena_query_b(bot, ev):
    await _arena_query(bot, ev, 2)


@sv.on_prefix('tjjc', *(p + a for p in ('台', 't', 'T') for a in aliases))
async def arena_query_tw(bot, ev):
    await _arena_query(bot, ev, 3)


@sv.on_prefix('rjjc', *(p + a for p in ('日', 'r', 'R') for a in aliases))
async def arena_query_jp(bot, ev):
    await _arena_query(bot, ev, 4)


def image_url(message):
    return next((seg.data['url'].replace('&amp;', '&').split(',file_size=')[0]
                 for seg in message if seg.type == 'image' and seg.data.get('url')), None)


async def _arena_query(bot, ev, region, _skip_limiter=False):
    if not _skip_limiter:
        if not lmt.check(ev.user_id):
            await bot.finish(ev, '您查询得过于频繁，请稍等片刻', at_sender=True)
        lmt.start_cd(ev.user_id)
    url = image_url(ev.message)
    content = ev.message.extract_plain_text().strip()
    key = request_key(ev)
    pop_request(key)
    if url:
        await old_main._QueryArenaImageAsync(url, region, bot, ev)
    elif content:
        await old_main._QueryArenaTextAsync(content, region, bot, ev)
    elif region == 1:
        await bot.send(ev, sv.help)
    else:
        remember(key, region)
        await bot.send(ev, f'请在 {gs_seconds_to_wait} 秒内发送防守队伍截图')


async def pending_screenshot(bot, ev):
    url = image_url(ev.message)
    # A screenshot with its own text command is handled by the prefix trigger.
    if not url or ev.message.extract_plain_text().strip():
        return
    region = pop_request(request_key(ev))
    if region is not None:
        try:
            await old_main._QueryArenaImageAsync(url, region, bot, ev)
        except CanceledException:
            pass


sv.on_message('group')(pending_screenshot)
sv.on_message('private')(pending_screenshot)
