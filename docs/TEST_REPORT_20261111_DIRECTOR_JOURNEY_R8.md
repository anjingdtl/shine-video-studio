# 测试报告：导演模式真实穿测第 8 轮（2026-11-11）

对应进度文档：`docs/PROGRESS_20261111_DIRECTOR_JOURNEY_R8.md`
测试树：`main` @ `7809902`（工作区另有未提交的文档与本报告）

## 1. 后端回归

### 1.1 全量后端套件

```
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
```

| 批次 | 启动时间 | 结果 |
| --- | --- | --- |
| 全量（覆盖 `592c8b9` 的树） | 2026-11-11 00:02 | **2253 passed / 2 skipped / 0 failed / 0 errors**，退出码 0（日志 `.local-acceptance/_full_pytest_20261111.log`） |
| 全量（覆盖最终树） | 2026-11-11 08:57 起 | 见 §1.4 |

计数口径：按 `-q` 进度行的字符统计（`.` 2253、`s` 2、`F` 0、`E` 0），末尾 `EXIT=0`。

### 1.2 本轮改动的定向回归（逐批跑，退出码全为 0）

| 测试文件 | 数量 | 覆盖本轮的哪条修复 |
| --- | --- | --- |
| `test_agent_retry_and_rate_limit.py`（新增） | 12 | 429 重试与端点冷却、探测仍不重发、agent 通道预算、剧组链限流重试、合同拒绝的具名纠错、格式失败的字段名、渲染器客户端不受 `[::1]` 影响 |
| `test_film_unknown_outcomes.py` | 24 | 崩溃遗留任务的三种核对结论、核对幂等、未知结果的授权重发 |
| `test_storyboard.py` | 33 | 素材缺口只是素材请求（不阻塞单元）、真实冲突仍然阻塞 |
| `test_film_crew.py` | 32 | 剧组链端到端、连续性仲裁、参考集策略 |
| `test_crew_prompt_contract.py` | 17 | 剧组提示词与官方约束的一致性 |
| `test_four_stage_reacceptance.py` | 19 | 四阶段 R01–R06 复验收（单元回执、交付快照、分集身份） |
| `test_llm_setup_contract.py` / `test_llm_gateway.py` | 36 / 19 | NO_PROXY 规整、连接与生成检查、输出预算 |

以上六个文件合计 134 条定向用例一次性全绿。

### 1.3 其他受影响批次

| 测试文件 | 结果 |
| --- | --- |
| `test_v13_instances.py`、`test_installation_persistence.py`、`test_managed_process_release.py` | 20 / 16 / 21 全绿（实例、受管进程、代理规则改动的影响面） |

### 1.4 最终树的复跑状态

最终树在 `7809902` 之后又补了本报告与进度文档两处**纯文档**改动；最终树的全量复跑在
报告写出时仍在进行，日志写入 `.local-acceptance/_full_pytest_20261111_final.log`。
**若该批次未跑完，不得把 §1.1 的 2253 条当成最终树的证据**；以该日志的 `EXIT=` 与
失败计数为准。

## 2. 前端回归

```
cd frontend && npm.cmd test -- --run
```

- **225 passed / 34 files**，15.55s，退出码 0。
- `npx tsc --noEmit`：本轮改动的 `FourStagePanel.tsx`、`api.ts` 类型检查通过。

## 3. 静态检查

```
backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests
```

- **All checks passed**（本轮新增文件 `shine/http_env.py`、
  `tests/test_agent_retry_and_rate_limit.py` 也在检查范围内）。

## 4. 真实链路证据（本机，非模拟）

| 事实 | 证据 |
| --- | --- |
| 文字模型是远端真实服务商 | 台账 `model_id=MiniMax-M3.1-flash`、`model_profile_id=012db0db…`、`is_mock=false` |
| 每次调用都有真实意图与结果 | `agent_task_records` 中 12 场分镜共 100+ 条角色记录，含 `phase=WAITING_PROVIDER` 的真实等待与 `http_status=429` 的真实限流 |
| 限流是真实发生的 | 5 条 `LLM_RATE_LIMIT` 记录，`external_outcome=RESPONSE_RECEIVED`、`http_status=429` |
| 崩溃遗留任务真实存在 | `32b610fa…` 长期 `RUNNING`，核对后按 `USER_AUTHORIZED_NEW_REQUEST` 退役 |
| 本机 GPU 未被占用 | `nvidia-smi` 显示 RTX 5080 显存 1677 MiB、利用率 0%（本轮只有文字调用） |

## 5. 未通过 / 未覆盖

1. **最终树的全量复跑**未在报告写出时完成（见 §1.4）。
2. **真实 H3 出片未覆盖**：本轮 0 个 Job、0 个 Shot、0 个成片，`test_offline_e2e`、
   `test_production_series` 只在离线层通过，**没有一次真实渲染**。
3. **VLM 正负校准未执行**：本轮未改 `worker.py` / `preproduction.py`，旧 revision 依旧不可复用，
   也**不得**声称已校准。
4. **LLM_TIMEOUT 的根治未验证**：等待上限提到 600 秒的设置在本轮未能落库
   （模型档案被其他项目的在途任务占用，`PROVIDER_PROFILE_HAS_IN_FLIGHT_TASKS`），
   因此该方向只有代码与测试，没有实机证据。
5. **浏览器旅程未跑完**：④ 分镜只有 2/12 单元有镜头方案，⑤–⑦ 未进入。

## 6. 结论

本轮修复的八个缺陷都有对应的回归用例且全绿；**两集出片没有完成**，真实渲染与后期装配
在本轮完全未覆盖。报告如实记录边界，不对未验证的部分作结论。
