"""文本向量。默认用本地小模型（sentence-transformers），没装时退回到词袋哈希向量。

所有向量都做 L2 归一化，所以点积就是余弦相似度。
"""

from __future__ import annotations

import hashlib
import re
import warnings
from typing import Protocol

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the of and or for to in on with via by from is are we our this that using based "
    "learning towards toward under over into at as be can".split()
)


class Embedder(Protocol):
    name: str

    def encode(self, texts: list[str]) -> np.ndarray: ...


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


class HashingEmbedder:
    """不依赖模型的词袋向量：离线、确定、很快，但只认字面词。用于测试和兜底。"""

    name = "hashing"

    def __init__(self, dim: int = 2048) -> None:
        self.dim = dim

    def _bucket(self, token: str) -> int:
        return int.from_bytes(hashlib.md5(token.encode()).digest()[:4], "little") % self.dim

    def encode(self, texts: list[str]) -> np.ndarray:
        m = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            tokens = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP and len(t) > 2]
            for a, b in zip(tokens, tokens[1:], strict=False):  # 加入二元词组
                m[i, self._bucket(f"{a}_{b}")] += 0.5
            for t in tokens:
                m[i, self._bucket(t)] += 1.0
        return _normalize(m)


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        from sentence_transformers import SentenceTransformer

        self.name = model_name
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self._model.encode(texts, batch_size=32, show_progress_bar=False)
        return _normalize(np.asarray(vecs, dtype=np.float32))


def default_embedder(model_name: str = "BAAI/bge-small-en-v1.5") -> Embedder:
    try:
        return SentenceTransformerEmbedder(model_name)
    except ImportError:
        warnings.warn(
            "没有安装 sentence-transformers，改用词袋向量，推荐质量会差一些。"
            "安装：uv sync --extra embed",
            stacklevel=2,
        )
    except Exception as e:  # 模型下载失败等：当天照样出推荐，只是质量差一些
        warnings.warn(f"向量模型加载失败（{e}），改用词袋向量。", stacklevel=2)
    return HashingEmbedder()
