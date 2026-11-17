# 素材缺口 · 一次采纳 · 实际剪辑表 · 整备一条链 · 本地 main 收口（2026-11-17）

> 仓库 `F:\ClaudeWorkSpace\projects\shine-video-studio`，`main@f066357` 上原地修复，
> **未新建分支、未建 worktree**。上轮核验基线 `main@f066357` 与 `HEAD` 一致，工作区
> 无已跟踪改动（仅未跟踪目录）。本轮按「一套事实、一个裁决、一个执行者」收敛五处
> 分裂链路，全部修在原有产品入口上，不新增检查、不新增状态、不新增入口。

隔离数据：真实开发库快照副本 `.local-acceptance/r3-repro/`（只读复核）与
`.local-acceptance/r3-ui/`（Web 回放，后端 8041 / 前端 5211，`SHINE_RENDER_PROVIDER=mock`）。

---

## 1 · 23 镜作品的旁白镜素材缺口（逐镜裁决，不再整场判定）

**现象** 项目 `cef9e171…` 的旁白镜 `1d14b472…` 在旅程里报 `SHOT_REFERENCE_MISSING`，
④ 素材清单里却没有它的任何需求。

**根因** `backend/shine/director/requirements.py` 用 `if not local` 决定是否为一场补
场景参考——判据是「这一场有没有任何素材」。同场别的镜头有人物需求，`local` 非空，
整场就被认为不需要场景，只缺参考的旁白镜被漏掉。旧回退名还是分段键 `section:…`，
即使补上也匹配不到真实场景主素材。

**修复** 派生流程直接消费 journey 的同一份逐镜裁决 `_shot_material_problems`：
只有 `SHOT_REFERENCE_MISSING` 且本镜自己没有任何可绑参考（人物 / 场景；道具不算，
采纳执行者不会把道具绑成参考图）时，才为该镜补场景需求；名字用真实场次标题，已有
场景主素材时自然命中复用，不再按场次序号造无关填充图。已有参考的镜头不生成填充图。

**只读复核（真实库副本）**

- 修复前：23 镜只覆盖 22 镜，`1d14b472…` 不在任何需求里（已复现）。
- 修复后：**23/23 覆盖**；旁白镜得到 `scene · 第2场：旧物修复铺`，`critical=True`，
  `strategy=generate`（该项目第 2 场没有同名场景主素材；第 6 场的同名地点另有一份）。

**回归** `backend/tests/test_material_shot_scene_gap.py`
- 同场一镜有人物主素材、另一镜只有旁白且无参考 → 场景需求只覆盖旁白镜，
  `critical/ strategy=generate`，人物镜不被塞进场景需求。
- 该场已有同名场景主素材 → `strategy=reuse_master` 且 `matched_master` 指向它。

---

## 2 · 同一镜的素材一次采纳、一次写入

**现象** 同一镜头同时确认两份合法人物主素材时，第二次写入读到旧绑定，整笔回滚：
`422 Bindings are missing assets required by the shot`。

**根因** `materialprep/service.py::bind_adopted_masters_to_shots` 按「需求行」逐条写
`shot.bindings`：第二条读到的 `shot.bindings` 是第一次写入之前的关系集合，而
`required_asset_ids` 已被第一次写入合并过，于是「已声明两份、只绑一份」被
`services.set_shot_bindings` 判为缺件。人物与场景同批时同样互相覆盖。

**修复** 仍是**同一个采纳执行者**，改成「先读全部裁决，再按镜头合成目标集合，最后写一次」：
同一镜头的人物与场景决策先合成 `{(family, entity): asset}`，与台账里的现有绑定
（`_binding_rows`，不读可能过期的关系集合）比较后一次写入 `shot.bindings` 与
`required_asset_ids`；「是否已经就是这个集合」复用 `services.bindings_unchanged`
（读取前先让关系集合从台账重读）。同一人物/场景确认了第二份主素材时**只留新的那份**，
其他有效引用照常保留；已冻结、在途、已通过的镜头仍由 `protected_shot_ids` 保护；
重复确认（丢响应后重放）不会写第二遍、不重复涨 revision。

**反例复验**（在基线代码上全部 4 例报 422，修复后全绿）
`backend/tests/test_material_adoption_one_write.py`
1. 同镜双人物 → 两份主体、`required_asset_ids` 两份，一次写入；
2. 人物 + 场景同批 → 人物两主体 + 场景一张环境图，各自到位；
3. A 换 B → 只留 B，另一个人的引用与声明照常保留，A 从声明里移除；
4. 丢响应后重放同一份确认 → 绑定集合不变、revision 不涨；
5. 场景换图 → 只留新的那张，不给一镜塞两张环境图。

