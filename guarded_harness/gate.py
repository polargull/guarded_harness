"""唯一工具执行入口：参数、权限、风险、审批、重复动作与副作用账本。"""
from .checkpoint import digest
from .completion import check_completion
from .sandbox import SandboxDenied
from .state_store import Result, ToolCall


SPECS = {
    'submit_plan': {'steps': list},
    'list_files': {'path': str},
    'read_file': {'path': str},
    'write_file': {'path': str, 'content': str},
    'run_tests': {'command': str},
    'final_answer': {'answer': str},
}
DESCRIPTIONS = {
    'submit_plan': '提交非空步骤计划。必须在最终完成前提交。',
    'list_files': '列出工作区相对目录中的文件；根目录使用 .。',
    'read_file': '读取工作区中的非敏感文本文件。',
    'write_file': '写入 src/ 或 artifacts/ 中的文本文件；必须由人类审批后执行。不得修改测试。',
    'run_tests': '运行管理员配置的固定测试命令。command 只能是 pytest，不接受 shell 或参数。',
    'final_answer': '申请最终验收。必须有计划、代码阅读证据、artifacts/report.md；修复任务还需要当前源码测试通过与源码 Diff。',
}


class Gate:
    def __init__(self, state, sandbox, checkpoint, audit):
        self.state, self.sandbox, self.checkpoint, self.audit = state, sandbox, checkpoint, audit
        self.handlers = {'read_file': sandbox.read_file, 'list_files': sandbox.list_files,
                         'write_file': sandbox.write_file, 'run_tests': sandbox.run,
                         'submit_plan': self._plan, 'final_answer': self._finish}

    def save(self, call_id='-'):
        self.checkpoint.save(self.state)
        self.audit.append('State', 'SAFE_BOUNDARY', 'SAVED', call_id,
                          status=self.state.status, rounds=self.state.rounds, tool_calls=self.state.tool_calls)

    def _result(self, call, ok, code, message='', data=None, component='Gate'):
        r = Result(ok, code, message, data)
        self.audit.append(component, code, 'ALLOW' if ok else 'DENY', call.id,
                          actor='program', tool=call.name, message=message)
        self.state.steps.append({'call': call.to_dict(), 'result': r.to_dict()})
        self.save(call.id)
        return r

    def invalidate_workspace(self, call_id='-'):
        self.state.evidence.clear()
        self.state.approvals.clear()
        self.state.stop('WORKSPACE_CHANGED', 'WAITING_REVIEW')
        self.audit.append('Checkpoint', 'WORKSPACE_CHANGED', 'DENY', call_id,
                          message='工作区版本变化；旧证据和审批失效，需人工核对')
        self.save(call_id)

    def _plan(self, steps):
        self.state.plan = steps
        return {'steps': steps}

    def _finish(self, answer):
        self.state.answer = answer
        self.state.status = 'COMPLETED'
        self.state.stop_reason = 'ACCEPTED'
        return {'answer': answer}

    @staticmethod
    def valid_arguments(call):
        spec = SPECS.get(call.name)
        if spec is None or not isinstance(call.arguments, dict) or set(call.arguments) != set(spec):
            return False
        for key, cls in spec.items():
            value = call.arguments[key]
            if type(value) is not cls:
                return False
            if cls is list and (not value or not all(type(s) is str and s.strip() for s in value)):
                return False
            if key in {'answer', 'path', 'command'} and not value.strip():
                return False
        return isinstance(call.id, str) and bool(call.id)

    def invoke(self, call):
        s = self.state
        self.checkpoint.ensure_current(s)
        old = s.ledger.get(call.id)
        if old and old['status'] in {'UNKNOWN_RESULT', 'STARTED'}:
            return self._result(call, False, 'UNKNOWN_RESULT', '执行结果未知，必须人工核对，禁止自动重放')
        if old and old['status'] in {'SUCCEEDED', 'FAILED'}:
            return self._result(call, False, 'DUPLICATE_CALL', '相同调用 ID 已处理，禁止再次执行')
        if s.status in {'STOPPED', 'WAITING_REVIEW', 'COMPLETED'}:
            return self._result(call, False, s.stop_reason or 'RUN_NOT_ACTIVE')
        if s.tool_calls >= s.max_calls:
            s.stop('TOOL_BUDGET')
            return self._result(call, False, 'TOOL_BUDGET')
        s.tool_calls += 1
        if call.name not in SPECS:
            return self._result(call, False, 'UNKNOWN_TOOL')
        if not self.valid_arguments(call):
            return self._result(call, False, 'INVALID_ARGUMENTS', f'需要字段及类型：{SPECS[call.name]}')
        fingerprint = digest({'tool': call.name, 'arguments': call.arguments})
        # 同一个待审批动作的恢复不算新的模型重复决策。
        if call.id not in s.pending:
            s.repeats = s.repeats + 1 if s.last_fingerprint == fingerprint else 1
            s.last_fingerprint = fingerprint
            if s.repeats >= 3:
                return self._result(call, False, 'REPEATED_ACTION', '连续第三次相同工具和参数已拦截')
        try:
            self.sandbox.validate(call.name, call.arguments)
        except (SandboxDenied, OSError, ValueError) as e:
            return self._result(call, False, 'SANDBOX_DENIED', str(e), component='Sandbox')
        self.audit.append('Sandbox', 'EXECUTION_BOUNDARY', 'ALLOW', call.id, tool=call.name)
        if self.sandbox.fingerprint() != s.workspace_digest:
            self.invalidate_workspace(call.id)
            return self._result(call, False, 'WORKSPACE_CHANGED')
        if call.name == 'write_file' and s.kind == 'code_understanding':
            resolved = self.sandbox.path(call.arguments['path'], write=True)
            if resolved.relative_to(self.sandbox.root).parts[0] != 'artifacts':
                return self._result(call, False, 'PERMISSION_DENIED', '代码理解任务只允许写 artifacts/')
        if call.name == 'write_file':
            binding = digest({'run_id': s.run_id, 'call': call.to_dict(), 'workspace': s.workspace_digest})
            approval = s.approvals.get(call.id)
            if not approval:
                if call.id in s.pending and s.pending[call.id] != call.to_dict():
                    return self._result(call, False, 'APPROVAL_MISMATCH')
                s.pending[call.id] = call.to_dict()
                s.status = 'WAITING_APPROVAL'
                return self._result(call, False, 'APPROVAL_REQUIRED', '等待人类审批；目标文件尚未修改')
            if approval['binding'] != binding or approval['consumed']:
                return self._result(call, False, 'APPROVAL_MISMATCH')
            approval['consumed'] = True
            s.pending.pop(call.id, None)
            s.status = 'RUNNING'
        elif s.status == 'WAITING_APPROVAL':
            return self._result(call, False, 'APPROVAL_REQUIRED', '先处理待审批动作')
        if call.name == 'final_answer':
            errors = check_completion(s, self.sandbox)
            if errors:
                return self._result(call, False, 'COMPLETION_REJECTED', '；'.join(errors), data={'errors': errors})
        self.audit.append('Gate', 'ALL_CHECKS_PASSED', 'ALLOW', call.id,
                          tool=call.name, risk='write' if call.name == 'write_file' else 'read_or_control')
        s.ledger[call.id] = {'call': call.to_dict(), 'status': 'STARTED', 'fingerprint': fingerprint}
        s.handler_calls += 1
        self.save(call.id)  # Write-ahead：先记账，再产生任何副作用。
        self.audit.append('Execution', 'HANDLER_STARTED', 'STARTED', call.id, tool=call.name)
        source_before = self.sandbox.fingerprint(source_only=True)
        try:
            data = self.handlers[call.name](**call.arguments)
        except Exception as e:
            if call.name == 'write_file':
                s.ledger[call.id]['status'] = 'UNKNOWN_RESULT'
                s.stop('UNKNOWN_RESULT', 'WAITING_REVIEW')
            else:
                s.ledger[call.id]['status'] = 'FAILED'
            s.workspace_digest = self.sandbox.fingerprint()
            self.audit.append('Execution', 'HANDLER_ERROR', 'FAILED', call.id, error=str(e))
            return self._result(call, False, 'EXECUTION_ERROR', str(e))
        s.ledger[call.id].update(status='SUCCEEDED', result=data)
        source_after = self.sandbox.fingerprint(source_only=True)
        if call.name == 'read_file':
            resolved = self.sandbox.path(data['path']).relative_to(self.sandbox.root)
            code_extensions = {'.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.go', '.rs', '.c', '.h', '.cpp', '.cs', '.rb', '.php', '.swift', '.kt', '.sh', '.vue'}
            s.evidence.append({'kind': 'read', 'call_id': call.id, 'source_digest': source_after,
                               'path': data['path'], 'sha256': data['sha256'],
                               'source_code': resolved.parts[0] != 'artifacts' and resolved.suffix in code_extensions})
        if call.name == 'write_file':
            s.evidence.append({'kind': 'write', 'call_id': call.id, **data})
        if call.name == 'run_tests':
            s.evidence.append({'kind': 'test', 'call_id': call.id, **data,
                               'source_digest': source_before if source_before == source_after else 'CHANGED_DURING_TEST'})
        s.workspace_digest = self.sandbox.fingerprint()
        self.audit.append('Execution', 'HANDLER_FINISHED', 'SUCCEEDED', call.id, result=data)
        return self._result(call, True, 'OK', data=data)
