"""PCR 头像/阵容识别基础，兼容 watermellye 的 old_main 导入路径。

识图算法移植自 watermellye/arena：
https://github.com/watermellye/arena/blob/master/old_main.py
沿用 HoshinoBot 的 GPL-3.0 许可证，查询逻辑采用 Lanly109/arena。
"""
import base64
import asyncio
import re
from functools import wraps
import threading
import time
from io import BytesIO
from os.path import dirname, join

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageColor
import requests

from hoshino import sucmd
from hoshino.util import filt_message, pic2b64
from hoshino.typing import CQEvent, HoshinoBot, MessageSegment
from .. import chara
from . import sv
from .record import load_icon_data, update_dic, load_best_attacks
from .query_provider import query_candidates, select_results
from .result_image import render_result_card, result_text

curpath = dirname(__file__)
dataDir = join(curpath, 'dic.npy')
data = {}
data_processed = None
_data_loaded = False
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
_recognition_lock = threading.Lock()
_download_slots = threading.BoundedSemaphore(4)
_worker_state = threading.local()


class RecognitionBusy(ValueError):
    pass


def _check_image(image):
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise ValueError('截图超过1200万像素，请裁剪阵容区域后重试')


def _in_worker(func):
    """Keep public async APIs while running the complete CPU task off-loop."""
    @wraps(func)
    async def wrapper(image):
        _check_image(image)
        if getattr(_worker_state, 'active', False):
            return await func(image)

        return await _run_recognition(func, image)
    return wrapper


async def _run_recognition(func, *args):
    if not _recognition_lock.acquire(blocking=False):
        raise RecognitionBusy('识图正在处理其他图片，请稍后重试')

    def run():
        try:
            _worker_state.active = True
            return asyncio.run(func(*args))
        finally:
            _worker_state.active = False
            _recognition_lock.release()

    task = asyncio.create_task(asyncio.to_thread(run))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Do not let a cancelled caller close the image while its worker uses it.
        await task
        raise


async def getBox(img):
    return await getPos(img)


async def cut_image(image, hash_size=16):
    '''
    将图像缩小成(16+1)*16并转化成灰度图
    :param image: PIL.Image
    :return list[int]
    '''

    image1 = image.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS).convert('L')
    pixel = np.asarray(image1).ravel().tolist()
    return pixel


async def trans_hash(lists):
    '''
    比较列表中相邻元素大小
    :param lists: list[int]
    :return list[bool]
    '''
    return [1 if lists[index - 1] > val else 0 for index, val in enumerate(lists)][1:]


async def difference_value(image_lists):
    # 获得图像差异值并获得指纹
    assert len(image_lists) == 17 * 16, "size error"
    m, n = 0, 17
    hash_list = []
    for i in range(0, 16):
        slc = slice(m, n)
        image_slc = image_lists[slc]
        hash_list.append(await trans_hash(image_slc))
        m += 17
        n += 17
    return hash_list


async def get_hash_arr(image):
    return np.array(await difference_value(await cut_image(image)))


async def calc_distance_arr(arr1, arr2):
    return int(np.count_nonzero(np.asarray(arr1) != np.asarray(arr2)))


async def calc_distance_img(image1, image2):
    return await calc_distance_arr(await get_hash_arr(image1), await get_hash_arr(image2))


async def process_data():
    global data, data_processed, _data_loaded
    if not _data_loaded:
        if not data:
            data = load_icon_data()
        _data_loaded = True
    data_processed = {}
    for uid in data:
        data_processed[uid] = await get_hash_arr(Image.fromarray(data[uid][25:96, 8:97, :]))


