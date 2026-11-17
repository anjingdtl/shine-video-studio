# 验收记录 2026-11-17：能力卡缺件裁决同源 + supervisor 归入同一停止链（发版轮）

提交：`3437a6b`（缺件裁决同源）、`c854027`（supervisor 停止链），均已推送 `main`
（远端 sha 经 `git ls-remote` 核实；ruleset `main-owner-only-push` 所有者 bypass 生效）。
本文档记录根因、收敛修改、回归与真机验收边界；发版产物见 `releases/` 与对应 GitHub Release。

## 1. 报障与根因

### 1.1 能力卡「已核验却报缺件」（远端真机截图）

- H3 卡：① 文件「已就绪并核验」，同卡却挂「缺少 6 个文件（25.1 GiB）」并给下载链接。
- 画面审片 / 语音核对：① 已核验、② 运行合同已核对，同卡却挂「缺少 13/10 个文件（0 GiB）」。
- 根因：同一张卡片读两份事实。`capability_report` 的 presence 只读
  `<选中实例>/models`，把「本机已核验、只是尚未落位到该目录」与「本机真的没有」
  混成一份缺件表；而「① 文件」读核验凭据（凭据记录用户自己目录里的字节，重读仍匹配）。
  审片另有根分裂：审片运行时真正读 `isolated-packages/<component_id>`
  （`advisory_review_readiness` 读那里），presence 却读 ComfyUI models 目录。
- 整备停步显示裸 `ValueError`：安装链拒绝是裸 `ValueError`、具名码在 message 里，
  `_code_of` 只取类名；该码此前也没有恢复指引映射。（「批准不提交即回滚」的根因
  早前一轮已修，见 `backend/tests/test_machine_progress.py`；远端截图为旧包行为。）

### 1.2 启动失败 `uv sync` os error 5（另一远端真机截图）

- 现象：`error: failed to remove file …\_brotli.cp312-win_amd64.pyd: 拒绝访问 (os error 5)`，
  `start_shine.ps1:65` 抛「Python dependency installation failed with exit code 2」，
  并被误导向 `bootstrap.ps1`（工具其实不缺）。
- 根因：常驻 supervisor 故意不在 `processes.json`（重启不被自己杀掉），但**完整停止
  此前也不收尾它**；它跑在本 checkout 的 venv 上持有 site-packages DLL。页面停止 /
  空闲自动退出后 supervisor 仍活，下次启动 `uv sync --locked` 换包即撞锁。
  启动器残栈收尾只认 `processes.json`，manifest 缺失 / 被归档时连 supervisor 记录都不看。

## 2. 收敛修改（一套事实 / 一个裁决 / 一个执行者）

- `backend/shine/comfyops/setup_summary.py`：每文件只分类一次。
  `missing_files`/`missing_bytes` = 需**外取**的字节（未落位、无有效凭据、不在缓存），
  供卡片显示与下载体积预览；`unplaced_files` = 未落位全集（含本机还没有的），供执行者
  建计划（缓存 / 凭据命中下载 0 字节，执行本地放置）。审片 presence 与
  `advisory_review_readiness` 同根（`package_root`）；H3 有作品时 binding 解析前移，
  缺件表与事实层跟同一份「图真正加载的组件」；② 的原因挂 `variant_readiness.next_step`
  与审片就绪既有裁决，不再另写通用推断。
- `backend/shine/api_setup.py`（prepare-plan）与 `setup_preparation.py`（DOWNLOAD 步）：
  计划组件取 missing ∪ unplaced；下载体积只算 `missing_bytes`，与卡片同一个数。
- `setup_preparation.py::_code_of`：裸 `ValueError` 的具名码（message 形如大写蛇形）
  成为记录码；前端失败块挂 `describeBlocker`，`errorActions.ts` 新增
  `INSTALL_APPROVAL_REQUIRED_OR_PLAN_HASH_CHANGED` 映射（再点一键准备会重新批准同一份
  计划并从停步继续）。
- `scripts/stop_shine.py`：新增 `stop_supervisor`，**复用同一条 `stop_manifest` 身份核验
  停止路径**；归属证明 = 记录 birth（新记录补写 birth + repo_root），旧记录用记录端口
  自己的 `/health`（role/pid/repo_root/data_dir，health 新增 pid）证明后才停；证明不了
  保留记录、不碰进程。`UNKNOWN` 判定显名化（模块对外契约）。
- `scripts/shine_supervisor.py`：自发起停止 / 重启（含重启拉起的 start_shine 子进程）带
  `SHINE_STOP_KEEP_SUPERVISOR=1`，重启不会杀掉驱动重启的自己。
- `scripts/start_shine.ps1`：残栈收尾条件 = manifest **或** `run/supervisor.json`；
  依赖失败给具名指引（占用 → 同一条停止命令收尾 → 重试本入口，不需要 bootstrap）。
- 顺带：`backend/shine/api_film.py:1550` 预存 UP038 一行 lint。

## 3. 回归与静态检查

