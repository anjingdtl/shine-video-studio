# 测试报告（2026-11-03 晚间补充轮 + 收尾轮）

- 记录时间：2026-11-03 18:06（Asia/Shanghai）；**收尾轮更新：2026-11-03 20:50**
- 被测代码：当前工作树（在 `4d8123a` 之上的收尾轮改动，见进度文档）
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

- 本轮未重新执行浏览器全流程复验（正式项目当前镜头阻塞，待恢复后再做）→ **已在收尾轮完成，见第五节**。
- 未从本轮最终提交重新构建 Windows 测试包；包构建与入口脚本校验列为后续工作 → **已在收尾轮完成，见第五节**。
- GitHub Actions 仍为 `WAIVED_EXTERNAL_BILLING`，不以上游 CI 作为通过证据。
- 真实作品版权、审美与正式发行审批仍为 `PENDING_HUMAN`。

---

## 五、收尾轮（2026-11-03 晚间后段）

被测代码：收尾轮改动（`worker.py` 持久化产物恢复、`handoff_startup.py` 死渲染器租约释放、`preproduction.py` 重新校验、`production_series.py` 同事务归并、`h3/compiler.py` 发声合同、前端对白语言下拉）。

### 1. 后端全量测试

命令：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q 2>&1 | Tee-Object -FilePath ..\.local-acceptance\backend-suite-final-20261103.log
```

结果：**`PYTEST_EXIT=0`**；按进度行逐字符统计 **`1472 passed` / `1 skipped` / `0 failed`**（共收集 1473 项，跳过 1 项为既有 Windows Job Object 依赖标记）。日志：`.local-acceptance/backend-suite-postfix-20261103.log`（这是独立复核修复之后的最终全量；此前一轮 `1464 passed / 1 skipped / 0 failed`，日志 `.local-acceptance/backend-suite-final-20261103.log`）。

该轮包含新增回归：`test_comfy_completion_window.py`（产物已持久化 → 不重投 → QC 通过 → PASSED；**落盘字节被篡改 → 不认领 → `ARTIFACT_HASH_MISMATCH`**）、`test_comfy_handoff_journal.py`（死渲染器释放 / 存活渲染器不释放 / **命名 Job Object 有成员则不释放** / **身份不可读则不释放**）、`test_production_series.py`（陈旧系列释放死渲染器租约 / 无法证明则保留租约 / **已完成但有阻塞镜头的系列被纠正**）、`test_v13_preparation.py`（重新校验成功 / 渲染合同变更时拒绝 / requirement 行重写不改变绑定 / **审片模型不算渲染绑定**）、`test_h3_compiler.py`（发声合同 / 语言名以英文规范名写入提示词）、`test_storyboard.py`（项目语言在 API 边界被规范化或明确拒绝）。

### 2. Ruff

```powershell
cd backend
.\.venv\Scripts\python.exe -m ruff check shine tests
```

结果：`All checks passed!`（`RUFF_EXIT=0`）。

### 3. Alembic

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic heads
.\.venv\Scripts\python.exe -m alembic history -r head:head
```

结果：单头 `f150a0030001 (head)`，`HEADS_EXIT=0`；无多头、无迁移漂移。

### 4. 前端构建

```powershell
cd frontend
npm.cmd run build
```

结果：`BUILD_EXIT=0`。产物：`dist/index.html 0.47 kB`、`index-DNeT5LWc.css 56.51 kB`、`index-UlvaeEEW.js 475.97 kB`（`tsc -b && vite build`，1606 modules）。

### 5. 前端测试

```powershell
cd frontend
npm.cmd test
```

结果：**`15 passed` 个测试文件 / `93 passed` 个测试**，`TEST_EXIT=0`（含新增「对白语言」保存、保存失败如实显示、未知语言回退默认 3 条）。

### 6. 真实模型与真实生产证据（收尾轮）

