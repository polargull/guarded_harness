"""最终验收只信任程序产生并绑定当前文件版本的证据。"""
from pathlib import Path
import hashlib


def check_completion(state, sandbox):
    errors = []
    if not state.plan:
        errors.append('缺少计划')
    if not any(e['kind'] == 'read' and e.get('source_code') for e in state.evidence):
        errors.append('缺少代码阅读证据')
    report = sandbox.path('artifacts/report.md')
    reports = [e for e in state.evidence if e['kind'] == 'write' and e['path'] == 'artifacts/report.md']
    if not report.is_file() or not report.read_text().strip() or not reports:
        errors.append('缺少 artifacts/report.md 交付物')
    elif reports[-1]['sha256'] != hashlib.sha256(report.read_bytes()).hexdigest():
        errors.append('交付物已变化')
    if state.pending:
        errors.append('仍有待审批动作')
    if any(v['status'] in {'STARTED', 'UNKNOWN_RESULT'} for v in state.ledger.values()):
        errors.append('仍有未决执行结果')
    if state.kind == 'test_fix':
        current = sandbox.fingerprint(source_only=True)
        tests = [e for e in state.evidence if e['kind'] == 'test']
        if not tests or tests[-1]['exit_code'] != 0 or tests[-1]['reason'] != 'EXITED' or tests[-1]['source_digest'] != current:
            errors.append('缺少当前版本的通过测试证据')
        changed = any(sandbox.root.joinpath(p).is_file() and sandbox.root.joinpath(p).read_text() != text
                      for p, text in state.baseline.items() if Path(p).parts[0] == 'src')
        if not changed:
            errors.append('缺少源码修复 Diff')
    elif not any(e['kind'] == 'read' and e.get('source_code') and e['source_digest'] == sandbox.fingerprint(source_only=True)
                 for e in state.evidence):
        errors.append('代码理解证据不属于当前版本')
    return errors
