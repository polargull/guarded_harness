# Checkpoint 恢复记录

这份记录对应最终提交要求中的“一份 Checkpoint 恢复记录”。以下数据整理自实际运行的 JSON 和 worker 输出，原始文件均保留在同目录下。

## 正常中断后换进程继续执行

- 模型：DeepSeek `deepseek-chat`，真实 API。
- Run ID：`fa4b2edddfde4dd587dde9a1beaf2e73`。
- 原始恢复记录：[real-test-fix/recovery.json](real-test-fix/recovery.json)。

| 阶段 | worker PID | 保存后的状态 | 累计模型轮数 | 累计工具尝试 | 累计 handler 执行 |
| --- | --- | --- | --- | --- | --- |
| 首个进程运行，源码写入等待审批 | 21422 | WAITING_APPROVAL | 9 | 9 | 7 |
| 宿主审批后，新进程恢复并完成源码写入和测试；报告写入等待审批 | 21511 | WAITING_APPROVAL | 11 | 12 | 9 |
| 宿主审批后，再换进程恢复、写入报告并通过最终验收 | 21531 | COMPLETED | 13 | 15 | 12 |

三个 PID 不同，但 Run ID 始终一致。轮数、工具预算计数与实际执行计数持续累计，没有从零开始。

两个写入调用 ID：

1. 源码修复：`call_00_VaWQdyMfkv8JeKosVtpn1265`。
2. 修复报告：`call_00_3frQWm2zglqahLqJsVS98210`。

原始记录中 `writes_executed_once` 为 `true`，`audit_chain_valid` 为 `true`。两个调用在最终 Checkpoint 中均为 `SUCCEEDED`，没有重放已完成写入。

对应原始文件：

- [boundary-1.json](real-test-fix/boundary-1.json)：第一个进程暂停，保存待审批源码写入、计划、证据和预算。
- [boundary-2.json](real-test-fix/boundary-2.json)：源码写入已完成，报告写入待审批。
- [boundary-3.json](real-test-fix/boundary-3.json)：最终 COMPLETED 状态，待审批列表为空。
- [process-1.txt](real-test-fix/process-1.txt)、[process-2.txt](real-test-fix/process-2.txt)、[process-3.txt](real-test-fix/process-3.txt)：原始 worker 输出。
- [run/checkpoint.json](real-test-fix/run/checkpoint.json)：最终 Checkpoint。
- [run/audit.jsonl](real-test-fix/run/audit.jsonl)：可按 Run ID 与调用 ID 关联审批、恢复与执行结果。

## 写入已发生但结果未保存时的恢复

额外进行了一次真实崩溃实验：子进程完成写入并 fsync 后，立即调用 `os._exit(73)`，刻意不保存 handler 结果。

| 检查项 | 实际结果 |
| --- | --- |
| 崩溃子进程退出码 | 73 |
| 崩溃前已保存的调用状态 | STARTED |
| 恢复后的调用状态 | UNKNOWN_RESULT |
| 恢复后的 Run 状态 | WAITING_REVIEW |
| 再次申请执行同一调用 | 拒绝，返回 UNKNOWN_RESULT |
| handler 累计执行次数 | 1 |
| 审计哈希链 | 校验通过 |

对应原始文件：

- [crash-recovery/recovery.json](crash-recovery/recovery.json)：实验结果。
- [crash-recovery/before-execution.json](crash-recovery/before-execution.json)：执行前已保存的审批与待办状态。
- [crash-recovery/after-crash-before-resume.json](crash-recovery/after-crash-before-resume.json)：崩溃后、恢复前，账本仍为 STARTED。
- [crash-recovery/run/checkpoint.json](crash-recovery/run/checkpoint.json)：恢复为 UNKNOWN_RESULT 的状态。
- [crash-recovery/run/audit.jsonl](crash-recovery/run/audit.jsonl)：崩溃恢复与拒绝重放的审计。

以上正常恢复和崩溃恢复分别验证：已完成写入不重复执行，未知执行结果不自动重放。
