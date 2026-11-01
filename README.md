# 作业三：基于 smolagents 的 Guarded Coding Harness

这个项目为 `smolagents.ToolCallingAgent` 装配可恢复的执行门禁，支持**代码理解**和**测试修复**两类仓库任务。模型只决定下一步；程序控制校验、权限、审批、路径/命令边界、预算、持久化、审计与最终完成状态。

截图中的五项最终提交材料，对应位置见 [提交清单.md](提交清单.md)。

## 快速运行

环境要求：Python 3.11+、macOS / Linux（使用 POSIX 文件锁和进程组）。本次实际验证环境为 Python 3.13.3、smolagents 1.24.0。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
```

`requirements.lock` 保存本次实际安装版本。如需复现完全相同依赖，可改用 `.venv/bin/python -m pip install -r requirements.lock`。

运行确定性演示，不需要 API Key：

```bash
.venv/bin/python -m guarded_harness demo \
  --provider fake --kind test_fix \
  --output /tmp/guarded-fix-demo --approve-demo-writes

.venv/bin/python -m guarded_harness demo \
  --provider fake --kind code_understanding \
  --output /tmp/guarded-understanding-demo --approve-demo-writes
```

演示会复制一份独立 Calculator 仓库。每次审批暂停后启动**新的 worker 子进程**继续执行，保存原始 PID、计数和边界 Checkpoint。`--approve-demo-writes` 表示宿主获得对这份独立示例仓库的预授权，审批仍经过 `Harness.approve()`；模型没有审批工具。不要将这个选项作为任意生产仓库的自动审批策略。

人工逐条审批的运行方式：

```bash
.venv/bin/python -m guarded_harness demo --provider fake \
  --kind test_fix --output /tmp/guarded-manual-demo
.venv/bin/python -m guarded_harness status /tmp/guarded-manual-demo/run
# 将 status 中具体的待审批 ID 填入下方；先检查 checkpoint.json 中对应路径及内容。
.venv/bin/python -m guarded_harness approve /tmp/guarded-manual-demo/run call-5 --actor human:owner
.venv/bin/python -m guarded_harness worker /tmp/guarded-manual-demo/run --provider fake
```

再次出现 `WAITING_APPROVAL` 时重复人工检查、approve、worker。不要直接对底层 `agent.run()` 发起任务，唯一业务运行入口是 `Harness.run()`。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `guarded_harness/gate.py` | 所有工具的唯一执行入口；严格参数、权限、重复动作、审批和写前账本 |
| `guarded_harness/sandbox.py` | 真实路径/敏感目录检查、固定 argv、净化环境、输出边界、超时和进程组终止 |
| `guarded_harness/state_store.py` | Run、计划、步骤、历史、证据、预算、审批、账本与 StopReason |
| `guarded_harness/checkpoint.py` | JSON 原子替换、fsync、校验和、revision 乐观并发校验 |
| `guarded_harness/audit.py` | append-only JSONL、actor/rule/decision/run_id/tool_call_id、哈希链 |
| `guarded_harness/harness.py` | 装配模型、smolagents、Gate、公开步骤回调、恢复和预算控制 |
| `guarded_harness/completion.py` | 当前版本证据与最终交付物验收 |
| `guarded_harness/fake_model.py` | 实现真实 smolagents Model 协议的确定性模型 |
| `guarded_harness/cli.py` | 演示、审批、状态查看、子进程继续运行与证据导出 |
| `tests/` | 作业规定的六个测试文件，覆盖正常和失败路径 |
| `examples/tiny_repo/` | 故意带有加法缺陷的练习仓库，原始测试不允许被模型修改 |
| `evidence/` | 实际测试输出、完整轨迹、审批边界、审计、恢复记录与产物 Diff |

## 不改写 smolagents 核心的集成方式

`_GateAgent` 继承 `ToolCallingAgent`，只覆盖公开的 `execute_tool_call()` 执行边界，将工具统一委托给 Gate。推理、工具调用解析、模型错误处理、步骤循环和历史消息转换仍使用原库实现。模型不会获得 Python 执行器、shell 或默认扩展工具。

Harness 消费 `agent.run(stream=True)` 的公开事件，在工具执行前取得真实 `tool_call_id`；通过公开 `step_callbacks` 保存模型可见历史。`_ToolSchema` 只提供工具声明，不能独立执行。`_BudgetModel` 包装真实模型计数并控制预算；每轮必须且只能有一个工具调用，多调用批次整批拒绝，避免并发写入和 final_answer 混入其他动作。

提供六种工具：`submit_plan`、`list_files`、`read_file`、`write_file`、`run_tests`、`final_answer`。所有工具先经 Gate，错误返回包含 `ok/code/message/data`。即使模型声称任务完成，最终验收失败也不能进入 `COMPLETED`。

## 审批与执行语义

1. 参数字段和类型严格匹配，错误或未知工具不会进入 handler。
2. 相同工具与参数连续第三次调用在执行前拒绝；已处理的调用 ID 禁止再次执行。
3. 写入只允许 `src/` 和 `artifacts/`；代码理解任务只能写 `artifacts/`。测试文件不可写。
4. 所有写入先保存待办，进入 `WAITING_APPROVAL`，此时目标文件不变。
5. 宿主审批绑定 Run、调用 ID、工具参数和工作区摘要；一次消费。`human:<identity>` 是可信宿主传入的身份字段，本示例不实现真实 IAM 身份认证。
6. 执行前将 `STARTED` 账本和审批消费状态持久化；完成后原子保存 `SUCCEEDED` 与结果。未知状态不会靠自动重试猜测。

状态转换：

```text
RUNNING -> WAITING_APPROVAL ->（宿主审批）-> RUNNING -> COMPLETED
   |                                                    
   +-> STOPPED          轮数/工具/Token 预算、取消或模型错误
   +-> WAITING_REVIEW   工作区变化或 UNKNOWN_RESULT
