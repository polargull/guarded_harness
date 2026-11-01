import pytest
from conftest import call

@pytest.mark.parametrize('arguments', [{}, {'path': 1}, {'path': 'src/calculator.py', 'extra': True}])
def test_g1_invalid_arguments_never_enter_handler(api, harness, arguments):
    r = harness.gate.invoke(api.ToolCall('bad', 'read_file', arguments))
    assert not r.ok and r.code == 'INVALID_ARGUMENTS'
    assert harness.state.handler_calls == 0

def test_g2_third_identical_call_is_blocked(api, harness):
    results = [call(api, harness, str(i), 'read_file', path='src/calculator.py') for i in range(3)]
    assert [r.ok for r in results] == [True, True, False]
    assert results[-1].code == 'REPEATED_ACTION'
    assert harness.state.handler_calls == 2

def test_g3_unapproved_write_waits_without_changing_file(api, harness, repo):
    before = (repo / 'src/calculator.py').read_bytes()
    r = call(api, harness, 'write-1', 'write_file', path='src/calculator.py', content='fixed')
    assert r.code == 'APPROVAL_REQUIRED'
    assert harness.state.status == 'WAITING_APPROVAL'
    assert (repo / 'src/calculator.py').read_bytes() == before

def test_g4_approved_call_executes_exactly_once(api, harness, repo):
    c = api.ToolCall('w', 'write_file', {'path': 'src/calculator.py', 'content': 'fixed'})
    harness.gate.invoke(c)
    harness.approve('w', actor='human:teacher')
    assert harness.gate.invoke(c).ok
    assert harness.gate.invoke(c).code == 'DUPLICATE_CALL'
    assert harness.state.handler_calls == 1
    assert (repo / 'src/calculator.py').read_text() == 'fixed'

def test_approval_is_bound_to_parameters_and_cannot_be_self_granted(api, harness):
    call(api, harness, 'w', 'write_file', path='artifacts/report.md', content='a')
    with pytest.raises(ValueError):
        harness.approve('w', actor='model')
    harness.approve('w', actor='human:owner')
    r = call(api, harness, 'w', 'write_file', path='artifacts/report.md', content='b')
    assert not r.ok

def test_code_understanding_cannot_modify_source(api, repo, tmp_path):
    h = api.Harness.create(repo, tmp_path / 'understand', task='解释代码', kind='code_understanding')
    assert call(api, h, 'w', 'write_file', path='src/calculator.py', content='x').code == 'PERMISSION_DENIED'