- 新增 `backend/tests/test_setup_missing_verdict.py`（6 例）：外部目录凭据不算缺件、
  缓存字节欠落位不欠下载（prepare-plan 组件在、download_bytes=0）、部分缺件时
  ① 标签计数 / 体积与缺件表同源、审片与就绪同根（稀疏文件只满足尺寸过滤）、
  图不加载的可选 LoRA 不进缺件表、裸 ValueError 具名码成为记录码。
- 新增 `backend/tests/test_supervisor_stop_convergence.py`（6 例）：真实进程被同一停止
  执行者收尾、keep 标记保留自己、旧记录 health 证明后才停、无证明拒绝并保留、
  停止 / 重启子进程带标记、启动器契约（认 supervisor.json、具名指引、用户启动不带标记）。
- 新增 `frontend/src/test/setupMissingVerdict.test.tsx`（3 例）：已核验不挂缺件入口、
  真缺件数量体积同源、停步显示映射原因而非 ValueError。
- 迁移 `test_setup_chain_convergence.py` 缓存字节断言到 missing/unplaced 语义。
- 后端定向批：缺件轮 9 模块 83 例、停止链轮 7 模块 58 例，均 exit 0 且零 FAILED/ERROR；
  `test_film_unknown_outcomes.py` 25 例绿。`ruff check backend/shine backend/tests scripts/…` 干净。
- 前端全量 `npx vitest run --no-file-parallelism`：80 文件 / 402 例绿；`tsc --noEmit`、
  `npm run build` 干净；`start_shine.ps1` 经 PowerShell Parser 解析无错误。

## 4. 真机验收（隔离数据目录，不碰用户共享栈）

### 4.1 supervisor 停止链四段（`.local-acceptance/sup-stop-20261117/accept.ps1`）

预置与当前锁文件同指纹的回执（依赖事实为真：仓库 venv 与 node_modules 已就位），
隔离验收不触发 `uv sync` / `npm ci`（共享依赖红线）。

1. `FIRST_START_OK`：首启成功，supervisor 记录带 birth 身份。
2. `RESIDUAL_STATE_OK`：带 keep 标记的停止后 manifest 消失、supervisor 仍活（= 远端残栈态）。
3. `SECOND_START_OK`：无 manifest 再启，启动器先收尾旧 supervisor（旧 pid 已死）再同步。
4. `STOP_OK`：完整停止后 supervisor 记录、manifest 消失，端口不再监听。

### 4.2 启动器四条（`.local-acceptance/launcher-4-20261117/p1.ps1 / p2.ps1 / p3.ps1`，串行）

1. `P1_START_OK` / `P1_STOP_OK`：干净启动后 API `/health` 与 Web `/` 均 200（curl 环回免代理）；
   停止退出码 0，manifest 与 supervisor 记录消失，端口不再监听。
2. `P2_OK api=8001 web=5174 foreign8000=alive` / `P2_STOP_OK`：8000 上放一个自己的空目录
   静态服务（绝不服务仓库根），启动器自动换到 8001/5174、未声称「已在运行」、未碰占用者；
   停止后释放占用者。
3. `P3_OK web restarted on 5174` / `P3_STOP_OK`：只杀 Web（记录身份）后再启动，启动器报
   `WARNING … missing: Web; reconciling it first`，身份核验收尾残栈后 Web 恢复 200、
   旧 pid 已死；停止后记录与端口全清。

如实记录：启动器为日常数据目录保留默认端口，故三个探针实际都落在 8001/5174；P2 的
「默认端口被占」另叠加自有 8000 占用者，结论不变。此前两版整脚本验收失败的原因是
验收方法而非产品缺陷：本机控制台重定向吞掉子进程输出，以及一次并行清理在验收运行中
删除了 manifest（`Get-Content processes.json` 报不存在）。改为串行短探针后一次通过。

偏差如实记录：入口链为 `启动 Shine 工作台.cmd → start_dev.ps1 → start_shine.ps1`；
`start_dev.ps1` 无 DataDir 参数，双击会落在用户默认数据目录，故四条验收在隔离目录
直接驱动同一执行者 `start_shine.ps1`（入口包装只追加浏览器打开与暂停）。浏览器落点
（空库 `#d/setup`、有作品 `/`）由前端回归 `setupEntry` / `modeEntry` 覆盖，本轮未开浏览器。

## 5. 发版

（待补：安装包文件名 / sha256、-Verify 结果、Release URL。）

## 6. 边界与未做

- 远端旧包用户当下办法：结束命令行含 `shine_supervisor.py` 的 python 进程（或重启机器）
  再启动；根治需换本包。tester 清单已加对应勾选与升级处置。
- 本轮无新真实 H3 出片；W6A8 候选与 12GB 真机路径仍 BLOCKED/NOT_RUN；VLM 正负校准
  仍未完成，不得声称已校准。未改 `worker.py` / `preproduction.py`，不触发重校准义务。
- 开发机 8790 端口存在一个**非本轮**的残留 supervisor（身份不属于本验收栈），未触碰，
  如实上报；它正是本轮修复的缺陷类别的现存实例。
- 后端全量 8 分片未跑（本轮按定向口径；两轮改动面已由定向批与前端全量覆盖）。