async def cutting(img, mode):
    '''
    :param img: 传入的待识别的原图片 PIL格式
    :param mode: mode=1：返回图片中最大的长方形区域，以及该区域的定位点 [PIL.Image, [x, y, w, h]] ; mode=2: 返回两个列表，列表中每个元素为正方形区域在原图的[x, y, w, h]。第一个列表为聚类结果，第二个列表为排除结果。若无正方形区域，返回[], []。
    '''
    im_grey = img.convert('L')
    totArea = (im_grey.size)[0] * (im_grey.size)[1]
    im_grey = im_grey.point(lambda x: 255 if x > 210 else 0)  # 没有用自带的二值化。考虑修改，使用更合适的函数。
    thresh = np.array(im_grey)

    # cv2.findContours. opencv3版本会返回3个值，opencv2和4只返回后两个值
    #img2, contours, hier = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contours, hier = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)  # 获取轮廓
    img2 = thresh

    lis = []
    icon = []  # 每个元素为：[边长，[矩阵左上点坐标，矩阵宽高]]
    # icon_area = {}  # key为边长 value为个数
    for i in range(len(contours)):
        area = cv2.contourArea(contours[i])  # 计算contour包围的像素点个数
        lis.append(area)
        if area > 500:
            if mode == 2:
                x, y, w, h = cv2.boundingRect(contours[i])  # 获取contour的aabb包围盒
                if w / h > 0.95 and w / h < 1.05:  # 近似正方形
                    are = (w + h) // 2
                    areaRatio = are * are / totArea * 100  # 获取该范围占整个输入图像的占比
                    # print(f"{areaRatio:2f}%")
                    if areaRatio >= 0.5:  # 过滤占比小于0.5%的正方形（可能为文字）
                        icon.append([are, [x, y, w, h]])
                        # icon_area[are] = icon_area.setdefault(are, 0) + 1
    # print()
    if mode == 1:
        i = lis.index(max(lis))
        x, y, w, h = cv2.boundingRect(contours[i])
        #cv2.rectangle(img2, (x, y), (x + w, y + h), (153, 153, 0), 5)
        img3 = img2[y + 2:y + h - 2, x + 2:x + w - 2]
        img4 = Image.fromarray(img3)
        return img4, [x, y, w, h]
    if mode == 2:
        if len(icon) == 0:
            return [], []

        # 对边长作简易聚类分析
        kinds = {}  # 边长: [[],[],[],...,[]]
        for i in icon:
            sidelen = i[0]
            category = -1
            for kind in kinds:
                ratio = sidelen / kind
                if ratio > 0.9 and ratio < 1.1:  # 将边长相差10%以内作为一类
                    category = kind
                    kinds[kind].append(i[1])
                    break
            if category == -1:
                kinds[sidelen] = [i[1]]

        def clusterWeight(x):
            sidelen = x[0]
            val = len(x[1])
            if val == 5:  # 该类有5个元素，优先返回。第二关键字为边长（从大到小）。
                return 5000000 + sidelen
            if val % 5 == 0:  # 该类元素个数为5的倍数，次优先返回。
                return 1000000 + sidelen
            return val * 10000 + sidelen

        kinds = sorted(kinds.items(), key=clusterWeight, reverse=True)
        kind = kinds[0]  # (边长, [[],[]])
        if len(kind[1]) % 5 == 0:  # 存在某一类，其元素个数为5的倍数，返回该类
            otherborder = []
            for x in range(1, len(kinds)):
                otherborder += kinds[x][1]
            return kind[1], otherborder
        else:
            return [x[1] for x in icon], []


async def cut(img, border):
    '''
    :param img: 待裁剪图片 PIL.Image
    :param border: 裁剪范围 [x, y, w, h]
    :return 裁剪后图像 PIL.Image
    '''
    x, y, w, h = border
    img = np.array(img)
    img = img[y + 2:y + h - 2, x + 2:x + w - 2]
    img = Image.fromarray(img)
    return img


