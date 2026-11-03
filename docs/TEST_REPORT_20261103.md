# 测试报告（2026-11-03 晚间补充轮）

- 记录时间：2026-11-03 18:06（Asia/Shanghai）
- 被测代码：当前工作树（在 `784cf36` 之上的未提交改动，见进度文档）
- 环境：Windows、Python 3.12（`backend/.venv`）、SQLite、Node.js、本地 ComfyUI 8188（本轮渲染后已停止）、本地 VLM 8116、本地 ASR 8117
- 说明：本报告只记录实际执行过的命令和结果；未执行的项明确标注为未运行。

## 一、后端

### 1. 全量后端测试

命令：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q *> ..\.local-acceptance\real-user-journey-final\logs\full-backend-final2-20261103.log
```

- 收集数量：`1456`（`pytest --collect-only -q -p no:warnings`，见 `logs/collect-only-20261103.log`）
- 运行结果：**`1455 passed` / `1 skipped` / `0 failed` / `PYTEST_EXIT=0`**
- 跳过项：既有 Windows Job Object 依赖标记的 1 项（按输出逐字符统计：`.`×1455、`s`×1，见日志进度行）
- 日志：`.local-acceptance/real-user-journey-final/logs/full-backend-final2-20261103.log`

### 2. 定向回归（本轮最后修改之后执行）

命令：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_advisory_queue.py tests/test_production_series.py tests/test_comfy_completion_window.py -q
```

结果：`51 passed`，`EXIT=0`。

覆盖的关键行为：

- 建议式审片真实执行、超时不阻断、镜头修订后作废、无路由时诚实记录 unavailable（`test_advisory_queue.py`）。
- 系列心跳在数据库写竞争下继续续约；所有镜头已通过但系列所有权证明丢失时，结果可归并为 `COMPLETED`；租约已释放时不再重复释放（`test_production_series.py`）。
- 激活不确定但外部历史已完成时，按已证明存活的受管渲染器采纳结果，`prompt_count` 不增加（绝不重投）；激活所有权不可证明时仍阻断（`test_comfy_completion_window.py`）。
- 项目本地 WorkflowProfile 与冻结修订按精确图身份复用；项目隔离、参数映射/图哈希/模式不一致时拒绝（`test_workflow_profile_scope.py`，本轮回填在 1456 收集数中）。

### 3. Ruff

命令：

```powershell
cd backend
.\.venv\Scripts\python.exe -m ruff check shine tests
```

结果：`All checks passed!`

### 4. 本轮修复的失败复盘

| 现象 | 根因 | 状态 |
|---|---|---|
| `worker_heartbeat_failed error_type=OperationalError` → `COMFY_HEARTBEAT_RECONCILIATION_REQUIRED` | 提交保护在外部上传/提交期间持有 SQLite 写锁，心跳线程 `BEGIN IMMEDIATE` 超时 | 已修复：外部调用移到事务提交之后；心跳对 `database is locked/busy` 容忍并重试 |
| 系列镜头 `PASSED` 后系列仍 `NEEDS_REVIEW / SERIES_OWNERSHIP_LOST` | 系列心跳线程在短暂写竞争下永久停止续约；缺少“租约已释放后的结果归并”分支 | 已修复并有回归 |
| 建议式 VLM `SEMANTIC_REVIEW_INVALID_JSON: STYLE_INPUT_EVIDENCE_NOT_VERIFIED` | advisory 队列把原始 `{artifact_id,sha256}` pin 直接交给只接受 `VerifiedInputEvidence` 的 reviewer | 已修复：引导式建议审片走 `TrackedProvider`；真实重跑后任务 `17536ac0…` 由 `UNAVAILABLE` 恢复为 `COMPLETED` |
| 正式项目首批真实帧被判 `STYLE_INPUT_*` 冲突 | 冻结 Style 为“明亮日光”，故事板为雨夜，文本与画面均冲突 | 已修正为 Style V2（雨夜），镜头 revision 4，真实文本评估 7/7 一致；旧 V1 与首批结果全部保留、不覆盖 |
| 正式项目第二次渲染在 QC 前 worker 进程丢失 | 运行环境 `process_lost`，日志无异常栈；产物已持久化 | **未完成**：见进度文档“当前阻塞”，不得盲重投 |

## 二、前端

### 1. 构建

命令：

```powershell
cd frontend
npm.cmd run build
```

结果：`BUILD_EXIT=0`。产物：`dist/index.html 0.47 kB`、`index-DnTqOWMN.css 56.22 kB`、`index-ncnGV18f.js 474.48 kB`。

日志：`.local-acceptance/real-user-journey-final-v4/evidence/frontend-build-final4-20261103.log`

### 2. 单元/组件测试

命令：

```powershell
cd frontend
npm.cmd test
```

结果：`15 passed` 个测试文件、`90 passed` 个测试，`VITEST_EXIT=0`。

日志：`.local-acceptance/real-user-journey-final-v4/evidence/frontend-vitest-final4-20261103.log`

## 三、真实模型与真实生产证据

- **保留的全通过真实生产**：回归作品三镜系列 `0dd7ad85…`，3/3 镜 `PASSED`、`quality_verification=real`、technical `passed`（ffprobe 全帧解码）；自动交付 `20d6bcda…` `DELIVERED`，成片 `e64ba9fd…`（9,927,251 B，SHA-256 `9039fc30…`）。该证据来自本日主验收，未因本轮修改被重写。
- **VLM 重校准**：最终 pinned review client 下两条真实调用 `09a42818…`（passed，0.98，0 findings）与 `4cfcbcb8…`（failed，0.85，7 findings）；新修订 `d91a16c3…` `PRODUCTION_APPROVED`，旧 `08174d46…` `DEPRECATED`。
- **正式项目**：Style V2 `7c3ed722…`、shot revision 4、真实文本评估 `1422ad65…`（7/7 consistent，`proceed_to_preflight`）、冻结计划 `3187c16d…`（`SHOOT_READY`）；第二次真实渲染外部任务 `52e492e7…` 已完成并采集 artifact `469ee15a…`（3,865,537 B，SHA-256 `4f094edb…`）。**该镜头尚未完成 QC/人工验收，不能计为 PASS。**

## 四、未运行项与边界

- 本轮未重新执行浏览器全流程复验（正式项目当前镜头阻塞，待恢复后再做）。
- 未从本轮最终提交重新构建 Windows 测试包；包构建与入口脚本校验列为后续工作。
- GitHub Actions 仍为 `WAIVED_EXTERNAL_BILLING`，不以上游 CI 作为通过证据。
- 真实作品版权、审美与正式发行审批仍为 `PENDING_HUMAN`。