**真机回放**：六镜作品 ④ 页点「确认决策并开始准备素材」→ 提示「素材决策已确认」，
无 422。该作品导演包 `fdec98a2…` 已 `CONFIRMED` 且未过期，六镜都属冻结范围，因此
本次确认如实 `bound_shots=0`（保护规则生效）；写入路径由隔离用例逐条覆盖。

---

## 3 · ⑦ 字幕与装配共用实际剪辑表

**现象** 六镜成片（产物 `1a678d1e…`）第 6 镜真实起点 49.912s，字幕却按计划值
49.000s 排布。

**根因** `director/subtitles.py::_real_clip_durations` 用 `scope_id == "ep-01"` 且
`status == "COMPLETED"` 查交付单；真实交付记录的 `scope_id` 是**运行中的分集行 ID**、
成功状态是 `DELIVERED`。查询永远为空 → 字幕静默回落计划时长。

**修复** 沿当前所选成片版本的 `ProductionRunEpisode.delivery_order_id` 读
`DeliveryOrder.edl_payload`（只认 `DELIVERED`），按真实剪辑顺序与 `in_s`/`out_s`
计算每镜起点与长度；字幕行按「相对本镜时间 − in_s」映射到成片时间，整行落在被裁掉
的片头就丢弃、跨切点的从片段起点开始。换版只跟随指针（同集重剪 = 新版本 + 指针移动），
重新装配仍复用原镜头产物。返回体新增 `cut_order_id` / `cut_version`，页面不必再猜。

**反例复验**（基线代码：`basis` 回落 `planned`，第 6 镜 `49.0 != 49.912`）
`backend/tests/test_episode_subtitle_cut.py`
1. 六镜真实出入点 → `basis=actual`、`cut_version=1`、第 6 镜字幕起点 **49.912**；
2. 片头被裁掉 1s → 该镜 1.5s 的字幕落在成片 6.5s，被裁掉的片头那行被丢弃；
3. 同集重剪出第 2 版并移动指针 → 字幕跟随所选版本（45.0s），且不新建交付单、
   不重新渲染镜头。

**真机回放（真实交付数据）**：⑦ 页点「按真实成片时长生成 / 更新字幕轨」→ 提示
「本集字幕轨第 1 版已按真实成片时长生成」；`subtitle_tracks` 第 1 版 cue
`start_s=49.912 / end_s=51.912`。

---

## 4 · 检索 → 核验 → 复用 → 下载接成同一条链

四个已复现的症状，收到现有扫描任务与现有核验凭据上：

| 现象 | 根因 | 修复 |
| --- | --- | --- |
| 添加目录后队列有任务但状态仍是 `IDLE` | `add_directory` 只入队不推进状态 | 入队后走同一个 `_queue_scan`：有种子就是 `QUEUED`，队列与状态不再各说各话 |
| 重新检索跳过上一轮扫完的目录 | `start_scan` 用 `source="user"`，被 `coverage=DONE` 跳过 | 显式重检索按 `rescan` 重新走用户目录、固定盘与 probe 根；删除目录时按 root 清队列 |
| 指定目录里与清单字节一致的改名权重仍进下载计划 | 整备只看安装目录，检索候选从未被消费 | `reconcile_installation` 消费 `resource_discovery.verification_candidates`：同一尺寸候选交给同一个 `verify_models` 逐字节核验，命中则登记 + 复制进现有内容寻址缓存；`create_install_plan` 的 `download_bytes` 因此为 0（本地落位，不再下载） |
| 已核验文件移走后仍显示 `FILES_FOUND`/`FILES_VERIFIED` | 旧登记（`ModelPackage.status`）直接压制缺件 | `setup_summary` 只认当前证据：核验回执仍能在盘上复读到字节（`verify_receipt`）或缓存里确有这些字节（`cache_candidates`）。`in_place`（回执读到的就是服务目录里的字节）才从缺件里排除——改名文件不再被当缺件，移走/改写的文件重新算缺件；只在缓存里的仍留在安装计划（本地落位、下载 0 字节），基础配置显示「还差 N 个文件落位（字节已在本机，不需要下载）」 |

**反例复验**（基线代码：`add_directory` 返回里没有扫描状态；重检索 `queued=0`；
reconcile 无 `scan_candidates`；移走文件后仍 `FILES_FOUND=True`）
`backend/tests/test_setup_chain_convergence.py`：4 例覆盖上述四条，并断言
`missing_bytes == 0`、`create_install_plan().payload["download_bytes"] == 0`、
`cached_bytes == 文件大小`。

