import json
import os
import subprocess
import sys
from conftest import call


def test_c1_checkpoint_contains_complete_business_state(api, harness):
    call(api, harness, 'p', 'submit_plan', steps=['理解源码', '修复并测试'])
    call(api, harness, 'r', 'read_file', path='src/calculator.py')
    call(api, harness, 'w', 'write_file', path='src/calculator.py', content='fixed')
    saved = json.loads(harness.checkpoint.path.read_text())['state']
    for key in ['run_id', 'task', 'plan', 'steps', 'evidence', 'rounds', 'tool_calls', 'max_calls', 'pending', 'approvals', 'stop_reason', 'ledger']:
        assert key in saved
    assert saved['pending']['w']['arguments']['content'] == 'fixed'
    assert saved['evidence']


def test_c2_started_write_is_unknown_and_never_replayed(api, harness, repo):
    c = api.ToolCall('crash', 'write_file', {'path': 'src/calculator.py', 'content': 'written-once'})
    harness.gate.invoke(c)
    harness.approve('crash', actor='human:owner')
    # 模拟 handler 已产生副作用，但尚未来得及持久化执行结果的进程崩溃。
    original = harness.gate.handlers['write_file']
    def crash_after_write(**args):
        original(**args)
        raise SystemExit('simulated power loss')
    harness.gate.handlers['write_file'] = crash_after_write
    try:
        harness.gate.invoke(c)
    except SystemExit:
        pass
    restored = api.Harness.resume(harness.run_dir)
    assert restored.state.status == 'WAITING_REVIEW'
    assert restored.state.ledger['crash']['status'] == 'UNKNOWN_RESULT'
    assert restored.gate.invoke(c).code == 'UNKNOWN_RESULT'
    assert (repo / 'src/calculator.py').read_text() == 'written-once'


def test_c3_resume_in_another_process_preserves_completed_writes_and_counters(api, harness, repo):
    c = api.ToolCall('done', 'write_file', {'path': 'src/calculator.py', 'content': 'one-write'})
    harness.gate.invoke(c)
    harness.approve('done', actor='human:owner')
    harness.gate.invoke(c)
    count = harness.state.tool_calls
    script = 'from guarded_harness import Harness,ToolCall; import sys,json; h=Harness.resume(sys.argv[1]); r=h.gate.invoke(ToolCall("done","write_file",{"path":"src/calculator.py","content":"one-write"})); print(json.dumps({"code":r.code,"calls":h.state.tool_calls,"handlers":h.state.handler_calls}))'
    result = subprocess.run([sys.executable, '-c', script, str(harness.run_dir)], capture_output=True, text=True, check=True)
    record = json.loads(result.stdout)
    assert record['code'] == 'DUPLICATE_CALL'
    assert record['calls'] >= count and record['handlers'] == 1


def test_c4_changed_workspace_invalidates_evidence_and_approval(api, harness, repo):
    call(api, harness, 'r', 'read_file', path='src/calculator.py')
    call(api, harness, 'w', 'write_file', path='artifacts/report.md', content='a')
    harness.approve('w', actor='human:owner')
    (repo / 'src/calculator.py').write_text('external edit')
    h = api.Harness.resume(harness.run_dir)
    assert h.state.status == 'WAITING_REVIEW'
    assert h.state.stop_reason == 'WORKSPACE_CHANGED'
    assert not h.state.approvals and not h.state.evidence
    assert call(api, h, 'w', 'write_file', path='artifacts/report.md', content='a').code == 'WORKSPACE_CHANGED'


def test_checkpoint_checksum_detects_corruption(api, harness):
    import pytest
    payload = json.loads(harness.checkpoint.path.read_text())
    payload['state']['tool_calls'] = 999
    harness.checkpoint.path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='checksum'):
        api.Harness.resume(harness.run_dir)


def test_c4_directory_symlink_retarget_invalidates_approval(api, repo, tmp_path):
    (repo / 'src/v1').mkdir()
    (repo / 'src/v2').mkdir()
    (repo / 'src/v1/a.py').write_text('VERSION=1')
    (repo / 'src/v2/a.py').write_text('VERSION=2')
    link = repo / 'src/current'
    link.symlink_to('v1', target_is_directory=True)
    h = api.Harness.create(repo, tmp_path / 'links', task='解释源码', kind='code_understanding')
    call(api, h, 'r', 'read_file', path='src/current/a.py')
    call(api, h, 'w', 'write_file', path='artifacts/report.md', content='v1')
    h.approve('w', actor='human:owner')
    link.unlink()
    link.symlink_to('v2', target_is_directory=True)
    restored = api.Harness.resume(h.run_dir)
    assert restored.state.stop_reason == 'WORKSPACE_CHANGED'
    assert not restored.state.approvals and not restored.state.evidence


def test_stale_instances_cannot_repeat_an_approved_noop_write(api, repo, tmp_path):
    h = api.Harness.create(repo, tmp_path / 'stale', task='解释代码', kind='code_understanding', max_rounds=1)
    (repo / 'artifacts/report.md').write_text('same')
    # 先有报告，再创建运行，避免将其创建视为外部变化。
    h = api.Harness.create(repo, tmp_path / 'stale-actual', task='解释代码', kind='code_understanding', max_rounds=1)
    call(api, h, 'noop', 'write_file', path='artifacts/report.md', content='same')
    h.approve('noop', actor='human:owner')
    first, stale = api.Harness.resume(h.run_dir), api.Harness.resume(h.run_dir)
    script = [('final_answer', {'answer': '完成'})]
    first.run(api.FakeModel(script))
    stale.run(api.FakeModel(script))
    writes = [e for e in stale.audit.read() if e['tool_call_id'] == 'noop' and e['rule'] == 'HANDLER_FINISHED']
    assert len(writes) == 1


def test_stale_direct_gate_cannot_overwrite_newer_checkpoint(api, harness):
    import pytest
    other = api.Harness.resume(harness.run_dir)
    call(api, other, 'new', 'read_file', path='src/calculator.py')
    with pytest.raises(ValueError, match='stale'):
        call(api, harness, 'old', 'read_file', path='src/calculator.py')
