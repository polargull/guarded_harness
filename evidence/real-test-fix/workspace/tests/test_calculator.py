import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from calculator import add


def test_positive_numbers():
    assert add(2, 3) == 5


def test_negative_numbers():
    assert add(-2, -3) == -5


def test_zero():
    assert add(7, 0) == 7