**真机回放**：基础配置 →「本机资源检索」粘贴目录并「添加目录」→ 角标立即变「检索中」，
文案「检索已排队，后台分批进行」，目录出现在检索目录列表；扫描推进后「本轮检索完成 ·
已完成 8/8 个区域」；点「重新检索」→「剩余 1 批」（上一轮已扫完的目录被重新排队）。

---

## 5 · 收掉基础配置的执行旁路，修正 Worker 的分批顺序

**旁路**
- `SetupCenter.tsx` 的「核验已有文件」直调 `/setup/reconcile`（长请求、关页面就断）；
- `DirectorSetupPanel.tsx` 的 VLM/ASR 下载仍由页面单独排安装计划并等下载结束。

**修复**
- 两处都接回现有「一键准备」任务（`/setup/prepare/execute`）：`RECONCILE` 与
  `PREPARE_ENVIRONMENT` 走同一个 `onPrepare`；审片/语音只提交能力，进度读
  `/setup/environment` 的已有安装记录（后台整备在跑时 5s 跟读、结束即停）。
  页面里不再有任何一处单独排安装计划。

**Worker 顺序**（`worker.py::run_configured_once`）
- 文本队列排在资源扫描之前：连续四轮扫描不再抢在可运行的文本任务前面；长文本跑满
  一个突发窗口仍会让出一轮给扫描，扫描不会被饿死。
- 整备步骤只是等待（`:waiting`）时不再交回整个 tick；真正推进或失败的步骤才独占。
- 安装队列里**只搬可选审片字节**的计划（`deployment.review_only_plan`，与整备链
  共用同一判定）不再独占 tick；生产字节仍然优先。

**反例复验**（基线代码：四轮全部 `resource-scan`；`setup:DOWNLOAD:waiting` 独占 tick）
`backend/tests/test_worker_batch_priority.py`（6 例）：文本优先、突发窗口仍放行扫描、
可选审片不独占、生产安装仍优先、等待中的整备不独占、推进中的整备独占。

**前端**（`frontend/src/test/setupSingleExecutor.test.tsx`，2 例）：点「核验已有文件」
只发 `/setup/prepare/execute`（`POST /setup/reconcile` 0 次）；审片按钮只提交
`capabilities=['vlm']`（`POST /setup/install-plan` 0 次）。

---

## 归并 / 删除的旧判断与旧执行路径

- 删掉 `requirements.py` 的「整场无素材才补场景」判断与 `section:…` 填充名；
  缺口判定只剩 journey 的 `_shot_material_problems` 一处。
- 删掉采纳执行者的「按需求行逐条写绑定 + 逐条改声明」路径，只保留一次写入；
  绑定相等判定复用 `services.bindings_unchanged`，不再另写比较。
- 删掉 `_real_clip_durations`（错误的 scope/status 查询），换成读所选版本 EDL 的
  `_selected_cut_timing`；字幕与装配读同一张剪辑表。
- 删掉 `setup_summary._registered_components`（用旧登记压制缺件），换成
  `_verified_components`（回执/缓存当前证据，并区分 `in_place`）。
- 删掉 `SetupCenter` 的 `onReconcile` 直调路径与 `DirectorSetupPanel` 的
  `install-plan → approve → waitForInstallationAttempt` 页面驱动路径。
- 删掉 `run_configured_once` 里 `IMAGE_SAMPLES/IMAGE_EDIT_SAMPLES` 的特例字符串比较，
  改为「`:waiting` 不独占 tick」一条规则；扫描从文本之前移到文本之后。
- 整备链与安装队列共用 `deployment.review_only_plan`，不再各自判「这套字节是不是可选审片」。

## 执行记录（回归）

后端全量按 `AGENTS.md` 的固定规则跑 **8 个按文件均分的并行分片**（263 个测试文件、
3001 个用例），前缀 `CODEBUDDY_SAFE_DELETE_ENABLED=0`，日志留在
`.local-data/shards-r3-final/`：

```bash
ls backend/tests/test_*.py > .local-data/shards-r3-final/all-tests.txt
split -n r/8 .local-data/shards-r3-final/all-tests.txt .local-data/shards-r3-final/chunk-
CODEBUDDY_SAFE_DELETE_ENABLED=0 bash -c 'for f in .local-data/shards-r3-final/chunk-a?; do
  ( backend/.venv/Scripts/python.exe -m pytest $(tr "\n" " " < "$f") -q -p no:cacheprovider \
      > "$f.log" 2>&1; echo "$f exit=$?" >> .local-data/shards-r3-final/exits.txt ) & done; wait'
```

