import os
import sys
import threading
import time
import pytest
from conftest import call

@pytest.mark.parametrize('path', ['artifacts/../../.env', '/etc/passwd', '.git/config', '.env', 'src/.env.local'])
def test_s1_sensitive_and_escaping_paths_never_enter_handler(api, harness, path):
    r = call(api, harness, 'escape', 'read_file', path=path)
    assert not r.ok and r.code == 'SANDBOX_DENIED'
    assert harness.state.handler_calls == 0

def test_s2_symlink_real_target_is_rejected_and_audited(api, harness, repo, tmp_path):
    secret = tmp_path / 'secret'
    secret.write_text('secret')
    (repo / 'artifacts' / 'link').symlink_to(secret)
    r = call(api, harness, 'link', 'read_file', path='artifacts/link')
    assert r.code == 'SANDBOX_DENIED'
    assert any(e['component'] == 'Sandbox' and e['decision'] == 'DENY' for e in harness.audit.read())

def test_s3_arbitrary_command_is_not_executed(api, harness):
    r = call(api, harness, 'cmd', 'run_tests', command='rm -rf /')
    assert r.code == 'SANDBOX_DENIED'
    assert harness.state.handler_calls == 0

@pytest.mark.parametrize('cancel', [False, True])
def test_s4_timeout_or_cancel_kills_descendants(api, repo, tmp_path, cancel):
    marker = tmp_path / 'escaped-child'
    child = f'import time; from pathlib import Path; time.sleep(0.8); Path({str(marker)!r}).write_text("alive")'
    script = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); time.sleep(5)'
    sb = api.Sandbox(repo, commands={'slow': [sys.executable, '-c', script]}, timeout=0.15 if not cancel else 3)
    event = threading.Event()
    timer = threading.Timer(0.15, event.set)
    if cancel:
        timer.start()
    result = sb.run('slow', cancel=event)
    timer.cancel()
    assert result['reason'] == ('CANCELLED' if cancel else 'TIMEOUT')
    time.sleep(0.9)
    assert not marker.exists()

def test_environment_is_clean_and_output_is_bounded(api, repo, monkeypatch):
    monkeypatch.setenv('VERY_SECRET_API_KEY', 'must-not-leak')
    sb = api.Sandbox(repo, commands={'env': [sys.executable, '-c', 'import os; print(os.getenv("VERY_SECRET_API_KEY", "absent"))'], 'noisy': [sys.executable, '-c', 'print("x"*100000)']}, output_limit=256)
    assert sb.run('env')['output'].strip() == 'absent'
    result = sb.run('noisy')
    assert result['reason'] == 'OUTPUT_LIMIT'
    assert len(result['output'].encode()) <= 256


def test_current_source_runs_even_if_old_bytecode_has_same_timestamp_and_size(api, repo):
    sb = api.Sandbox(repo)
    assert sb.run('pytest')['exit_code'] != 0
    path = repo / 'src/calculator.py'
    import py_compile
    py_compile.compile(str(path), doraise=True)
    stat = path.stat()
    path.write_text(path.read_text().replace('a - b', 'a + b'))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    result = sb.run('pytest')
    assert result['exit_code'] == 0, result['output']


def test_sensitive_paths_are_case_insensitive(api, repo, tmp_path):
    (repo / '.env').write_text('SECRET=do-not-read')
    h = api.Harness.create(repo, tmp_path / 'case', task='解释源码', kind='code_understanding')
    r = call(api, h, 'case', 'read_file', path='.ENV')
    assert r.code == 'SANDBOX_DENIED'
    assert h.state.handler_calls == 0
