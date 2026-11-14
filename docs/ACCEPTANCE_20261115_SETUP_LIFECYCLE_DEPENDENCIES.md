# 验收：工作栈生命周期与全流程依赖一键整备（2026-11-15）

## 范围与用户裁定

1. 关页后后台工作栈残留 → 页面 presence + 空闲自动退出（有活工作不退、无头不退）。
2. 服务复核验「停 Worker」无产品入口 → supervisor Worker 级停/启。
3. 迁移阻塞只报计数 → 对象化明细 + 处理入口（门不变）。
4. 收编机节点白名单目录名不匹配 → 按磁盘目录解析 + 启动 import 日志落盘。
5. 裸机（无 ComfyUI/权重/节点/工作流）一次性补全 → 持久整备链 INSTANCE 步 +
   全链缺口分析，入口收进导演模式基础配置（`#d/setup` 嵌入 SetupCenter）。

## 分级证据

### OFFLINE_VERIFIED（开发机，本轮已执行）

- 后端新增/定向：`test_supervisor_lifecycle.py`（6）、`test_node_pack_whitelist.py`（4）、
  `test_setup_preparation_instance.py`（5）、`test_output_directory.py`（10）、
  `test_setup_capability_gates.py`（21）全绿。
  命令：`backend\.venv\Scripts\python.exe -m pytest <上述文件> -q -p no:cacheprovider`
  （`CODEBUDDY_SAFE_DELETE_ENABLED=0`）。
- Ruff：改动面 `backend/shine`、两个 scripts、三个新测试文件 0 错误。
- 前端：`npx tsc --noEmit` 通过；`npx vitest run --no-file-parallelism` 全量
  （含新增 `stackLifecycle.test.tsx`、更新 `setupEntry`/`directorEntries` 新契约）；
  `npm run build` 通过。
- 单测覆盖：自动退出决策矩阵、presence 的 Origin/marker 拒绝、stop-stack 忙拒绝、
  stop-worker 临时 manifest 不改真实记录、start-worker 运行中拒绝；白名单别名/歧义/
  缺包、bootstrap 日志落盘（子进程真实执行）、409 带日志摘录；INSTANCE 步跳过已有安装、
  等待下载、单授权批准+执行、NEEDS_REVIEW 具名停。

### 开发机真机（生命周期，本轮已执行）

命令：`SHINE_IDLE_SHUTDOWN_MINUTES=0.1` + `start_shine.ps1 -DataDir .local-lifecycle-test
-SupervisorPort 8791`，curl 监督进程端点，45s 后复查。结果：

- `POST /stop-worker` → `{"status":"STOPPED"}`；`POST /start-worker` → `{"status":"RUNNING"}`。
- `POST /presence` → ok；停报 45s 后 API `/health` 无响应（`API_DOWN`）。
- `GET /lifecycle` → `stack_running:false`、`last_stop:{kind:"auto", ok:true,
  detail:"后台已停止，显存已释放。"}`。
- 收尾 `stop_shine.py` 与监督进程清理完成；三个历史残栈（witch-main/v2/closeout）
  按身份核验停止——它们共享 node_modules 并锁住 esbuild/rollup，正是「残栈拥堵」的实例。
- 过程中发现并修复：`_stack_blockers` 误用 `AgentTaskRecord.kind`（真实口径为
  `role_id`，creative 任务角色 `SCRIPT_TASK_ROLE_ID`/`creative_brief_authoring`）。

### 用户机（待执行，如实报告）

- 收编安装：重新生成配置（白名单目录名）→ `services/validate` → L4 封存；
  PDD-Acc 若仍导入失败，凭 `renderer-<instance>.log` 定因，不静默删包。
- 裸机：`#d/setup` 一次确认 → 下载闭包 → INSTANCE 阶梯 → 登记/核验/激活；
  硬件不达标时「补全完成」与「具备出片条件」分开陈述。

## 边界

- 未改 `client_review_identity` 钉住文件 → 不触发 VLM 重校准；历史正负校准未完成，
  **不得声称已校准**，也不声称真实出片验收。
- 12GB 机 8000 阈值契约仓库化不在本轮（用户本地修改，产品裁定另议）。
- 不放宽任何门禁：idle gate、身份三证、UNKNOWN、`--disable-all-custom-nodes`、
  每计划授权/对账语义均保持。

## 启动器验收

`start_shine.ps1` 仅新增 `.env` ownedKeys 透传 `SHINE_IDLE_SHUTDOWN_MINUTES`；
四条入口（干净重启/端口被占/半残栈/停止）按 AGENTS.md 执行并记录。