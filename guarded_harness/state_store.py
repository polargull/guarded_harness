"""可 JSON 序列化的业务状态；模型输出与程序验收状态分离。"""
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict

    def to_dict(self):
        return asdict(self)


@dataclass
class Result:
    ok: bool
    code: str
    message: str = ''
    data: Any = None

    def to_dict(self):
        return asdict(self)


@dataclass
class RunState:
    run_id: str
    task: str
    kind: str
    workspace: str
    workspace_digest: str
    baseline: dict
    commands: dict
    revision: int = 0
    status: str = 'RUNNING'
    stop_reason: str | None = None
    rounds: int = 0
    tool_calls: int = 0
    handler_calls: int = 0
    tokens: int = 0
    max_rounds: int = 30
    max_calls: int = 60
    max_tokens: int = 50000
    plan: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    history: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    pending: dict = field(default_factory=dict)
    approvals: dict = field(default_factory=dict)
    ledger: dict = field(default_factory=dict)
    last_fingerprint: str | None = None
    repeats: int = 0
    answer: str | None = None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

    def stop(self, reason, status='STOPPED'):
        self.status = status
        self.stop_reason = reason
