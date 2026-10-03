import os
from urllib.parse import urljoin
from urllib.request import pathname2url

from nonebot import MessageSegment
from PIL import Image

import hoshino
from hoshino import util

class ResObj:
    def __init__(self, res_path):
        self.__path = os.path.normpath(res_path)
        self._checked_path()

    def _checked_path(self):
        res_dir = os.path.realpath(os.path.expanduser(hoshino.config.RES_DIR))
        fullpath = os.path.realpath(os.path.join(res_dir, self.__path))
        try:
            contained = os.path.normcase(os.path.commonpath((res_dir, fullpath))) == os.path.normcase(res_dir)
        except ValueError:
            contained = False
        if not contained:
            raise ValueError('Cannot access outside RESOURCE_DIR')
        return fullpath

    @property
    def url(self):
        """资源文件的url，供Onebot（或其他远程服务）使用"""
        self._checked_path()
        relative = os.path.relpath(self.path, os.path.realpath(os.path.expanduser(hoshino.config.RES_DIR)))
        return urljoin(hoshino.config.RES_URL, pathname2url(relative))

    @property
    def path(self):
        """资源文件的路径，供Hoshino内部使用"""
        return self._checked_path()

    @property
    def exist(self):
        return os.path.exists(self.path)


class ResImg(ResObj):
    @property
    def cqcode(self) -> MessageSegment:
        if hoshino.config.RES_PROTOCOL == 'http':
            return MessageSegment.image(self.url)
        elif hoshino.config.RES_PROTOCOL == 'file':
            return MessageSegment.image(f'file:///{os.path.abspath(self.path)}')
        else:
            try:
                return MessageSegment.image(util.pic2b64(self.open()))
            except Exception as e:
                hoshino.logger.exception(e)
                return MessageSegment.text('[图片出错]')

    def open(self) -> Image:
        try:
            return Image.open(self.path)
        except FileNotFoundError:
            hoshino.logger.error(f'缺少图片资源：{self.path}')
            raise


def get(path, *paths):
    return ResObj(os.path.join(path, *paths))

def img(path, *paths):
    return ResImg(os.path.join('img', path, *paths))