```

`WAITING_APPROVAL` 是安全暂停，不代表完成。`STOPPED` 和 `WAITING_REVIEW` 不自动继续；人工核对未知写入后，应确认当前仓库状态并建立新的 Run，原始账本和审计保留供核对。

## Checkpoint 与恢复

每个安全边界后保存 `checkpoint.json`，包含完整业务状态。控制目录必须位于工作区之外，因此模型不能读取或修改自己的审批和审计文件。

- 正常恢复：还原计划、历史、证据、计数、审批和已处理 ID；恢复后只执行已批准且尚未 `STARTED` 的待办。
- 崩溃恢复：若 Checkpoint 中仍为 `STARTED`，转为 `UNKNOWN_RESULT` 并进入 `WAITING_REVIEW`。即使磁盘上已看到写入，也不把它猜测为成功，更不会自动重放。
- 工作区变化：文件内容或目录软链接落点变化会使旧证据和审批失效，停止等待人工核对。
- 多实例：`run/approve/resume` 使用同一运行锁；获得锁后重新加载 Checkpoint。保存使用 revision 校验，旧对象不能覆盖更新状态；低层直接调用过期 Gate 会收到 `stale checkpoint revision`。
- Checkpoint SHA-256 检测意外损坏；审计哈希链用于发现记录损坏。二者不声称能抵御拥有宿主磁盘写权限的攻击者。

真实崩溃实验：

```bash
.venv/bin/python scripts/record_crash_recovery.py --output /tmp/guarded-crash-demo
```

子进程完成写入并 fsync 后调用 `os._exit(73)`，刻意绕过结果保存。恢复记录应显示 `UNKNOWN_RESULT`、`WAITING_REVIEW`、handler 总调用 1 次，重放申请被拒绝。

## Sandbox 的具体边界

路径检查同时验证相对路径、真实落点与敏感目录，包含大小写变体及软链接。禁止 `.env*`、`.git`、`.ssh`、`.aws`、`.codex` 等路径。`run_tests` 只接受配置键 `pytest`，实际 argv 由宿主固定，`shell=False`；模型不能追加参数。子进程环境使用最小白名单，不继承 API Key。输出、文件大小和运行时长均有限制。

每次 Python 测试使用独立字节码缓存前缀并禁写缓存，避免同长度、同 mtime 的源码修改命中旧 `.pyc`。超时、取消、超量输出或用户 KeyboardInterrupt 均清理整个进程组。

这是**可信仓库测试代码的本地执行边界**，不是容器或操作系统级恶意代码隔离。固定 pytest 命令会执行仓库测试；若仓库本身不可信，应在容器/VM 中运行整个 Harness，并关闭网络与限制挂载目录。本项目不会把路径检查表述为系统调用级隔离。

## 最终完成验收

共同要求：非空计划、真实源码阅读证据、程序成功写入的非空 `artifacts/report.md`，且报告当前哈希与证据一致；无待审批或未知执行结果。

- **代码理解**：至少一个当前源码版本的源码文件阅读证据；自读报告、只读普通 Markdown 都不算理解代码。
- **测试修复**：存在源码修复 Diff，且最近测试正常退出、退出码为 0、测试证据摘要与当前源码/测试版本一致。

报告写入不会使源码测试证据过期；测试通过后再修改源码则必须重跑。停止原因包括 `MAX_ROUNDS`、`TOOL_BUDGET`、`TOKEN_BUDGET`、`WORKSPACE_CHANGED`、`UNKNOWN_RESULT`、`CANCELLED` 等，不会把预算耗尽时库生成的兜底回答认定为成功。

## 真实模型运行

真实模型通过 `OpenAIServerModel` 使用兼容 OpenAI 的 DeepSeek 接口，读取宿主已有环境变量，不把凭据写入源码或证据。只有获得外部服务调用授权后才运行：

```bash
# 事先在终端配置 DEEPSEEK_OPENAI_API_KEY 和 DEEPSEEK_OPENAI_BASE_URL。
.venv/bin/python -m guarded_harness demo --provider deepseek \
  --kind test_fix --model-id deepseek-chat \
  --output /tmp/guarded-real-demo --approve-demo-writes
```

会发送独立示例仓库的工具输出、测试结果和任务上下文。`FakeModel` 与真实模型记录分别保存，验收结果以 `evidence/验收报告.md` 为准。
