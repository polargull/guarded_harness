"""本地可信仓库的路径、固定命令和进程边界；不是 OS 级恶意代码隔离器。"""
import hashlib
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from .checkpoint import digest


class SandboxDenied(ValueError):
    pass


SENSITIVE = {'.git', '.ssh', '.aws', '.codex', '.agents', '.gnupg', '.venv', 'node_modules'}
IGNORED = {'__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'}


def sensitive(parts):
    return any(p.casefold() in SENSITIVE or p.casefold().startswith('.env') for p in parts)


class Sandbox:
    def __init__(self, root, commands=None, timeout=15.0, output_limit=32768, file_limit=262144):
        self.root = Path(root).resolve(strict=True)
        self.commands = commands if commands is not None else {
            'pytest': [sys.executable, '-I', '-m', 'pytest', '-q', '-p', 'no:cacheprovider']}
        self.timeout = timeout
        self.output_limit = output_limit
        self.file_limit = file_limit
        for argv in self.commands.values():
            if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                raise ValueError('commands must be trusted fixed argv lists')
            if not Path(argv[0]).is_absolute():
                raise ValueError('executable must be absolute')

    def path(self, value, write=False):
        if not isinstance(value, str) or not value or '\x00' in value:
            raise SandboxDenied('invalid path')
        rel = Path(value)
        if rel.is_absolute() or sensitive(rel.parts):
            raise SandboxDenied('absolute or sensitive path')
        target = (self.root / rel).resolve()
        if not target.is_relative_to(self.root) or sensitive(target.relative_to(self.root).parts):
            raise SandboxDenied('resolved path escapes workspace or targets sensitive directory')
        if write and (not target.relative_to(self.root).parts or target.relative_to(self.root).parts[0] not in {'src', 'artifacts'}):
            raise SandboxDenied('writes allowed only under src/ or artifacts/')
        return target

    def validate(self, name, args):
        if 'path' in args:
            self.path(args['path'], write=name == 'write_file')
        if name == 'run_tests' and args['command'] not in self.commands:
            raise SandboxDenied('test command is not in configured allowlist')
        if name == 'write_file' and len(args['content'].encode()) > self.file_limit:
            raise SandboxDenied('file exceeds size limit')

    def read_file(self, path):
        target = self.path(path)
        if target.stat().st_size > self.file_limit:
            raise SandboxDenied('file exceeds size limit')
        return {'path': path, 'content': target.read_text(), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}

    def list_files(self, path):
        target = self.path(path)
        if not target.is_dir():
            raise SandboxDenied('not a directory')
        return {'files': sorted(str(p.relative_to(self.root)) for p in target.iterdir()
                                if not sensitive(p.relative_to(self.root).parts) and not p.is_symlink())[:1000]}

    def write_file(self, path, content):
        target = self.path(path, write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 不跟随最终组件的软链接；执行时重新验证真实落点。
        self.path(path, write=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0), 0o644)
        with os.fdopen(fd, 'w') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        return {'path': path, 'sha256': hashlib.sha256(content.encode()).hexdigest()}

    def files(self, source_only=False):
        result = {}
        for base, dirs, files in os.walk(self.root, followlinks=False):
            for name in dirs:
                p = Path(base) / name
                if p.is_symlink() and not sensitive(p.relative_to(self.root).parts):
                    if not (source_only and Path(base) == self.root and name == 'artifacts'):
                        result[str(p.relative_to(self.root))] = {'symlink': os.readlink(p)}
            dirs[:] = sorted(d for d in dirs if d not in IGNORED and not sensitive([d])
                             and not (Path(base) / d).is_symlink()
                             and not (source_only and Path(base) == self.root and d == 'artifacts'))
            for name in sorted(files):
                p = Path(base) / name
                rel = str(p.relative_to(self.root))
                if sensitive(p.relative_to(self.root).parts) or name.endswith('.pyc'):
                    continue
                if p.is_symlink():
                    result[rel] = {'symlink': os.readlink(p)}
                else:
                    result[rel] = {'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
        return result

    def fingerprint(self, source_only=False):
        return digest(self.files(source_only))

    def baseline(self, source_only=False):
        result = {}
        for rel in self.files(source_only=source_only):
            p = self.root / rel
            if not p.is_symlink() and p.stat().st_size <= self.file_limit:
                try:
                    result[rel] = p.read_text()
                except UnicodeError:
                    pass
        return result

    @staticmethod
    def _kill_group(proc):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()

    def run(self, command, cancel=None):
        if command not in self.commands:
            raise SandboxDenied('test command is not in configured allowlist')
        # -I 会忽略 PYTHONDONTWRITEBYTECODE。独立缓存前缀确保旧 .pyc 不能
        # 在相同 mtime / 文件大小的快速修复中冒充当前源码；-B 禁止生成缓存。
        with tempfile.TemporaryDirectory(prefix='guarded-pycache-') as cache:
            argv = list(self.commands[command])
            if Path(argv[0]).resolve() == Path(sys.executable).resolve():
                argv[1:1] = ['-B', '-X', 'pycache_prefix=' + cache]
            return self._run_argv(command, argv, cancel)

    def _run_argv(self, command, argv, cancel):
        if os.name != 'posix':
            raise SandboxDenied('process group isolation requires POSIX')
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
               'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
        proc = subprocess.Popen(argv, cwd=self.root, env=env, shell=False,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
        output = bytearray()
        reason = 'EXITED'
        start = time.monotonic()
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ)
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    reason = 'CANCELLED'
                    break
                if time.monotonic() - start >= self.timeout:
                    reason = 'TIMEOUT'
                    break
                for key, _ in sel.select(timeout=0.03):
                    data = os.read(key.fileobj.fileno(), 8192)
                    if not data:
                        sel.unregister(key.fileobj)
                        continue
                    room = self.output_limit - len(output)
                    output.extend(data[:room])
                    if len(data) > room:
                        reason = 'OUTPUT_LIMIT'
                        break
                if reason != 'EXITED' or (proc.poll() is not None and not sel.get_map()):
                    break
        finally:
            # 即使主进程已退出，也清理仍持有文件描述符或后台运行的后代。
            self._kill_group(proc)
            sel.close()
            proc.stdout.close()
        return {'command': command, 'argv': argv, 'exit_code': proc.returncode,
                'reason': reason, 'output': output.decode('utf-8', errors='replace'),
                'duration_seconds': round(time.monotonic() - start, 4)}
