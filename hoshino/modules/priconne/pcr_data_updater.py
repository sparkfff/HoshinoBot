import os
import random
import tempfile

import hoshino
from hoshino import Service, aiorequests, priv, sucmd
from hoshino.config import SUPERUSERS
from hoshino.typing import CommandSession

from . import chara

sv = Service("pcr-data-updater", use_priv=priv.SU, manage_priv=priv.SU, visible=False)


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
        rsp = await aiorequests.get('https://raw.githubusercontent.com/Ice9Coffee/LandosolRoster/master/_pcr_data.py', timeout=300)
        rsp.raise_for_status()
        rsp = await rsp.text

        filename = os.path.join(os.path.dirname(__file__), '_pcr_data.py')
        result = install_roster_source(rsp, filename)

    except Exception as e:
        sv.logger.exception(e)
        await report_to_su(sess, f'Error: {e}', f'pcr_data定时更新时遇到错误：\n{e}')
        return

    result = f"角色别称导入成功 {result['success']}，重名 {result['duplicate']}"
    await report_to_su(sess, result, f'pcr_data定时更新：\n{result}')


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
