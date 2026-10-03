from hoshino import Service, priv
from hoshino.typing import CQEvent

sv = Service('_help_', manage_priv=priv.SUPERUSER, visible=False)

TOP_MANUAL = '''
====================
= HoshinoBot使用说明 =
====================
发送[]内的内容触发

====== 角色查询 ======
[谁是布丁] 或 [布丁是谁]
查询角色名与头像，支持别称
头像不可用时返回文字
[帮助pcr查询] 查看查询说明

====== 竞技场查询 ======
[怎么拆 五个角色名或阵容截图]
[b怎么拆][台怎么拆][日怎么拆]
需配置API密钥及绘图资源
[竞技场更新卡池] 重建本地头像库(超级用户)

====== 群内维护 ======
[来杯咖啡 反馈内容] 联系维护组
[帮助通用] 查看维护服务说明
群管理员可使用：
[lssv] 查看服务开关
[lssv -a] 包含隐藏服务
[启用 pcr-query][禁用 pcr-query]
其他插件请使用其注册的服务名

====== 超级用户 ======
[更新花名册] 手动更新角色别称
[下载角色头像 布丁]
[下载完整头像库] 补全头像并重建识图卡池
[下载六星头像] 补全缺失头像
[广播 内容] 向所有群广播
[取码 消息] 查看CQ码
以下指令限私聊：
[ls -g][ls -f][ls -b]
[ls -s pcr-query] 服务状态
[启用 pcr-query 群号]
[禁用 pcr-query 群号]
[退群 群号]
[billing 群号 YYYY-MM-DD]
[清理数据] 清理客户端图片缓存

====== 第三方插件 ======
安装后可发送 [帮助服务名]
或 [帮助分类名] 查看插件说明

原项目：github.com/Ice9Coffee/HoshinoBot
本fork：github.com/sparkfff/HoshinoBot
'''.strip()
# 魔改请保留 github.com/Ice9Coffee/HoshinoBot 项目地址


def gen_service_manual(service: Service, gid: int):
    spit_line = '=' * max(0, 18 - len(service.name))
    manual = [f"|{'○' if service.check_enabled(gid) else '×'}| {service.name} {spit_line}"]
    if service.help:
        manual.append(service.help)
    return '\n'.join(manual)


def gen_bundle_manual(bundle_name, service_list, gid):
    manual = [bundle_name]
    service_list = sorted(service_list, key=lambda s: s.name)
    for s in service_list:
        if s.visible:
            manual.append(gen_service_manual(s, gid))
    return '\n'.join(manual)


@sv.on_prefix('help', '帮助')
@sv.on_suffix('help', '帮助')
async def send_help(bot, ev: CQEvent):
    gid = ev.group_id
    arg = ev.message.extract_plain_text().strip()
    bundles = Service.get_bundles()
    services = Service.get_loaded_services()
    if not arg:
        await bot.send(ev, TOP_MANUAL)
    elif arg in bundles:
        msg = gen_bundle_manual(arg, bundles[arg], gid)
        await bot.send(ev, msg)
    elif arg in services:
        s = services[arg]
        msg = gen_service_manual(s, gid)
        await bot.send(ev, msg)
    # else: ignore
