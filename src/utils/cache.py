import hashlib
import os
from collections import OrderedDict
from pathlib import Path
from typing import TypeVar

K = TypeVar("K")
V = TypeVar("V")


def get_positive_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def file_digest(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def lru_get(cache: OrderedDict[K, V], key: K) -> V | None:
    try:
        value = cache.pop(key)
    except KeyError:
        return None
    cache[key] = value
    return value


def lru_put(cache: OrderedDict[K, V], key: K, value: V, max_size: int) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > max_size:
        cache.popitem(last=False)
