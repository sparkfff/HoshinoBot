"""PCR 头像/阵容识别基础，兼容 watermellye 的 old_main 导入路径。

识图算法移植自 watermellye/HoshinoBot 的 ellye 分支：
https://github.com/watermellye/HoshinoBot/blob/ellye/hoshino/modules/priconne/arena/old_main.py
沿用 HoshinoBot 的 GPL-3.0 许可证。仅保留识图及当前查询接口适配，
不加载该分支的缓存推荐系统或自动定时任务。
"""
import base64
from io import BytesIO
from os.path import dirname, join

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageColor

from hoshino import aiorequests, sucmd
from hoshino.typing import CQEvent, HoshinoBot, Message
from .. import chara
from . import sv
from .record import load_icon_data, update_dic

curpath = dirname(__file__)
dataDir = join(curpath, 'dic.npy')
data = {}
data_processed = None
_data_loaded = False


async def getBox(img):
    return await getPos(img)


async def cut_image(image, hash_size=16):
    '''
    将图像缩小成(16+1)*16并转化成灰度图
    :param image: PIL.Image
    :return list[int]
    '''

    image1 = image.resize((hash_size + 1, hash_size), Image.LANCZOS).convert('L')
    pixel = list(image1.getdata())
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
                        cropped = img.crop([x + 2, y + 2, x + w - 2, y + h - 2]).resize((64, 64), Image.LANCZOS)
                        compare_img.paste(cropped, (pos_x, pos_y), cropped)  # 要不要加cropped
                        # 对比图直接使用识别库的原图，不依赖星级/专武素材或下载。
                        icon = Image.fromarray(data[arr_id_6[row_index][4 - col_index]]).convert('RGBA').resize((icon_size, icon_size), Image.LANCZOS)
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
                outpImg = outpImg.resize((int((outpImg.size)[0] / ratio), int((outpImg.size)[1] / ratio)), Image.LANCZOS)

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


async def getUnit(img2):
    img2 = img2.convert("RGB").resize((128, 128), Image.LANCZOS)
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
    return await (await aiorequests.get(address, timeout=6)).content


async def _QueryArenaTextAsync(text: str, region: int, bot: HoshinoBot, ev: CQEvent):
    """兼容 old_main 的查询入口，使用当前保留的竞技场后端。"""
    from . import _arena_query
    event = CQEvent(dict(ev))
    event.message = Message(text)
    await _arena_query(bot, event, region, _skip_limiter=True)


async def _QueryArenaImageAsync(image_url: str, region: int, bot: HoshinoBot, ev: CQEvent):
    with Image.open(BytesIO(await get_pic(image_url))) as image:
        teams, preview = await getBox(image)
    if not teams or not any(teams):
        await bot.finish(ev, '未识别到阵容，请检查截图及本地头像资源')
    if preview:
        await bot.send(ev, preview)
    for team in teams:
        if not team:
            continue
        if len(team) != 5:
            await bot.send(ev, '已识别部分角色；当前竞技场查询接口需要完整的5人队伍')
            continue
        text = ' '.join(chara.fromid(uid).name for uid in team)
        await _QueryArenaTextAsync(text, region, bot, ev)


@sucmd('竞技场更新卡池', force_private=False)
async def _update_dic(bot_session):
    await bot_session.send(update_dic())
