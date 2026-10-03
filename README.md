# HoshinoBot 精简版

本仓库是 [sparkfff/HoshinoBot](https://github.com/sparkfff/HoshinoBot)，基于 [Ice9Coffee/HoshinoBot](https://github.com/Ice9Coffee/HoshinoBot) 精简。保留公主连结角色别称查询（whois）、竞技场模块（arena）、原有机器人维护功能和 Hoshino / NoneBot 1 插件加载机制，其他内置业务功能已移除，可以按需添加第三方插件。

## 保留功能

| 用途 | 指令 | 权限与说明 |
| --- | --- | --- |
| 角色别称查询 | `谁是布丁`、`布丁是谁` | 群内查询角色名，支持模糊匹配；头像缺失时尝试下载，下载失败返回文字 |
| 竞技场查询 | `怎么拆` 后接五个角色名或阵容截图；可加 `b`、`台`、`日` 前缀 | 需要配置 `priconne.arena.AUTH_KEY`，截图识别需要本地头像库，查询结果图片需要绘图资源 |
| 重建识图头像库 | `竞技场更新卡池` | 超级用户；从本地角色头像重建识别缓存，不下载图片 |
| 帮助 | `帮助`、`帮助pcr查询`、`帮助通用` | 显示总帮助和已加载服务的帮助 |
| 服务列表 | `lssv`、`lssv -a`、`lssv -H` | 群管理员；分别查看普通、全部、隐藏服务 |
| 群内服务开关 | `启用 pcr-query`、`禁用 pcr-query` | 权限取决于服务的 `manage_priv`，默认需要群管理员 |
| 反馈 | `来杯咖啡 反馈内容` | 群内使用，每人每天一次，私聊发送给首位超级用户 |
| 更新角色别称 | `更新花名册`、`重载花名册` | 超级用户；从上游 LandosolRoster 下载数据并重新载入，不再定时更新 |
| 更新单个角色头像 | `下载角色头像 布丁` | 超级用户；覆盖下载该角色的 1、3、6 星头像 |
| 补全头像库 | `下载完整头像库`、`补全头像库` | 超级用户；补全当前花名册的 1、3、6 星头像，完成后自动重建识图卡池 |
| 补全六星头像 | `下载六星头像`、`更新六星头像` | 超级用户；仅下载缺失的六星头像 |
| 列表与状态 | `ls -g`、`ls -f`、`ls -b`、`ls -s pcr-query` | 超级用户私聊；查看群、好友、连接的 bot 或指定服务状态 |
| 跨群服务开关 | `启用 pcr-query 群号`、`禁用 pcr-query 群号` | 超级用户私聊；可在服务名后填写多个群号 |
| 广播 | `广播 内容`（别名 `bc`、`broadcast`） | 超级用户；向所有连接的 bot 所在群发送消息 |
| 退群 | `退群 群号`（别名 `quit`） | 超级用户私聊；可指定多个群号 |
| 到期通知 | `billing 群号 YYYY-MM-DD` | 超级用户私聊；发送通知，可填写多组群号和日期 |
| 查看 CQ 码 | `取码 消息` | 超级用户；显示消息的转义 CQ 码 |
| 清理客户端图片缓存 | `清理数据` | 超级用户私聊；需要 QQ 客户端支持 `clean_data_dir` 接口 |

同时保留入群邀请审核（仅自动接受超级用户的邀请）和被踢出群时通知首位超级用户的行为。角色数据及兼容接口仍位于 `hoshino.modules.priconne.chara`，方便后续插件复用。

## 部署

使用 Python 3.10 和独立虚拟环境。框架沿用 NoneBot 1，依赖版本见 `requirements.txt`；现有依赖已在 Python 3.10 下验证，安装时请保留这些版本约束。

Windows PowerShell：

```powershell
git clone https://github.com/sparkfff/HoshinoBot.git
cd HoshinoBot
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item -Recurse hoshino/config_example hoshino/config
```

Linux：

```bash
git clone https://github.com/sparkfff/HoshinoBot.git
cd HoshinoBot
python3.10 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp -r hoshino/config_example hoshino/config
```

首次部署时复制配置；已有 `hoshino/config` 时直接修改现有配置。编辑 `hoshino/config/__bot__.py`，填写 `SUPERUSERS`，并根据实际环境设置 `HOST`、`PORT`、`RES_DIR`、`RES_PROTOCOL`。默认只加载：

```python
MODULES_ON = {
    'botmanage',
    'priconne',
}
```

从 Python 3.9 升级时，先停止 bot，用 Python 3.10 重新创建虚拟环境并安装依赖，保留现有的 `hoshino/config` 和 `RES_DIR` 资源目录。

Windows 使用 `.\.venv\Scripts\python.exe run.py`，Linux 使用 `.venv/bin/python run.py` 启动。检查启动日志，确认模块加载成功。

机器人需要另行连接支持 OneBot v11 反向 WebSocket 的 QQ 客户端。客户端连接到 `ws://127.0.0.1:8080/ws/`（与默认 `HOST`、`PORT` 对应）；如修改地址或端口，客户端配置也需同步修改。QQ 客户端的安装、登录和具体配置请参考所选客户端的文档。

连接成功后，在群内发送 `帮助` 和 `谁是布丁` 验证。whois 不要求预先下载完整资源包；头像保存在 `RES_DIR` 下，目录须可读写。客户端与 bot 不在同一机器时，需要正确配置可访问的图片协议和资源地址。

需要自动安装识图头像时，超级用户先发送 `更新花名册`，成功后发送 `下载完整头像库`。按当前花名册补全可用角色的 1、3、6 星头像，使用两个下载任务并限制请求频率，完成后自动重建识图卡池。每处理 100 项报告一次进度；源站没有的星级会记为缺失，网络失败可重跑命令，已下载的有效 PNG 会被跳过。首次下载可能需要数分钟，头像保存在 `RES_DIR/img/priconne/unit/`。

源站返回 404 的头像记录在 `RES_DIR/avatar_missing.json`，缓存 7 天，重复补全时不会再次请求；网络失败不缓存。进度仅统计待下载项，已有有效头像和缓存缺失项直接跳过。缓存过期后会自动重新检查；角色新开放六星时可发送 `补全头像库 重试缺失` 立即重新检查，也可使用 `下载六星头像 重试缺失`。首次使用此优化仍需请求一次原先未记录的缺失项，随后补全会更快。

## 添加第三方插件

保留完整的 `hoshino.modules.priconne.arena` 路径及其查询、反馈和图片渲染接口，供其他插件复用。`hoshino/config/priconne.py` 中的 `arena.AUTH_KEY` 用于竞技场 API；渲染查询结果还需 `RES_DIR/img/priconne/gadget/` 下的点赞图标、角色星级/专武素材以及 `msyh.ttc` 字体。缺素材或字体时返回文字结果。查询接口导入时不加载这些图片。点赞/点踩仅保留接口，不注册命令；未实现的上传命令已移除。

### 复用截图识别

识图核心移植自 [watermellye/arena](https://github.com/watermellye/arena)，保留 `old_main.py` 的异步接口：

```python
from hoshino.modules.priconne.arena.old_main import getBox, getPos, getUnit

# image / avatar 均为 PIL.Image；需要在 async 函数内调用。
teams, preview = await getBox(image)  # getPos(image) 与此相同
uid_6, unit_id, name, score = await getUnit(avatar)
```

`teams` 是角色 ID 二维列表，例如 `[[1001, 1002, 1003, 1004, 1005]]`；继承原算法的从右往左识别顺序。`preview` 是含两张诊断图片的 CQ 消息字符串。`getUnit` 的 `uid_6` 含星级信息（如 `100131`），`unit_id` 为角色 ID（如 `1001`），分数沿用原算法。没有头像库时分别返回 `([], '')` 和 `(0, 0, 'Unknown', -1)`。

识图计算在工作线程执行，同一进程同时处理一张图片，繁忙时返回提示。远程截图限制为 8 MiB、1200 万像素，并限制并发下载；`getBox`、`getPos`、`getUnit` 同样检查像素上限，直接调用时可能抛出 `ValueError`（包括 `RecognitionBusy`）。新下载头像后运行 `竞技场更新卡池` 刷新进程缓存。

花名册更新只接受 Python 字面量常量，不执行远程代码；先校验并构建新索引，再原子替换数据文件。旧插件的同步 `Chara.icon` 只查询本地资源，需要下载时请改用 `await Chara.get_icon()`。

花名册下载遇到连接重置或超时，会在 GitHub Raw 与官方 GitHub API 之间回退，并最多尝试三轮。更新失败时保留旧数据。若仍提示网络错误，请在运行 bot 的机器上检查 GitHub 连接及代理的 `HTTP_PROXY` / `HTTPS_PROXY` 配置；重试无法替代可用的网络连接。

将头像 PNG 放入 `RES_DIR/img/priconne/unit/`，文件名为 `icon_unit_100131.png` 等六位编号格式。识别首次调用时从本地头像加载，补充图片后由超级用户执行 `竞技场更新卡池`；插件也可调用 `record.update_dic()`。兼容缓存 `dic.npy` 由本地建库生成并被 Git 忽略，识图模块直接读取 PNG，不在启动时读取 pickle、下载图库或加载战绩推荐缓存。头像诊断图使用本地图库，不需要星级/专武素材。

此算法通过边框定位和头像哈希匹配识别4–5人阵容，不是通用 OCR。`怎么拆` 与截图同一条消息发送时会识别后查询；当前竞技场后端只查询完整5人队伍，4人结果可供其他插件直接使用。未引入该分支的多队无冲突推荐或分开发送截图的会话机制。先用实际游戏截图验证识别效果，再用于自动操作。

选择兼容 **Hoshino / NoneBot 1** 的插件，按照插件说明将其放入 `hoshino/modules/<模块名>/`，然后把模块名加入 `hoshino/config/__bot__.py` 的 `MODULES_ON`。如插件需要配置文件，将其放入 `hoshino/config`；使用同一个虚拟环境安装插件声明的额外依赖，并准备所需资源或 API 配置。重启后检查日志，再通过 `lssv` 和 `启用 服务名` 管理各群的服务。

`MODULES_ON` 使用目录模块名，群内启用/禁用命令使用插件注册的 `Service` 名，两者可能不同。被移除的旧内置功能不会仅靠修改配置恢复，需要另行安装相应插件。

NoneBot 2 插件不能直接放入本框架使用，依赖被移除业务模块的 Hoshino 插件也需要适配。保留 `priconne.chara` 等基础接口不代表所有旧插件都兼容；请逐个安装并核对依赖。

## 来源与许可证

原项目由 Ice9Coffee 及 HoshinoBot 开发组、贡献者开发：[Ice9Coffee/HoshinoBot](https://github.com/Ice9Coffee/HoshinoBot)。角色别称数据来自 [Ice9Coffee/LandosolRoster](https://github.com/Ice9Coffee/LandosolRoster)。感谢上游作者与贡献者。

本 fork 继续使用 [GPL-3.0](LICENSE) 许可证。使用、修改和分发时请遵守许可证要求，保留原作者与许可证信息；本项目不提供质保。