- **VLM 真实重校准**（最终 pinned review client `client_review_sha256=8b8d9bd3…`，本地 `qwen3-vl-4b-bnb4`）：正向 `3f82719e-49a7-4cc9-911c-57c94b5f0e22` → `passed`（confidence 0.98，0 findings）；负向 `87a00a00-41b2-4789-b98f-4e4409db401e` → `failed`（confidence 0.85，7 findings）。记录：`.local-acceptance/vlm-recalibration-20261103.json`。（独立复核修复后又重做一次，此为最终结果；上一轮校准 `7b2246d0…`/`32310955…` 已作废。）
- **新修订发布**：VLM `03f32432-4c94-4476-8580-e1a14e9fc37f` `PRODUCTION_APPROVED`；此前发布/迭代产生的 `82906927…`/`f930e8ec…`/`43ca215d…`/`ed841a10…`/`e83b9b54…` 已 `DEPRECATED`，`839aeef6…` 保持 DRAFT。
- **重新冻结**：计划 `7832906a-d383-4eaa-a94d-0e0b510769d2`（`FROZEN`，绑定 H3 `93da6ba3…`、VLM `03f32432…`、ASR `1f0263da…`），`readiness(offline=false)` = `SHOOT_READY`。
- **正式镜头真实恢复**：镜头 `f0173fbe-41c0-4ee0-ac26-9c6fecc74635` = `PASSED`，`quality_verification=manual_verified`，`acceptance_status=accepted`；attempt `reconciliation_state=adopted_persisted_artifact`，**`resubmitted=false`**（未重投 H3）。门禁：technical `passed`（ffprobe + FFmpeg 全帧解码）、semantic `passed`、style `passed`（真实 VLM 0 findings / 0.98）、audio `not_applicable`、final `passed`。按新冻结合同重新校验后 `preproduction_manifest_hash` = `55b03fe7…`，状态不变。
- **人工逐项审片**（浏览器 OperatorReview，`local-user`）：7 项「通过」+ 3 项「不适用」，逐项写有具体证据；「我已完整观看当前视频」已勾选；**「已人工试听」未勾选**（本轮未做人工听辨，不冒充）。
- **租约与系列**：GPU 租约 `96971172-9ad9-4518-b82a-3f0e2de5abb0` → `RELEASED`（走「已证明死亡」释放路径）；系列 `a6b97b5e-1cb3-4daa-ae71-409686fe806d` → `COMPLETED / RELEASED`，`completed_count=1/1`。
- **交付与成片**：交付订单 `ac7f0298-4411-4c71-9f29-f7588686e2ba` → `DELIVERED`；成片 artifact `6c65ee08-193e-4b02-9c95-b58e08f649bd`，下载得 3,644,345 B，SHA-256 `04dc2cff7ce579b89ca54ff754d24dad6a6c9aee305dd857f7f0bbd91bb03241`（与界面指纹前缀一致）；ffprobe：mp4 / h264 768x1344 / 24fps / AAC 48kHz 立体声 / 9.44 s；独立 `ffmpeg -f null -` 全片解码 exit 0。

### 7. Windows 测试包

命令：

```powershell
pwsh -File scripts\build_test_package.ps1 -OutDir <dir> -Verify
```

结果：**__PACKAGE_RESULT__**。包必须从已提交的干净树构建（脚本会拒绝脏工作树）。

### 8. 收尾轮未运行项与边界（如实保留）

- **未做带人声的真实 H3 重渲染**：对白语言设置已验证到提示词层（编译器回归 + 前端回归），模型实际口音未验证。
- **未做新的完整多镜正式系列生产**：正式项目本轮只完成单镜头（1/1）恢复与交付；多镜连拍的真实生产证据仍来自回归作品三镜系列 `0dd7ad85…`。
- **未做 Windows 之外的操作系统验证**：所有验证都在 Windows 11 本机完成。
- **未使用上游 CI 作为通过证据**：GitHub Actions 仍为 `WAIVED_EXTERNAL_BILLING`。
- 真实作品版权、审美与正式发行审批仍为 `PENDING_HUMAN`；建议式模型审片不等于人工签字。
