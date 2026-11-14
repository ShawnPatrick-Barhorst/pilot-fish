try:
    import orjson

    def _dumps(obj) -> bytes:
        return orjson.dumps(obj, default=repr, option=orjson.OPT_APPEND_NEWLINE)
except ImportError:
    import json

    def _dumps(obj) -> bytes:
        return (json.dumps(obj, default=repr, separators=(",", ":")) + "\n").encode()

class JsonlSink:
    """One JSON array per line: ["codes", [...]] or ["events", thread, [...]]."""

    def __init__(self, path):
        self._f = open(path, "ab")

    def write_batch(self, obj):
        self._f.write(_dumps(obj))   # tuples → arrays

    def flush(self):
        self._f.flush()

    def close(self):
        self._f.close()
