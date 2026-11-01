from conftest import call


def write_approved(api, h, id, path, content):
    c = api.ToolCall(id, 'write_file', {'path': path, 'content': content})
    h.gate.invoke(c)
    h.approve(id, actor='human:owner')
    assert h.gate.invoke(c).ok


def test_h1_claiming_done_without_deliverable_is_rejected(api, harness):
    r = call(api, harness, 'done', 'final_answer', answer='已完成')
    assert not r.ok and r.code == 'COMPLETION_REJECTED'
    assert harness.state.status != 'COMPLETED'


def test_h2_edit_after_passing_tests_invalidates_test_evidence(api, harness):
    call(api, harness, 'p', 'submit_plan', steps=['修复', '测试'])
    call(api, harness, 'r', 'read_file', path='src/calculator.py')
    write_approved(api, harness, 'w', 'src/calculator.py', 'def add(a, b):\n    return a + b\n')
    assert call(api, harness, 't', 'run_tests', command='pytest').data['exit_code'] == 0
    write_approved(api, harness, 'w2', 'src/calculator.py', 'def add(a, b):\n    return a - b\n')
    write_approved(api, harness, 'report', 'artifacts/report.md', '已修复')
    assert call(api, harness, 'f', 'final_answer', answer='完成').code == 'COMPLETION_REJECTED'


def test_report_does_not_invalidate_source_test_version(api, harness):
    call(api, harness, 'p', 'submit_plan', steps=['修复', '测试'])
    call(api, harness, 'r', 'read_file', path='src/calculator.py')
    write_approved(api, harness, 'w', 'src/calculator.py', 'def add(a, b):\n    return a + b\n')
    call(api, harness, 't', 'run_tests', command='pytest')
    write_approved(api, harness, 'report', 'artifacts/report.md', '已修复；pytest 通过')
    assert call(api, harness, 'f', 'final_answer', answer='完成').ok
    assert harness.state.status == 'COMPLETED'


def test_h3_max_rounds_stops_without_forced_success(api, repo, tmp_path):
    h = api.Harness.create(repo, tmp_path / 'round', task='解释', kind='code_understanding', max_rounds=1)
    s = h.run(api.FakeModel([('read_file', {'path': 'src/calculator.py'})]))
    assert s.status == 'STOPPED' and s.stop_reason == 'MAX_ROUNDS'
    assert s.rounds == 1


def test_h3_tool_and_token_budgets_stop_explicitly(api, repo, tmp_path):
    for budget in [{'max_calls': 0}, {'max_tokens': 1}]:
        h = api.Harness.create(repo, tmp_path / str(next(iter(budget))), task='解释', kind='code_understanding', **budget)
        s = h.run(api.FakeModel([('read_file', {'path': 'src/calculator.py'})]))
        assert s.status == 'STOPPED' and s.stop_reason in {'TOOL_BUDGET', 'TOKEN_BUDGET'}
        assert s.handler_calls == 0


def test_deliverable_is_in_reviewable_diff(api, harness):
    write_approved(api, harness, 'report-diff', 'artifacts/report.md', '# 说明\n可复核的交付物\n')
    assert '+可复核的交付物' in harness.diff()


def test_reading_own_report_does_not_count_as_understanding_source(api, repo, tmp_path):
    h = api.Harness.create(repo, tmp_path / 'self-report', task='解释代码', kind='code_understanding')
    call(api, h, 'plan', 'submit_plan', steps=['阅读源码', '说明'])
    write_approved(api, h, 'report', 'artifacts/report.md', '模型声称理解了代码')
    call(api, h, 'read-report', 'read_file', path='artifacts/report.md')
    assert call(api, h, 'final', 'final_answer', answer='已完成').code == 'COMPLETION_REJECTED'
