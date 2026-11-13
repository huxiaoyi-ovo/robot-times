"""语言：中文（zh）或英文（en）。

两处用到：
- 生成的内容（精排要点、解读、简报、周报、问 AI）：按设置里的 lang 选提示词，见 presets.get_preset。
- 看板接口返回给用户看的消息（报错、提示、体检）：用 L("中文", "English") 写两份，
  按当前请求的语言挑一份。本地看板每个请求按页面带的 X-Lang 头设置（local/server.py），
  后台生成任务按设置里的 lang 设置（local/jobs.py）。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

LANGS = ("zh", "en")
DEFAULT_LANG = "zh"

_current: ContextVar[str] = ContextVar("robot_times_lang", default=DEFAULT_LANG)


def norm(lang: object) -> str:
    """不认识的写法一律当中文（老用户的设置里没有这一项）。"""
    s = str(lang or "").strip().lower()
    return "en" if s.startswith("en") else DEFAULT_LANG


def current() -> str:
    return _current.get()


def set_lang(lang: object) -> None:
    """设置当前线程（请求、后台任务）的语言。"""
    _current.set(norm(lang))


@contextmanager
def using(lang: object) -> Iterator[None]:
    token = _current.set(norm(lang))
    try:
        yield
    finally:
        _current.reset(token)


def L(zh: str, en: str) -> str:  # noqa: N802  短名字，满篇都是
    """按当前语言二选一。两份要同时写，格式化参数在外面统一做（"…{n}…".format）。"""
    return en if _current.get() == "en" else zh