| 分片 | 退出码 | 失败 |
| --- | --- | --- |
| chunk-aa | 0 | — |
| chunk-ab | 0 | — |
| chunk-ac | 0 | — |
| chunk-ad | 0 | — |
| chunk-ae | 1 | `test_audit_event_codes::test_the_frontend_list_covers_every_event_code_the_backend_writes` |
| chunk-af | 1 | `test_material_director_flow::test_an_ambiguous_identity_is_reported_but_does_not_block` |
| chunk-ag | 1 | `test_story_unknown_resend::test_two_scene_calls_keep_a_gap_between_them` |
| chunk-ah | 1 | `test_director_streamline::test_journey_follows_the_current_plan_beside_an_older_scoped_package` |

**这 4 个失败全部在基线 `f066357` 上同样失败**（把本轮改动 `git stash` 后逐个复跑，
四个测试同样 FAILED，非本轮引入、也未被本轮改动触碰）：

1. `MATERIAL_SCENE_AUTO_RETRY` 后端事件码（`materialprep/scenes.py`，上轮 `2a284bf` 引入）
   未加入前端码表；
2. `MATERIAL_SCENE_KEY_UNKNOWN`：场景分段只认 `S1`，测试却提交 `S2`；
3. 两场剧本调用之间应有一次间隔，实际只记录到一次调用；
4. `EPISODE_PLAN_NOT_MATERIALIZED`：旧导演包旁的新计划场次未落镜。

按「只提交本轮相关文件」的要求，这 4 项保持原样并如实报告，不在本轮顺手改。

其余检查：

- 前端全量 `npx vitest run --no-file-parallelism`：**78 文件 / 395 例全通过**
  （上轮 77/393，本轮 +1 文件 +2 例）。
- 前端 `npx tsc --noEmit`、`npm run build`：通过（`tsc` 无输出，build 2.35s）。
- Ruff：本轮改动的 16 个文件 `ruff check` **All checks passed**；全仓仅剩
  `backend/shine/api_film.py:1550 UP038` 这一条基线旧告警（未触碰该文件）。
- 定向回归：`test_material_shot_scene_gap`(2) + `test_material_adoption_one_write`(5) +
  `test_episode_subtitle_cut`(3) + `test_setup_chain_convergence`(4) +
  `test_worker_batch_priority`(6) + `test_shot_subtitles`(5) = 25 例全通过；
  另跑了 `test_setup_*`/`test_new_machine_setup`/`test_installation_*`/`test_variant_aware_summary`
  等整备相关 200+ 例无新增失败。

## 23 镜目前下一步能做什么

1. **只读复核已确认缺口**：项目 `cef9e171…` 现在 23/23 镜都有需求；缺的唯一一项是
   `第2场：旧物修复铺`（旁白镜 `1d14b472…` 的环境图，必需）。
2. 由世恒哥在 ④「素材准备」里对这项**上传或确认一张该场环境图**（已有的
   `第6场：旧物修复铺` 主素材属于另一场，系统不会替它跨场借用），确认后会按第 2 节的
   一次写入把这张图绑成该镜的参考图。
3. 之后 ⑤ 导演包核对「第 1 集 6 镜已交付」范围 → 开拍。本轮没有替用户确认任何素材、
   没有开拍、没有真实出片。

## 仍待验证 / 已知限制

- **真实 H3 出片**不在本轮范围：本轮所有渲染相关结论都来自隔离数据与只读复核，
  没有新的真实 GPU 出片。
- 六镜作品导演包已冻结（`CONFIRMED`、未过期），素材再确认不会改写这些镜头
  （`bound_shots=0` 是保护规则，不是失败）；要看到绑定变化必须等新一轮导演包。
- 真实库的扫描索引里没有与真实清单同尺寸的改名权重样本，第 4 节的「改名权重复用」
  用隔离清单（小文件）覆盖；`download_bytes=0` 与缓存落位都已断言。
- 基线已有 4 个失败（`f066357` 上同样失败，已在基线复跑确认）：见「执行记录」表下四条。
  本轮没有修它们，也没有因此放宽任何门禁。
- 基线已有 Ruff 告警 `backend/shine/api_film.py:1550 UP038`（未触碰该文件），本轮改动
  文件 Ruff 全绿。
