"""只追加、落盘、带哈希链的结构化审计。哈希链用于发现意外篡改。"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from .checkpoint import canonical, digest


class Audit:
    def __init__(self, path, run_id):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.path.touch(mode=0o600, exist_ok=True)
        if not self.verify():
            raise ValueError('audit hash chain mismatch')

    def read(self):
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]

    def verify(self):
        previous = '0' * 64
        try:
            for seq, record in enumerate(self.read(), 1):
                content = {k: v for k, v in record.items() if k != 'hash'}
                if content['seq'] != seq or content['previous_hash'] != previous or digest(content) != record['hash']:
                    return False
                previous = record['hash']
            return True
        except (ValueError, KeyError, TypeError):
            return False

    def append(self, component, rule, decision, tool_call_id='-', actor='program', **details):
        records = self.read()
        event = {'seq': len(records) + 1, 'timestamp': datetime.now(timezone.utc).isoformat(),
                 'run_id': self.run_id, 'tool_call_id': tool_call_id, 'actor': actor,
                 'component': component, 'rule': rule, 'decision': decision,
                 'details': details, 'previous_hash': records[-1]['hash'] if records else '0' * 64}
        event['hash'] = digest(event)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            record = canonical(event) + b'\n'
            with os.fdopen(fd, 'ab', closefd=False) as f:
                f.write(record)
                f.flush()
                os.fsync(f.fileno())
        finally:
            os.close(fd)
        return event
