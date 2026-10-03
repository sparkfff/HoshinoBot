import os
import asyncio
import base64
import random
import tempfile

import hoshino
from hoshino import Service, aiorequests, priv, sucmd
from hoshino.config import SUPERUSERS
from hoshino.typing import CommandSession

from . import chara

sv = Service("pcr-data-updater", use_priv=priv.SU, manage_priv=priv.SU, visible=False)
ROSTER_RAW_URL = 'https://raw.githubusercontent.com/Ice9Coffee/LandosolRoster/master/_pcr_data.py'
ROSTER_API_URL = 'https://api.github.com/repos/Ice9Coffee/LandosolRoster/contents/_pcr_data.py?ref=master'


async def fetch_roster_source():
    """Retry transient failures via two official GitHub endpoints, never mirrors."""
    last_error = None
    for attempt in range(3):
        for url in (ROSTER_RAW_URL, ROSTER_API_URL):
            response = None
            try:
                response = await aiorequests.get(url, timeout=(5, 15))
                response.raise_for_status()
                if url == ROSTER_RAW_URL:
                    source = await response.text
                else:
                    payload = await response.json()
                    if not isinstance(payload, dict) or payload.get('encoding') != 'base64' \
                            or not isinstance(payload.get('content'), str):
                        raise ValueError('GitHub API 返回的花名册格式不正确')
                    content = ''.join(payload['content'].split())
                    source = base64.b64decode(content, validate=True).decode('utf8')
                # Validate before returning: a proxy HTML page is not a roster.
                chara.parse_data_source(source)
                return source
            except aiorequests.RequestException as exc:
                last_error = exc
                sv.logger.warning('花名册下载尝试 %d 失败 (%s): %s', attempt + 1, url, type(exc).__name__)
            finally:
                if response is not None:
                    response.raw_response.close()
        if attempt < 2:
            await asyncio.sleep(attempt + 1)
    raise last_error


async def report_to_su(sess, msg_with_sess, msg_wo_sess):
    if sess:
        await sess.send(msg_with_sess)
    else:
        bot = hoshino.get_bot()
        sid = bot.get_self_ids()
        if len(sid) > 0:
            sid = random.choice(sid)
            await bot.send_private_msg(self_id=sid, user_id=SUPERUSERS[0], message=msg_wo_sess)


async def pull_chara(sess: CommandSession = None):
    try:
        source = await fetch_roster_source()

        filename = os.path.join(os.path.dirname(__file__), '_pcr_data.py')
        result = install_roster_source(source, filename)

    except aiorequests.RequestException as exc:
        sv.logger.warning('花名册下载失败，保留旧数据：%s', type(exc).__name__)
        message = 'GitHub 花名册下载失败，已重试且保留旧花名册。请检查运行 bot 的网络或代理设置，稍后再试。'
        await report_to_su(sess, message, message)
        return
    except Exception as e:
        sv.logger.exception(e)
        message = '花名册校验或保存失败，已保留旧数据，请查看日志确认原因。'
        await report_to_su(sess, message, message)
        return

    result = f"角色别称导入成功 {result['success']}，重名 {result['duplicate']}"
    await report_to_su(sess, result, f'pcr_data手动更新：\n{result}')


sucmd('update-pcr-chara', force_private=False, aliases=('重载花名册', '更新花名册'))(pull_chara)


def install_roster_source(source, filename):
    """Validate and prepare first; failed writes leave the old disk and trie intact."""
    values = chara.parse_data_source(source)
    trie, result = chara.roster.prepare(values)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', newline='\n',
                dir=os.path.dirname(os.path.abspath(filename)), suffix='.tmp', delete=False) as output:
            temporary = output.name
            output.write(source)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, filename)
        temporary = None
        chara.roster.install(values, trie)
    finally:
        if temporary is not None:
            os.unlink(temporary)
    return result
