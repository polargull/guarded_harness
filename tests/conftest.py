from pathlib import Path
import importlib
import pytest

@pytest.fixture
def api():
    return importlib.import_module('guarded_harness')

@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    (root / 'src').mkdir(parents=True)
    (root / 'tests').mkdir()
    (root / 'artifacts').mkdir()
    (root / 'src' / 'calculator.py').write_text('def add(a, b):\n    return a - b\n')
    (root / 'tests' / 'test_calculator.py').write_text(
        'import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parents[1] / "src"))\n'
        'from calculator import add\ndef test_add():\n    assert add(2, 3) == 5\n')
    return root

@pytest.fixture
def harness(api, repo, tmp_path):
    return api.Harness.create(repo, tmp_path / 'run', task='修复加法并提交修复说明', kind='test_fix')

def call(api, harness, id, name, **arguments):
    return harness.gate.invoke(api.ToolCall(id, name, arguments))
