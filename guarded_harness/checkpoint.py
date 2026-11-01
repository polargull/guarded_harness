"""原子 JSON Checkpoint，拒绝损坏或不兼容的状态。"""
import hashlib
import fcntl
import json
import os
from pathlib import Path
import tempfile
from .state_store import RunState


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class Checkpoint:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, state):
        with (self.path.parent / '.checkpoint.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.ensure_current(state)
            self._save(state)

    def ensure_current(self, state):
        if self.path.exists() and self.load().revision != state.revision:
            raise ValueError('stale checkpoint revision; reload before continuing')

    def _save(self, state):
        data = state.to_dict()
        data['revision'] += 1
        payload = {'schema_version': 1, 'checksum': digest(data), 'state': data}
        fd, temp = tempfile.mkstemp(prefix='.checkpoint-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(canonical(payload))
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)
            state.revision = data['revision']
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def load(self):
        payload = json.loads(self.path.read_text())
        if payload.get('schema_version') != 1:
            raise ValueError('unsupported checkpoint schema')
        if payload.get('checksum') != digest(payload['state']):
            raise ValueError('checkpoint checksum mismatch')
        return RunState.from_dict(payload['state'])