@_in_worker
async def getPos(img: Image):
    '''
    :param img: 待识别图片 PIL.Image
    :return 识别出的阵容的坐标, 识别出的阵容角色字符串 [[第1队第1个角色(int), 1-2, ..., 1-5], [2-1, ..., 2-5], [3-1, ..., 3-5]], str；若识别失败，返回[], ""
    '''
    if data_processed is None:
        await process_data()
    if not data_processed:
        return [], ''
    img = img.convert("RGBA")
    im_grey = img

    actual_img = img
    actual_x = 0
    actual_y = 0

    nowcolor = 0
    outpImg = Image.new(mode="RGBA", size=img.size, color=ImageColor.getrgb(f'rgb({nowcolor},{nowcolor},{nowcolor})'))
    outpImgText = Image.new("RGBA", img.size, (0, 0, 0, 0))
    outpImgTextDraw = ImageDraw.Draw(outpImgText)

    cnt = 0
    while cnt <= 6:
        bo = False
        cnt += 1
        border, otherborder = await cutting(im_grey, 2)  # 获取当前图像中的正方形区域
        if len(border) == 0:  # 如果莫得 说明需要翻转图像的黑白
            bo = True
        else:
            # print(f'cnt={cnt} border={border}') # test
            # 将正方形区域按照行列分组
            def highlight(rec, color="red"):  # 识别成功red 被其它代码排除的识别项blue 超过五列未识别的green 识别不出角色的black 识别出是100031的yellow
                x, y, w, h = rec
                cropped = img.crop([x + 2, y + 2, x + w - 2, y + h - 2])
                outpImgText.paste(cropped, (actual_x + x + 2, actual_y + y + 2, actual_x + x + w - 2, actual_y + y + h - 2))
                outpImgTextDraw.rectangle((actual_x + x, actual_y + y, actual_x + x + w, actual_y + y + h), fill=None, outline=color, width=4)

            for i in otherborder:
                highlight(i, 'blue')
            if len(border) < 4:  # 由5改为4 是为了日后准备加入残缺队伍查询（通过无api版本）
                for i in border:
                    highlight(i, 'blue')
            else:
                recs = border
                recs = set([tuple(rec) for rec in recs])

                def split_last_col_recs(recs):
                    if not recs:
                        return [], []
                    recs = sorted(recs, key=lambda x: x[0], reverse=True)
                    last_col_recs = [rec for rec in recs if abs(rec[0] - recs[0][0]) < recs[0][2] / 2]
                    last_col_recs = sorted(last_col_recs, key=lambda x: x[1])
                    return list(set(recs) - set(last_col_recs)), last_col_recs

                _, last_col_recs = split_last_col_recs(recs)  # 先找出最右侧的一列有几行
                row_cnt = len(last_col_recs)

                arr = [[None for __ in range(5)] for _ in range(row_cnt)]
                arr_id = [[] for _ in range(row_cnt)]
                arr_id_6 = [[0 for __ in range(5)] for _ in range(row_cnt)]
                last_col_recs_xpos = [rec[1] for rec in last_col_recs]  # 以最右一列的坐标为基准

                for col_index in range(5):  # 从右往左一列一列掰，最多拿五列
                    recs, last_col_recs = split_last_col_recs(recs)
                    if len(last_col_recs) == 0:
                        break
                    for rec in last_col_recs:
                        # 看看rec能不能被识别出来
                        x, y, w, h = rec
                        cropped = img.crop([x + 2, y + 2, x + w - 2, y + h - 2])
                        uid_6, unit_id, unit_name, similarity = await getUnit(cropped)
                        # print(uid_6, unit_id, unit_name, similarity)  # 0 0 Unknown -1~-5
                        if unit_id == 0:
                            highlight(rec, "black")
                        else:
                            highlight(rec, "red" if unit_id != 1000 else "yellow")
                            most_near_row = 0
                            for row_index in range(1, len(arr)):
                                if abs(last_col_recs_xpos[row_index] - rec[1]) < abs(last_col_recs_xpos[most_near_row] - rec[1]):
                                    most_near_row = row_index
                            if arr[most_near_row][col_index] is None or abs(last_col_recs_xpos[most_near_row] - arr[most_near_row][col_index][1]) > abs(last_col_recs_xpos[most_near_row] - rec[1]):
                                arr[most_near_row][col_index] = rec
                                arr_id_6[most_near_row][col_index] = uid_6

                for rec in recs:
                    highlight(rec, "green")

                # 创建一个 rowcnt行 5列 的画布，行间及四周留16px空隙，每行中的每列分为上下两个头像：截出来的和通过识别的id render出来的（均为64*64)。头像间隙0px。
                icon_size = 64
                compare_img = Image.new("RGBA", (icon_size * 5 + 16 * 2, icon_size * 2 * row_cnt + 16 * (row_cnt + 1)), (255, 255, 255, 255))

                for row_index in range(row_cnt):
                    arr_id[row_index] = [uid // 100 for uid in arr_id_6[row_index] if uid]
                    none_cnt = arr[row_index].count(None)
                    if none_cnt >= 2:  # 不允许1-3个角色查询 不渲染
                        arr_id[row_index] = []
                        continue
                    for col_index in range(5):
                        if arr[row_index][4 - col_index] is None:
                            continue
                        pos_x = 16 + icon_size * col_index
                        pos_y = 16 * (row_index + 1) + icon_size * 2 * row_index
                        x, y, w, h = arr[row_index][4 - col_index]
                        cropped = img.crop([x + 2, y + 2, x + w - 2, y + h - 2]).resize((64, 64), Image.Resampling.LANCZOS)
                        compare_img.paste(cropped, (pos_x, pos_y), cropped)  # 要不要加cropped
                        # 对比图直接使用识别库的原图，不依赖星级/专武素材或下载。
                        icon = Image.fromarray(data[arr_id_6[row_index][4 - col_index]]).convert('RGBA').resize((icon_size, icon_size), Image.Resampling.LANCZOS)
                        compare_img.paste(icon, (pos_x, pos_y + 64), icon)

                teams = [team for team in arr_id if team]
                if not teams:
                    return [], ''

                def outp_b64(outp_img):
                    buf = BytesIO()
                    outp_img.save(buf, format='PNG')
                    base64_str = f'base64://{base64.b64encode(buf.getvalue()).decode()}'
                    return f'[CQ:image,file={base64_str}]'

                outpImg = Image.blend(outpImg, actual_img, 0.2)
                outpImg.alpha_composite(outpImgText)
                ratio = max(1, max((outpImg.size)[0], (outpImg.size)[1]) / 500)
                outpImg = outpImg.resize((int((outpImg.size)[0] / ratio), int((outpImg.size)[1] / ratio)), Image.Resampling.LANCZOS)

                return teams, f'{outp_b64(outpImg)}\n{outp_b64(compare_img)}'

        try:
            im_grey, border = await cutting(im_grey, 1)  # 获取图片中最大的长方形区域
        except:
            return [], ""
        if cnt == 1 or bo:  # 如果是第一次裁剪，或者识别不到正方形区域，反色
            im_grey = im_grey.point(lambda x: 0 if x > 128 else 255)
        img = await cut(img, border)  # 将原始img也裁剪，和im_grey同步
        actual_x += border[0] + 2
        actual_y += border[1] + 2

        nowcolor = (nowcolor + 60) % 300
        outpImg.paste(ImageColor.getrgb(f'rgb({nowcolor},{nowcolor},{nowcolor})'), (actual_x, actual_y, actual_x + border[2], actual_y + border[3]))

        # outpImg.show()  # test

    return [], ""


@_in_worker
async def getUnit(img2):
    img2 = img2.convert("RGB").resize((128, 128), Image.Resampling.LANCZOS)
    img3 = np.array(img2)
    img4 = img3[25:96, 8:97, :]
    img4 = Image.fromarray(img4)
    dic = {}
    global data_processed
    if data_processed == None:
        await process_data()

    if not data_processed:
        return 0, 0, 'Unknown', -1

    img4_arr = await get_hash_arr(img4)
    for uid in data_processed:
        dic[uid] = await calc_distance_arr(data_processed[uid], img4_arr)

    lis = list(sorted(dic.items(), key=lambda x: abs(x[1])))

    similarity = int(lis[0][1])
    if similarity > 90:  # 没一个相似的
        return 0, 0, "Unknown", 100 - similarity
    uid_6 = int(lis[0][0])
    uid = uid_6 // 100
    try:
        return uid_6, uid, chara.fromid(uid).name, 100 - similarity
    except:
        return uid_6, uid, "Unknown", 100 - similarity


async def get_pic(address: str):
    def download():
        if not _download_slots.acquire(blocking=False):
            raise RecognitionBusy('图片下载繁忙，请稍后重试')
        try:
            return read_response()
        finally:
            _download_slots.release()

    def read_response():
        started = time.monotonic()
        with requests.get(address, stream=True, timeout=(3, 6)) as response:
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', '').split(';')[0]
            if content_type and not content_type.startswith('image/'):
                raise ValueError('图片链接未返回图片，请重新发送截图')
            body = bytearray()
            for chunk in response.iter_content(64 * 1024):
                if len(body) + len(chunk) > MAX_IMAGE_BYTES:
                    raise ValueError('截图超过8 MiB，请压缩后重试')
                if time.monotonic() - started > 15:
                    raise ValueError('图片下载超时，请重新发送截图')
                body.extend(chunk)
            return bytes(body)
    return await asyncio.to_thread(download)


def _decode_image(body):
    with Image.open(BytesIO(body)) as image:
        _check_image(image)
        return image.convert('RGB')


async def _recognize_bytes(body):
    with _decode_image(body) as image:
        return await getBox(image)


async def render_atk_def_teams(entries, *, defense_teams=()):
    def get_character(uid: int):
        try:
            return chara.fromid(uid)
        except (LookupError, ValueError):
            return None

    def character_name(uid: int) -> str:
        return getattr(get_character(uid), "name", str(uid))

    return await render_result_card(
        entries,
        defense_groups=[[get_character(uid) for uid in team] for team in defense_teams],
        placeholder=lambda: get_character(1000),
        character_name=character_name,
    )


async def _QueryArenaImageAsync(image_url: str, region: int, bot: HoshinoBot, ev: CQEvent):
    try:
        teams, preview = await _run_recognition(_recognize_bytes, await get_pic(image_url))
    except (requests.RequestException, OSError, ValueError, Image.DecompressionBombError) as exc:
        sv.logger.warning('截图识别失败：%s', exc)
        message = str(exc) if isinstance(exc, ValueError) else '图片下载或解码失败，请重新发送有效截图'
        await bot.send(ev, message, at_sender=True)
        return
    if not teams or not any(teams):
        await bot.finish(ev, '未识别到阵容，请检查截图及本地头像资源')
    if preview:
        await bot.send(ev, preview)
    if len(teams) == 1:
        await __arena_query(bot, ev, region, teams[0])
        return
    if len(teams) > 3:
        await bot.finish(ev, '请发送含 2 至 3 队防守阵容的对战履历详情截图')

    boxDict = teams
    best_atk_records = await asyncio.to_thread(load_best_attacks)
    team_has_result = 0
    all_query_records = [[] for _ in range(len(boxDict))]
    '''
    [
        [
            [None, -100, None], # 通配，等待从缓存中获取配队
            [(第1队第1解),权值,render(渲染该队所需数据)]
            [(第1队第2解),权值,render]
        ],
        [
            [None, -100, None], # 通配
            [(第2队第1解),权值,render]
        ]
    ]
    '''

    for query_index, query_team in enumerate(boxDict):
        all_query_records[query_index].append([None, -100, "placeholder"])

        if len(query_team) < 4 or all(uid == 1000 for uid in query_team):  # 1000 1000 1000 1000 1000 pjjc情况
            continue

        if invalid_defense(query_team):
            continue
        records = await __arena_query(bot, ev, region, query_team, raw=1)

        if records == []:
            continue

        team_has_result += 1

        for record in records:
            record_team = tuple(chara_obj.id for chara_obj in record['atk'])
            all_query_records[query_index].append([record_team, record["val"], record])

    if team_has_result == 0 and len(boxDict) == 3:
        await bot.finish(ev, "均未查询到解法！")

    await generateCollisionFreeTeam(bot, ev, all_query_records, team_has_result, region, boxDict, best_atk_records)  # 最多允许补两队


def recommend1Team(already_used_units, best_atk_records):
    '''
    already_used_units: [1110,1008,1011,1026,1089] 不可使用的角色id（四位）
    return : render | "placeholder"
    '''
    for record_6 in best_atk_records:  # [111451,101261,110351,103461,103261]
        record_4 = [x // 100 for x in record_6]  # [1114,1012,1103,1034,1032]
        team_mix = already_used_units + record_4
        if len(team_mix) == len(set(team_mix)):  # 推荐配队成功
            return {"atk": [chara.fromid(uid_6 // 100, uid_6 % 100 // 10) for uid_6 in record_6], "team_type": "frequency"}
    return "placeholder"


def recommend2Teams(already_used_units, best_atk_records):
    '''
    return : render, render 或 "placeholder", "placeholder"
    '''

    try_combinations = []  # [查询1解序号, 查询2解序号, 优先级]
    for record_1_index in range(len(best_atk_records) - 1):
        for record_2_index in range(record_1_index + 1, len(best_atk_records)):
            try_combinations.append([record_1_index, record_2_index, record_1_index + record_2_index])
    try_combinations = list(sorted(try_combinations, key=lambda x: x[-1]))
    for try_combination in try_combinations:
        record_6_1_index = try_combination[0]  # [111451,101261,110351,103461,103261]
        record_6_1 = best_atk_records[record_6_1_index]
        record_4_1 = [x // 100 for x in record_6_1]

        record_6_2_index = try_combination[1]
        record_6_2 = best_atk_records[record_6_2_index]
        record_4_2 = [x // 100 for x in record_6_2]

        team_mix = already_used_units + record_4_1 + record_4_2
        if len(team_mix) == len(set(team_mix)):  # 推荐配队成功
            # print(f'\n\n成功配队{already_used_units}\n{record_4_1}\n{record_4_2}')  # test
            return {"atk": [chara.fromid(uid_6 // 100, uid_6 % 100 // 10) for uid_6 in record_6_2], "team_type": "frequency"}, {"atk": [chara.fromid(uid_6 // 100, uid_6 % 100 // 10) for uid_6 in record_6_1], "team_type": "frequency"}

    return "placeholder", "placeholder"


async def generateCollisionFreeTeam(bot: HoshinoBot, ev: CQEvent, all_query_records, team_has_result, region, boxDict, best_atk_records):
    '''
    all_query_records
    [
        [
            [None, -100, "placeholder"], # 通配，等待从缓存中获取配队
            [(1110,1008,1011,1026,1089), 2.105, render], # [(第1队第1解),权值,render(渲染该队所需数据)]
            [(1111,1008,1802,1012,1014), 1.152, render] # [(第1队第2解),权值,render]
        ],
        [
            [None, -100, "placeholder"], # 通配
            [(第2队第1解),权值,render]
        ]
    ]
    '''
    collision_free_match_cnt = 0
    outp_render = []
    collision_free_match_cnt_2 = 0  # 处理三队查询只能两队无冲的情况
    outp_render_2 = []

    if len(all_query_records) == 2:
        try_combinations = []  # [查询1解序号, 查询2解序号, 优先级]
        for query_1_index, query_1_record in enumerate(all_query_records[0]):
            for query_2_index, query_2_record in enumerate(all_query_records[1]):
                val = query_1_record[1] + query_2_record[1]
                try_combinations.append([query_1_index, query_2_index, val])
        try_combinations = sorted(try_combinations, key=lambda x: x[-1], reverse=True)

        for try_combination in try_combinations:
            record_1 = all_query_records[0][try_combination[0]]  # [(1110,1008,1011,1026,1089), 2.105, render] # 或通配
            record_2 = all_query_records[1][try_combination[1]]
            team_1 = [] if record_1[0] is None else list(record_1[0])  # (1110,1008,1011,1026,1089)
            team_2 = [] if record_2[0] is None else list(record_2[0])
            team_mix = team_1 + team_2  # list
            if len(team_mix) != len(set(team_mix)):  # 存在冲突
                continue

            succ = False
            val = try_combination[-1]
            if val < -250:
                break
            if val < -150:  # 已有0队，要补2队 # 只会出现一次
                team_recommend_1, team_recommend_2 = recommend2Teams(team_mix, best_atk_records)
                if team_recommend_1 == "placeholder" or team_recommend_2 == "placeholder":
                    continue
                record_1[-1] = team_recommend_1
                record_2[-1] = team_recommend_2
                succ = True
            elif val < -50:  # 已有1队，要补1队
                team_recommend = recommend1Team(team_mix, best_atk_records)
                if team_recommend == "placeholder":
                    continue
                if team_1 == []:
                    record_1[-1] = team_recommend
                if team_2 == []:
                    record_2[-1] = team_recommend
                succ = True
            else:  # 已有2队
                succ = True

            if succ:
                collision_free_match_cnt += 1
                outp_render += [record_1[-1], record_2[-1], []]
                if collision_free_match_cnt >= 8:
                    break

    if len(all_query_records) == 3:
        try_combinations = []  # [查询1解序号, 查询2解序号, 查询3解序号, 优先级]
        for query_1_index, query_1_record in enumerate(all_query_records[0]):
            for query_2_index, query_2_record in enumerate(all_query_records[1]):
                for query_3_index, query_3_record in enumerate(all_query_records[2]):
                    val = query_1_record[1] + query_2_record[1] + query_3_record[1]
                    try_combinations.append([query_1_index, query_2_index, query_3_index, val])
        try_combinations = sorted(try_combinations, key=lambda x: x[-1], reverse=True)

        for try_combination in try_combinations:
            record_1 = all_query_records[0][try_combination[0]]  # [(1110,1008,1011,1026,1089), 2.105, render] # 或通配
            record_2 = all_query_records[1][try_combination[1]]
            record_3 = all_query_records[2][try_combination[2]]
            team_1 = [] if record_1[0] is None else list(record_1[0])  # (1110,1008,1011,1026,1089)
            team_2 = [] if record_2[0] is None else list(record_2[0])
            team_3 = [] if record_3[0] is None else list(record_3[0])
            team_mix = team_1 + team_2 + team_3  # list
            if len(team_mix) != len(set(team_mix)):  # 存在冲突
                continue

            succ = False
            val = try_combination[-1]
            if val < -250:
                break
            if val < -150:  # 已有1队，要补2队
                team_recommend_1, team_recommend_2 = recommend2Teams(team_mix, best_atk_records)
                if team_recommend_1 == "placeholder" or team_recommend_2 == "placeholder":
                    continue
                if team_1 != []:
                    record_2[-1] = team_recommend_1
                    record_3[-1] = team_recommend_2
                if team_2 != []:
                    record_3[-1] = team_recommend_1
                    record_1[-1] = team_recommend_2
                if team_3 != []:
                    record_1[-1] = team_recommend_1
                    record_2[-1] = team_recommend_2
                succ = True
            elif val < -50:  # 已有2队，要补1队 # 此时已有两队无冲
                team_recommend = recommend1Team(team_mix, best_atk_records)
                if team_recommend == "placeholder":
                    collision_free_match_cnt_2 += 1
                    outp_render_2 += [record_1[-1], record_2[-1], record_3[-1], []]
                else:
                    if team_1 == []:
                        record_1[-1] = team_recommend
                    if team_2 == []:
                        record_2[-1] = team_recommend
                    if team_3 == []:
                        record_3[-1] = team_recommend
                    succ = True
            else:  # 已有3队
                succ = True

            if succ:
                collision_free_match_cnt += 1
                outp_render += [record_1[-1], record_2[-1], record_3[-1], []]
                # print(f'当前无冲配队数={collision_free_match_cnt} len(outp_render)={len(outp_render)}')  # test
                if collision_free_match_cnt >= 6:
                    break

    if collision_free_match_cnt:
        # print(f'\n\n总共无冲配队数={collision_free_match_cnt} len(outp_render)={len(outp_render)}')  # test
        await bot.finish(ev, await format_results(outp_render[:-1], boxDict))
    elif collision_free_match_cnt_2:
        await bot.finish(ev, await format_results(outp_render_2[:-1], boxDict))
    else:
        if len(all_query_records) == 2:  # 查两队
            if team_has_result == 0:
                await bot.finish(ev, "均未查询到解法！")
            elif team_has_result == 1:
                for index, records in enumerate(all_query_records):
                    if len(records) > 1:
                        await bot.send(ev, f"仅第{index+1}队查询到解法！")
                        await __arena_query(bot, ev, region, boxDict[index], only_use_cache=True)
            else:  # 2队有结果，但凑不满2队无冲
                await bot.send(ev, "无冲配对失败，返回单步查询结果")
                for index, records in enumerate(all_query_records):
                    await __arena_query(bot, ev, region, boxDict[index], only_use_cache=True)
        else:  # 查三队
            if team_has_result == 1:
                for index, records in enumerate(all_query_records):
                    if len(records) > 1:
                        await bot.send(ev, f"仅第{index+1}队查询到解法！")
                        await __arena_query(bot, ev, region, boxDict[index], only_use_cache=True)
            else:  # 2~3队有结果，但凑不满2队无冲
                await bot.send(ev, "无冲配对失败，返回单步查询结果")
                for index, records in enumerate(all_query_records):
                    if len(records) > 1:
                        await __arena_query(bot, ev, region, boxDict[index], only_use_cache=True)


async def _QueryArenaTextAsync(text: str, region: int, bot: HoshinoBot, ev: CQEvent):
    defen = re.sub(r'[?？，,_]', '', text)
    defen, unknown = chara.roster.parse_team(defen)
    if unknown:
        _, name, score = chara.guess_id(unknown)
        if score < 70 and not defen:
            return  # 忽略无关对话
        unknown = filt_message(unknown)
        msg = f'无法识别"{unknown}"' if score < 70 else f'无法识别"{unknown}" 您说的有{score}%可能是{name}'
        await bot.finish(ev, msg)
    await __arena_query(bot, ev, region, defen)

def invalid_defense(team):
    known = [uid for uid in team if uid != 1000]
    return (not 4 <= len(team) <= 5 or len(set(known)) != len(known)
            or any(chara.is_npc(uid) for uid in known))


async def __arena_query(bot, ev: CQEvent, region: int, defen, raw=0, only_use_cache=False):
    if len(defen) > 5:
        await bot.finish(ev, '编队不能多于5名角色', at_sender=True)
    if len(defen) < 4:
        await bot.finish(ev, '编队角色过少', at_sender=True)
    if len(defen) != len(set(defen) - {1000}) + defen.count(1000):
        await bot.finish(ev, '编队中含重复角色', at_sender=True)
    if any(chara.is_npc(i) for i in defen if i != 1000):
        await bot.finish(ev, '编队中含未实装角色', at_sender=True)

    defense_ids = tuple(defen)
    res = await query_candidates(defen, region, only_use_cache)
    notices = getattr(res, "notices", [])
    if notices:
        await bot.send(ev, "\n".join(notices))

    defen = [chara.fromid(x).name for x in defen]
    defen = f"防守方【{' '.join(defen)}】"

    # 处理查询结果
    if not res:
        if not raw:
            await bot.finish(ev, f'{defen}\n未查询到解法')
        else:
            return []

    if raw:
        return select_results(res, 24)  # At most25^3 PJJC combinations, including each placeholder.

    # 发送回复
    res = select_results(res, 10)  # Preserve database rows and a few model rows on simulation degradation.
    await bot.send(ev, defen + '\n' + await format_results(res, (defense_ids,)))


async def format_results(rows, defense_teams):
    from . import render_atk_def_teams as render
    try:
        with await render(rows, defense_teams=defense_teams) as image:
            return str(MessageSegment.image(pic2b64(image)))
    except (OSError, ValueError, UnicodeError) as exc:
        sv.logger.warning('竞技场结果绘图失败，改用文字：%s', exc)
        return '图片资源不可用，以下为文字结果：\n' + '\n'.join(
            ' '.join(c.name for c in row['atk']) + ' ' + result_text(row)
            if isinstance(row, dict) else '未知阵容' if row == 'placeholder' else ''
            for row in rows)


@sucmd('竞技场更新卡池', force_private=False)
async def _update_dic(bot_session):
    def rebuild():
        with _recognition_lock:
            return update_dic()
    await bot_session.send(await asyncio.to_thread(rebuild))
