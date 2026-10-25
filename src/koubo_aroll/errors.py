"""流水线里对外抛出的错误。"""


class KouboError(Exception):
    """用户能看懂的失败。CLI 捕获后原样打印。"""
