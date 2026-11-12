"""文本向量。默认用本地小模型：装了 sentence-transformers 用它，否则用 fastembed（ONNX，不装 PyTorch）；
都用不了时退回到词袋哈希向量。

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


class FastEmbedEmbedder:
    """同一个 bge 模型的 ONNX 版：依赖小（不装 PyTorch），安装包默认用它。"""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        from fastembed import TextEmbedding

        from ..config import CONFIG_DIR

        self.name = model_name
        # 默认缓存在系统临时目录，重启就清空、每天重新下载：放进数据目录
        self._model = TextEmbedding(model_name, cache_dir=str(CONFIG_DIR / "models"))

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:  # 文献库为空时也要是 (0, 维数)，后面要做矩阵乘
            return np.zeros((0, self._model.embedding_size), dtype=np.float32)
        vecs = list(self._model.embed(texts, batch_size=64))
        return _normalize(np.asarray(vecs, dtype=np.float32))


def default_embedder(model_name: str = "BAAI/bge-small-en-v1.5") -> Embedder:
    for cls in (SentenceTransformerEmbedder, FastEmbedEmbedder):
        try:
            return cls(model_name)
        except ImportError:
            continue
        except Exception as e:  # 模型下载失败等：当天照样出推荐，只是质量差一些
            warnings.warn(f"向量模型加载失败（{e}），改用词袋向量。", stacklevel=2)
            return HashingEmbedder()
    warnings.warn("没有可用的向量模型，改用词袋向量，推荐质量会差一些。", stacklevel=2)
    return HashingEmbedder()
