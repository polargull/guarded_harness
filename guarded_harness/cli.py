"""可复现实验与受控运行 CLI；模型凭据只从环境读取。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from .harness import Harness
from .fake_model import FakeModel


def fake_script(kind):
    plan = ('submit_plan', {'steps': ['阅读源码和测试', '执行测试复现', '完成任务', '输出报告并验收']})
    read = ('read_file', {'path': 'src/calculator.py'})
    if kind == 'code_understanding':
        return [plan, read, ('read_file', {'path': 'tests/test_calculator.py'}),
                ('write_file', {'path': 'artifacts/report.md', 'content': '# 代码理解报告\n\n'
                    'calculator.add(a, b) 声明返回两数之和，当前实现却返回 a - b。\n'
                    '入口是 src/calculator.py 的 add，测试覆盖正数、负数与零。\n'
                    '建议将返回表达式修改为 a + b，并运行现有测试；本次代码理解任务未修改源码。\n'}),
                ('final_answer', {'answer': '已完成代码理解，证据与说明见 artifacts/report.md。'})]
    return [plan, read, ('read_file', {'path': 'tests/test_calculator.py'}), ('run_tests', {'command': 'pytest'}),
        ('write_file', {'path': 'src/calculator.py', 'content': '"""一个用于练习测试修复的小型计算模块。"""\n\n\ndef add(a, b):\n    """返回两个数的和。"""\n    return a + b\n'}),
        ('run_tests', {'command': 'pytest'}),
        ('write_file', {'path': 'artifacts/report.md', 'content': '# 测试修复报告\n\n'
            '根因：add 使用减法，与函数契约及测试预期不符。\n'
            '修改：仅将 src/calculator.py 中的 a - b 改为 a + b，测试文件未修改。\n'
            '验证：先复现两个失败，修复后固定 pytest 命令得到 3 passed。\n'}),
        ('final_answer', {'answer': '源码修复完成，当前版本 3 项测试通过，报告已交付。'})]


def model_for(provider, kind, model_id):
    if provider == 'fake':
        return FakeModel(fake_script(kind))
    from smolagents import OpenAIServerModel
    key = os.environ.get('DEEPSEEK_OPENAI_API_KEY')
    base = os.environ.get('DEEPSEEK_OPENAI_BASE_URL')
    if not key or not base:
        raise ValueError('请配置 DEEPSEEK_OPENAI_API_KEY 与 DEEPSEEK_OPENAI_BASE_URL；不会将凭据写入记录')
    return OpenAIServerModel(model_id=model_id, api_base=base, api_key=key,
        client_kwargs={'timeout': 60, 'max_retries': 1}, temperature=0, max_tokens=2400, tool_choice='auto')


def summary(h):
    s = h.state
    return {'pid': os.getpid(), 'run_id': s.run_id, 'status': s.status, 'stop_reason': s.stop_reason,
            'rounds': s.rounds, 'tool_calls': s.tool_calls, 'handler_calls': s.handler_calls,
            'tokens': s.tokens, 'pending': list(s.pending)}


def export_run(h, output):
    output = Path(output)
    (output / 'run.json').write_text(json.dumps(h.state.to_dict(), ensure_ascii=False, indent=2) + '\n')
    (output / 'workspace.diff').write_text(h.diff())
    lines = ['# 实际运行记录', '', f'- Run ID：`{h.state.run_id}`',
        f'- 状态：`{h.state.status}`；停止原因：`{h.state.stop_reason}`',
        f'- 模型轮数：{h.state.rounds}；工具尝试：{h.state.tool_calls}；handler 实际调用：{h.state.handler_calls}',
        f'- Token 计数：{h.state.tokens}', '', '## 工具执行轨迹', '']
    for i, step in enumerate(h.state.steps, 1):
        c, r = step['call'], step['result']
        lines += [f'### {i}. {c["name"]} / {c["id"]}', '', '```json',
                  json.dumps({'arguments': c['arguments'], 'result': r}, ensure_ascii=False, indent=2), '```', '']
    lines += ['## 当前版本 Diff', '', '```diff', h.diff(), '```', '']
    (output / 'complete-run.md').write_text('\n'.join(lines))


def demo(args):
    output = Path(args.output).resolve()
    if output.exists():
        raise ValueError('演示输出目录已存在；请选择新目录，避免覆盖证据')
    fixture = Path(__file__).resolve().parents[1] / 'examples' / 'tiny_repo'
    output.mkdir(parents=True)
    shutil.copytree(fixture, output / 'workspace', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache'))
    task = ('理解 calculator 模块，写出文件职责、入口、现有缺陷和验证建议。保持源码不变，报告写入 artifacts/report.md。'
            if args.kind == 'code_understanding' else
            '修复 calculator.add 的错误。先读源码与现有测试并运行测试复现，然后只修改 src/calculator.py，'
            '重跑现有完整测试集并确认通过。不得修改测试或测试配置。将根因、修改与实际测试结果写入 artifacts/report.md。')
    h = Harness.create(output / 'workspace', output / 'run', task, args.kind, max_rounds=25, max_tokens=60000)
    processes = []
    started = datetime.now(timezone.utc).isoformat()
    for attempt in range(8):
        command = [sys.executable, '-m', 'guarded_harness', 'worker', str(h.run_dir),
                   '--provider', args.provider, '--model-id', args.model_id]
        completed = subprocess.run(command, capture_output=True, text=True)
        (output / f'process-{attempt + 1}.txt').write_text(completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError(f'worker exited {completed.returncode}; see process-{attempt + 1}.txt')
        record = json.loads(completed.stdout.strip().splitlines()[-1])
        processes.append(record)
        h = Harness.resume(output / 'run')
        # 每一次暂停保存审批前快照，证明目标写入尚未执行。
        shutil.copy2(h.checkpoint.path, output / f'boundary-{attempt + 1}.json')
        if h.state.status != 'WAITING_APPROVAL':
            break
        if not args.approve_demo_writes:
            break
        for cid in list(h.state.pending):
            call = h.state.pending[cid]
            h.audit.append('Approval', 'DEMO_HOST_PREAUTHORIZATION', 'REVIEWED', cid,
                actor='human:demo-operator', path=call['arguments']['path'],
                message='演示宿主依据 --approve-demo-writes 授权审批此独立示例仓库的写入；模型无审批工具')
            h.approve(cid, actor='human:demo-operator')
    export_run(h, output)
    events = h.audit.read()
    writes = [e for e in events if e['component'] == 'Execution' and e['rule'] == 'HANDLER_STARTED'
              and e['details'].get('tool') == 'write_file']
    recovery = {'provider': args.provider, 'model_id': args.model_id if args.provider != 'fake' else 'deterministic-fake-model',
                'started_at_utc': started, 'processes': processes,
                'distinct_worker_pids': sorted({p['pid'] for p in processes}),
                'write_execution_ids': [e['tool_call_id'] for e in writes],
                'writes_executed_once': len(writes) == len({e['tool_call_id'] for e in writes}),
                'audit_chain_valid': h.audit.verify(), 'final': summary(h)}
    (output / 'recovery.json').write_text(json.dumps(recovery, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(recovery, ensure_ascii=False, indent=2))
    return 0 if h.state.status in {'COMPLETED', 'WAITING_APPROVAL'} else 1


def main():
    parser = argparse.ArgumentParser(description='作业三：可恢复 Guarded Coding Harness')
    commands = parser.add_subparsers(dest='command', required=True)
    d = commands.add_parser('demo', help='复制独立示例仓库，跨进程运行并保存证据')
    d.add_argument('--output', required=True)
    d.add_argument('--kind', choices=['code_understanding', 'test_fix'], default='test_fix')
    d.add_argument('--provider', choices=['fake', 'deepseek'], default='fake')
    d.add_argument('--model-id', default='deepseek-chat')
    d.add_argument('--approve-demo-writes', action='store_true', help='授权宿主审批此独立演示仓库的写入')
    w = commands.add_parser('worker', help='从 Checkpoint 在当前进程继续运行')
    w.add_argument('run_dir')
    w.add_argument('--provider', choices=['fake', 'deepseek'], default='fake')
    w.add_argument('--model-id', default='deepseek-chat')
    a = commands.add_parser('approve', help='由宿主确认一个具体待审批调用')
    a.add_argument('run_dir')
    a.add_argument('call_id')
    a.add_argument('--actor', required=True, help='human:<identity>，身份由可信宿主提供')
    s = commands.add_parser('status')
    s.add_argument('run_dir')
    args = parser.parse_args()
    try:
        if args.command == 'demo':
            return demo(args)
        h = Harness.resume(args.run_dir)
        if args.command == 'approve':
            h.approve(args.call_id, args.actor)
        elif args.command == 'worker':
            h.run(model_for(args.provider, h.state.kind, args.model_id))
        print(json.dumps(summary(h), ensure_ascii=False))
        return 1 if h.state.status == 'STOPPED' else 0
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 2
