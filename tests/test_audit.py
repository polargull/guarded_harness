from conftest import call


def test_a1_denial_has_rule_decision_actor_and_tool_call_id(api, harness):
    call(api, harness, 'bad', 'read_file', path='../secret')
    event = next(e for e in harness.audit.read() if e['decision'] == 'DENY')
    assert all(event[k] for k in ['run_id', 'tool_call_id', 'rule', 'decision', 'actor'])
    assert event['tool_call_id'] == 'bad'


def test_a2_append_only_and_components_can_be_correlated(api, harness):
    prefix = harness.audit.path.read_bytes()
    call(api, harness, 'w', 'write_file', path='artifacts/report.md', content='report')
    harness.approve('w', actor='human:owner')
    call(api, harness, 'w', 'write_file', path='artifacts/report.md', content='report')
    assert harness.audit.path.read_bytes().startswith(prefix)
    events = [e for e in harness.audit.read() if e['tool_call_id'] == 'w']
    assert {'Gate', 'Sandbox', 'State', 'Approval', 'Execution'} <= {e['component'] for e in events}
    assert {e['run_id'] for e in events} == {harness.state.run_id}
    assert harness.audit.verify()
