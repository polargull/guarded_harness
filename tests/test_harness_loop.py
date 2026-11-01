from smolagents import ToolCallingAgent


def understanding_script():
    return [('submit_plan', {'steps': ['阅读源码', '生成说明']}),
            ('read_file', {'path': 'src/calculator.py'}),
            ('write_file', {'path': 'artifacts/report.md', 'content': '# 代码理解\ncalculator.add 当前错误地做减法。'}),
            ('final_answer', {'answer': '代码说明已写入 artifacts/report.md。'})]


def test_real_smolagents_loop_with_fake_model_and_approval_resume(api, repo, tmp_path):
    script = understanding_script()
    h = api.Harness.create(repo, tmp_path / 'run', task='解释代码', kind='code_understanding')
    state = h.run(api.FakeModel(script))
    assert isinstance(h.agent, ToolCallingAgent)
    assert state.status == 'WAITING_APPROVAL'
    assert not (repo / 'artifacts/report.md').exists()
    h.approve(next(iter(state.pending)), actor='human:owner')
    restored = api.Harness.resume(h.run_dir)
    state = restored.run(api.FakeModel(script))
    assert state.status == 'COMPLETED'
    assert state.rounds == 4
    assert (repo / 'artifacts/report.md').exists()


def test_test_fix_full_loop_records_fresh_tests_and_diff(api, repo, tmp_path):
    script = [('submit_plan', {'steps': ['阅读', '测试复现', '修复', '验证', '报告']}),
              ('read_file', {'path': 'src/calculator.py'}),
              ('run_tests', {'command': 'pytest'}),
              ('write_file', {'path': 'src/calculator.py', 'content': 'def add(a, b):\n    return a + b\n'}),
              ('run_tests', {'command': 'pytest'}),
              ('write_file', {'path': 'artifacts/report.md', 'content': '将减法修复为加法；测试通过。'}),
              ('final_answer', {'answer': '修复完成'})]
    h = api.Harness.create(repo, tmp_path / 'fix', task='修复测试', kind='test_fix')
    for _ in range(3):
        state = h.run(api.FakeModel(script))
        if state.status != 'WAITING_APPROVAL':
            break
        for cid in list(state.pending):
            h.approve(cid, actor='human:owner')
        h = api.Harness.resume(h.run_dir)
    assert state.status == 'COMPLETED'
    assert any(e['kind'] == 'test' and e['exit_code'] == 0 for e in state.evidence)
    assert '+    return a + b' in h.diff()


def test_model_cannot_bypass_gate_with_bad_args_or_unknown_tool(api, repo, tmp_path):
    h = api.Harness.create(repo, tmp_path / 'bad', task='解释', kind='code_understanding', max_rounds=2)
    s = h.run(api.FakeModel([('read_file', {'path': 2}), ('imaginary_tool', {})]))
    assert s.handler_calls == 0
    assert s.status == 'STOPPED'
    assert any(e['rule'] == 'INVALID_ARGUMENTS' for e in h.audit.read())


def test_parallel_tool_batch_is_rejected_before_any_handler(api, repo, tmp_path):
    model = api.FakeModel([[('read_file', {'path': 'src/calculator.py'}), ('read_file', {'path': 'tests/test_calculator.py'})]])
    h = api.Harness.create(repo, tmp_path / 'batch', task='解释', kind='code_understanding', max_rounds=1)
    s = h.run(model)
    assert s.handler_calls == 0
    assert s.stop_reason == 'MAX_ROUNDS'
    assert any(e['rule'] == 'ONE_CALL_PER_STEP' for e in h.audit.read())
