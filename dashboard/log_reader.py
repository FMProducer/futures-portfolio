"""Bounded log tails and incremental parsing for read-only dashboards."""

import os
from functools import lru_cache
from threading import RLock

LOCK = RLock()


@lru_cache(maxsize=128)
def _cursor(path, consumer):
    return {}


def appended_lines(path):
    """Return complete appended lines, resetting on rotation or rewrite."""
    with path.open("rb") as stream:
        stat = os.fstat(stream.fileno())
        state = _cursor(path, "samples")
        identity = stat.st_dev, stat.st_ino
        offset = state.get("offset", 0)
        stream.seek(max(0, offset - 64))
        boundary = stream.read(min(64, offset))
        reset = (state.get("identity") != identity or stat.st_size < offset
                 or boundary != state.get("boundary", b"")
                 or (stat.st_size == state.get("size") and stat.st_mtime_ns != state.get("mtime")))
        if reset:
            state.clear()
            offset = 0
        stream.seek(offset)
        body = stream.read(stat.st_size - offset)
        end = max(body.rfind(b"\n"), body.rfind(b"\r")) + 1
        complete, pending = body[:end], body[end:]
        offset += end
        stream.seek(max(0, offset - 64))
        state.update(identity=identity, offset=offset, size=stat.st_size, mtime=stat.st_mtime_ns,
                     boundary=stream.read(min(64, offset)))
        return state, reset, complete.decode("utf-8", errors="replace").splitlines(), pending


def read_tail(path, lines=100, offset=0):
    """Read backwards in blocks, retaining only the requested tail."""
    wanted = lines + offset
    if wanted <= 0:
        return []
    with path.open("rb") as stream:
        position = stream.seek(0, os.SEEK_END)
        blocks, breaks = [], 0
        while position and breaks <= wanted:
            size = min(position, 8192)
            position -= size
            stream.seek(position)
            block = stream.read(size)
            if blocks and block.endswith(b"\r") and blocks[-1].startswith(b"\n"):
                breaks -= 1
            blocks.append(block)
            breaks += block.count(b"\n") + block.count(b"\r") - block.count(b"\r\n")
    text = b"".join(reversed(blocks)).decode("utf-8", errors="ignore")
    tail = text.splitlines()[-wanted:]
    return tail[:max(0, len(tail) - offset)] if offset else tail


def line_count(path):
    with LOCK:
        with path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            state = _cursor(path, "count")
            identity = stat.st_dev, stat.st_ino
            offset = state.get("offset", 0)
            stream.seek(max(0, offset - 64))
            boundary = stream.read(min(64, offset))
            if (state.get("identity") != identity or stat.st_size < offset
                    or boundary != state.get("boundary", b"")
                    or (stat.st_size == offset and stat.st_mtime_ns != state.get("mtime"))):
                state.clear()
                offset = 0
            stream.seek(offset)
            count, last = state.get("count", 0), state.get("last", b"")
            remaining = stat.st_size - offset
            while remaining:
                block = stream.read(min(65536, remaining))
                if not block:
                    break
                count += block.count(b"\n") + block.count(b"\r") - block.count(b"\r\n")
                count -= last == b"\r" and block.startswith(b"\n")
                last = block[-1:]
                remaining -= len(block)
            offset = stream.tell()
            stream.seek(max(0, offset - 64))
            state.update(identity=identity, offset=offset, mtime=stat.st_mtime_ns,
                         boundary=stream.read(min(64, offset)), count=count, last=last)
            return count + bool(last and last not in (b"\n", b"\r"))
