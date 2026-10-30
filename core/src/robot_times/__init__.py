"""The Robot Times 核心库。"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("robot-times")  # 唯一来源：core/pyproject.toml 的 version
except PackageNotFoundError:  # 没安装、直接从源码跑的时候
    __version__ = "0.0.0"
