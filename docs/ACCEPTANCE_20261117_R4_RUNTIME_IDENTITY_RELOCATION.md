# 环境整备状态跟着「这台机器」走，不跟着「这一次的目录」走（R4）

> 用户报障：「怎么又出现『真实制作暂不可用』了，不是已经要求根据三原则统一环境整备状态了吗？
> 为什么每期启动项目都要重新配置基础配置，不是要求检索过后就要保持状态么？」
> 追加要求：「主仓的项目代码还有哪些地方钉在非主仓的 worktree 里、副本里、非主分支里，再 review 修复。」

本轮把「配置身份」从「账本目录的绝对路径」收敛为「这台机器的事实」，并顺手清掉了仓库里
会让运行落到副本上的两处残留。仓库主检出一份，`main` 原地提交，不新建 worktree。

---

## 1 · 根因：四个都指向同一件事——把「书签」当成了「事实」

| # | 现象 | 真正的原因 |
|---|------|-----------|
| 1 | 徽标「真实制作暂不可用 · 固定文件发生了变化」 | `configuration_identity` 把每个钉住文件的**绝对路径**写进身份；`same_configuration` 要求整份身份**完全相等**。数据目录被复制/改名，路径变 → `ACTIVE_RUNTIME_CONFIGURATION_CHANGED` |
| 2 | 连身份都算不出来（`COMFY_RECIPE_PATH_MISSING`） | `managed_recipe.json` 字节没变，但里面 `runtime_data_root`、`bootstrap`、`log_file`、审片 `manifest`/`argv` 仍写着**已被删除的 worktree**；`load_managed_recipe` 的 `_absolute()` 要求路径**存在** |
| 3 | 升级一版代码又被判成「环境变了」 | 仓库自带的 `scripts/managed_comfy_bootstrap.py` 与 `scripts/local_review_isolated_server.py` 被当作「安装事实」钉住，改一行代码就让整台机器的环境失效 |
| 4 | 重新生成一次配置后，之前验证过的服务又作废 | 服务证据封条 `configuration_sha256 = graph_hash(identity)`（含路径）。路径一变，封条必失配 → `SERVICE_VALIDATION_STALE_CONFIGURATION` |

四条的共性：**账本目录（可搬）与机器事实（不可变）被混在同一个判断里**。于是「检索过、
验证过、激活过」这件事无法跨一次目录搬迁存活，操作员每次都从零开始。

实测（真实运行库 `.local-acceptance/witch-main-20261111`，`main@f28e8fe`）：

```
resolve : BLOCKED ACTIVE_RUNTIME_CONFIGURATION_CHANGED
report  : INVALID_REQUIRES_REVIEW / unavailable / services_verified= False
```

---

## 2 · 修复：一个裁决（`same_configuration`），一条链（`load_runtime_recipe`）

### 2.1 配置身份只比「机器事实」

```python
_CONFIGURATION_FACTS = ('instance_id', 'instance_sha256', 'catalog_sha256',
                        'recipe_sha256', 'comfyui_base_url')
```

- 五项事实全等，且**安装态**钉住文件的摘要集合全等 → 同一套配置。
- `_workbench_pin_names(settings)` 把**仓库自带的两个脚本**（启动器 bootstrap、审片入口）
  从安装事实里摘出去：它们随产品版本走，升级后就是「下一次运行的代码」，不是「环境变了」。
- 审片 `manifest`（`asr.json`/`vlm.json`）**仍计入**：它是操作员安装的模型清单，改了要重验。
- 解释器、ComfyUI `main.py`、adapter 的 `__init__.py`/`barrier.py`、采样节点 → 改动照旧阻断。

### 2.2 配方里的绝对路径是书签，按现状解析

`_recorded_roots` 认出两处会搬的根：`<data>/comfyops` 与仓库根。`_anchored` 只在
「记录路径已不存在」且「落在旧根下」时才映射到现位置。

`load_managed_recipe(path, *, expected_sha256=None, anchor=None)`：

- `expected_sha256` 永远比对**磁盘上的原始字节** → 搬迁后仍能证明「就是操作员激活的那份」；
- `anchor` 在校验通过**之后**递归改写文档里的绝对路径 → 渲染器拿到的是现位置。

### 2.3 唯一读取入口

```python
load_runtime_recipe(settings, *, expected_sha256=None, path=None) -> (recipe, sha256)
```

返回「现位置的配方」与「磁盘字节的摘要」，于是记录身份的人哈希的正是它校验过的东西。
`service_validation`（两处）、`worker._managed_recipe`、`image_renderer`（两处）全部改走它。

### 2.4 证据封条只封机器

```python
def configuration_fingerprint(identity) -> str:      # 只含 _CONFIGURATION_FACTS
```

`record_service_evidence` 用它落封条；读取时同时接受旧口径（`graph_hash(identity)`）——
旧证据只是「按当时的口径封的」，不是坏证据，不该逼着操作员重做一遍验证。

---

## 3 · 复验（反例 → 绿）

| 反例 | 手段 | 结果 |
|------|------|------|
| 数据目录被复制：身份记在已删除的 worktree，配方字节不变 | `test_a_moved_data_directory_keeps_real_production_ready` | `ACTIVE` / `services_verified=True`，账本里的旧路径**不被改写** |
| 连续启动 3 次 | `test_relocation_is_stable_across_repeated_launches` | 每次都 `ACTIVE`，无自我改写 |
| 配方**内部**路径全指向已删除 worktree | `test_a_recipe_naming_a_deleted_worktree_still_launches_from_here` | 起机用现位置；`argv` 里没有任何一项还指向旧目录；所有钉住文件都在 |
| 仓库启动器升级（字节变） | `test_an_upgraded_bootstrap_is_not_an_environment_change` | 判为同一套配置；改 `main.py` / 换实例 / 改审片 manifest → 仍阻断 |
| 在新位置重新封印一次服务证据 | `test_a_resealed_validation_is_not_invalidated_by_the_directory` | `READY`，零 blocker |
| 真的改了渲染器文件 | `test_a_really_changed_renderer_file_still_blocks` | `INVALID_REQUIRES_REVIEW` + `ACTIVE_RUNTIME_CONFIGURATION_CHANGED` |
| 真的改了配方 | `test_an_edited_recipe_still_blocks` | 同上 |
| 检索结果跨会话 | `test_the_scan_index_survives_a_new_session` | 仍读得到 |

**行为级基线红→绿**（只用公开入口 `resolve_runtime` / `runtime_report`）：

```
# main@f28e8fe（stash 掉本轮改动）
r3-recheck          : BLOCKED ACTIVE_RUNTIME_CONFIGURATION_CHANGED / services_verified=False
witch-main-20261111 : BLOCKED ACTIVE_RUNTIME_CONFIGURATION_CHANGED / services_verified=False

# 本轮修复后
r3-recheck          : OK provider=comfyui  recipe=<当前数据目录>/comfyops/setup/managed_recipe.json
                      ACTIVE / comfyui / None / services_verified=True
witch-main-20261111 : OK provider=comfyui  recipe=<当前数据目录>/comfyops/setup/managed_recipe.json
                      ACTIVE / comfyui / None / services_verified=True
```

生产模式投影（`summarize_production_mode`）：两个库都从「真实制作暂不可用」回到
**`READY` / 真实制作已就绪**。

---

## 4 · 归并 / 删除的旧判断与旧执行路径

| 旧东西 | 处置 |
|--------|------|
| `runtime_configuration.RECIPE_RELATIVE` + 带 `identity` 参数的 `recipe_path()` | 删除；`recipe_path(settings)` 只有一份，落在 `managed_recipe`（配方模块自己说清配方在哪） |
| `image_renderer.RECIPE_RELATIVE_PATH` + 它自己的 `recipe_path()` | 删除，改为引用同一份定义 |
| `service_validation` 三处 `load_managed_recipe(identity["recipe"], ...)` | 改为 `load_runtime_recipe(...)`；记录路径不再被当成可访问路径 |
| `worker._managed_recipe` 直读 `load_managed_recipe(path)` | 改为 `load_runtime_recipe(..., path=path)`，`SHINE_MANAGED_RECIPE` 指到旧目录也能锚定 |
| `record_candidate(..., recipe_path)` 参数名遮蔽同名函数 | 改名 `path` |
| `configuration_identity` 里三处零散 `_anchored(...)` | 删除：锚定在加载配方时一次完成 |
| `_advisory_pin_names`（只排除审片） | 并入 `_workbench_pin_names`（排除仓库自带脚本；审片 manifest 回归为安装事实） |
| `.env.example` 里写死本机绝对路径的示例 | 改为说明「默认用 SHINE_DATA_DIR 内的配方」 |
| `.env` 里的 `SHINE_SETUP_RECIPE` | 删除：代码只写不读，且指向副本，纯残留 |
| `AGENTS.md` 回归示例 `cd <worktree>` | 改为 `cd <repo>`，不再诱导另建副本 |

---

## 5 · 「钉在非主仓 / 副本 / 非主分支」专项排查

排查面：主仓全部代码与脚本（`backend/`、`frontend/src`、`scripts/`、`config/`、
`.github/workflows/`）、入库配置、`.env*`、git worktree/分支、真实运行库台账。

| 类别 | 结论 |
|------|------|
| 代码里写死 worktree / 副本路径 | **0 处**。`backend/`、`frontend/src`、`scripts/`、`config/` 内没有 `shine-video-studio-*` 变体名，也没有非本机的盘符绝对路径 |
| `git worktree` | 只有主仓一条（`F:/ClaudeWorkSpace/projects/shine-video-studio [main]`），无残留注册 |
| 分支 | `main`，本地领先 `origin/main` 1 个提交（上一轮 `f28e8fe`），没有在别的分支上开发 |
| 启动器 | `启动 Shine 工作台.cmd` 用 `%~dp0`，`scripts/start_dev.ps1` 默认 `<repo>/.local-data` —— 都是「主仓相对」 |
| `ShineLauncher-build-info.json` 的 `source_commit=f41e609` | 在 `main` 历史里，是正当构建记录 |
| `config/` | 只有模板（`managed_recipe.json.example` 等），无绝对路径 |
| `.github/workflows` | 无写死路径、无 `working-directory` 指向副本 |
| 台账里的绝对路径 | 三类：`F:\AIGC2026\...`（机器安装，正确）、当前数据目录（自引用，正确）、`handoff:*` 证据里的 `history_path` 指向 `witch-ui-20261109`（**历史审计**，代码只在内存里用 `self._drain`，不重读旧值） |
| `installation_plans.payload.storage_root` | 自引用；只在 `target_holder`/`target_identity` 里**比较**，不直接当路径用 |
| `docs/` 与 `.workbuddy/memory/` 里的 worktree 路径 | 历史验收记录（记录当时在哪个副本做过），保留；不参与运行 |

**真正钉在副本里的只有本机运行配置**（未入库）：

1. `.env`：`SHINE_DATA_DIR=F:\...\.local-acceptance\witch-main-20261111` —— 数据目录停在一个
   **验收副本**里，副本一旦清理就会「丢项目」。
2. `PYTEST_DEBUG_TEMPROOT=F:\ClaudeWorkSpace\.pytest-tmp` —— 指向已 100% 占满的 F: 盘，
   全量回归会变成整片 `OSError: could not create numbered dir` 的**假失败**（本轮实测：
   8 分片全 `exit=120`，改到 C: 盘后恢复）。

处置：入库的两个（`.env.example`、`.env` 的残留变量）已改；`.env` 的数据目录**未擅自改动**
（换库会让操作员看不到已有项目），列在「仍待验证」由操作员裁定。

---

## 6 · 执行记录（回归）

### 6.1 三轮全量的过程与结论（两次都是「环境造成的假失败」，要记清楚）

| 轮次 | 临时根 | 结果 | 定性 |
|------|--------|------|------|
| 第 1 轮 | `PYTEST_DEBUG_TEMPROOT=F:\ClaudeWorkSpace\.pytest-tmp`（8 进程共享，该目录下已堆 46 个 `pytest-N`） | 8 分片**全部** `exit=120`，整片 `OSError: could not create numbered dir ... after 10 tries` | 八个进程抢同一个 temproot 的编号锁，与代码无关 |
| 第 2 轮 | `--basetemp` 在 **C:（系统盘）**，每片独立 | 4 片 `exit=1`，共 5 项失败 | 4 项是**上一轮就已存在的基线失败**；第 5 项 `test_setup_chain_convergence` 是 `require_storage` 的 **`CHOOSE_DEPLOYMENT_DIRECTORY_OUTSIDE_SYSTEM_DRIVE`**——产品规则拒绝把部署目录放在系统盘，测试数据落在 C: 必然触发 |
| 第 3 轮 | `--basetemp` 在 D:（非系统盘），每片独立 | 31 分钟只跑到 20% | D: 盘 IO 太慢，中止，不作为口径 |
| 第 4 轮 | `--basetemp` 在 **F:（非系统盘）**，每片独立 | 28 分钟跑完：**4 片 `exit=0`，4 片各 1 项失败（共 4 项）** | 这 4 项全部是**上一轮就存在的基线失败**（见 6.3）；本轮改动**零新增失败** |

`df` 在本机把 F: 误报成「0 可用 / 100%」；`Get-PSDrive` 的真实值是 **F: 剩 179.5GB**，
C: 只剩 11.8GB，D: 剩 554.9GB。所以第 1 轮的问题不是磁盘满，是**共享 temproot 的编号竞争**。

### 6.2 有效口径（第 4 轮，F: 盘非系统盘 + 每片独立临时根）

```bash
OUT=/f/shine-pytest-r4
export CODEBUDDY_SAFE_DELETE_ENABLED=0; unset PYTEST_DEBUG_TEMPROOT
ls backend/tests/test_*.py > "$OUT/all-tests.txt"      # 264 个文件
split -n r/8 "$OUT/all-tests.txt" "$OUT/chunk-"
for f in "$OUT"/chunk-??; do
  n=$(basename "$f")
  backend/.venv/Scripts/python.exe -m pytest $(tr '\n' ' ' < "$f") -q -p no:cacheprovider \
    --basetemp="F:/shine-pytest-r4/bt-$n" > "$OUT/$n.log" 2>&1 &
done; wait
```

| 分片 | 退出码 | 失败用例 |
|------|--------|----------|
| chunk-aa | 0 | — |
| chunk-ab | 0 | — |
| chunk-ac | 0 | — |
| chunk-ad | 0 | — |
| chunk-ae | 1 | `test_audit_event_codes`（基线） |
| chunk-af | 1 | `test_material_director_flow`（基线） |
| chunk-ag | 0 | — |
| chunk-ah | 1 | `test_director_streamline`、`test_story_unknown_resend`（基线） |

关键对照：第 2 轮在 C: 失败的 `test_setup_chain_convergence::test_a_verified_search_candidate_is_reused_instead_of_downloaded`，在本轮**通过**（4/4）——坐实了它是「部署目录在系统盘」这一条产品规则，不是并发、也不是代码。


### 6.3 上一轮就存在的 4 项基线失败（本轮未修，不属本轮范围）

这 4 项在 `main@f28e8fe` 上同样失败（上一轮已用 stash 复跑确认），本轮改动不涉及：

```
test_audit_event_codes   :: 前端码表漏了 MATERIAL_SCENE_AUTO_RETRY
test_material_director_flow :: 期望 conflicts 字段（MATERIAL_SCENE_KEY_UNKNOWN 相关投影）
test_story_unknown_resend :: 两场调用之间只记录到 1 个时间戳
test_director_streamline :: 旧 scope 包并存时的计划投影（EPISODE_PLAN_NOT_MATERIALIZED）
```

其余检查：改动文件 Ruff 全绿；前端全量 `npx vitest run --no-file-parallelism`
**78 文件 / 395 例全过**；`tsc --noEmit` 通过；`npm run build` 通过（1645 modules，2.46s）。


---

## 7 · 仍待验证 / 已知限制

1. **F: 上的历史 pytest 临时目录 124GB**：`F:\ClaudeWorkSpace\.pytest-tmp\pytest-of-Administrator`
   下积了 46 个 `pytest-N` 目录，且 pytest 自己的清理没有生效（这也是第 1 轮编号抢不到的
   背景）。本轮已把我自己占的临时目录全部清掉（C: 回 73.9GB、D: 全清、F: 释放 62.5GB），
   但这份历史遗留不在本轮授权范围内，未动。
2. **4 项基线失败**（码表、素材冲突投影、计划投影、两场调用间隔）在 `main@f28e8fe` 上
   同样失败，本轮改动不涉及，需要单独一轮处理。
3. **`.env` 的数据目录仍在验收副本里**：`witch-main-20261111`。要不要迁到主仓
   `.local-data`（或独立目录），需要操作员裁定——迁移前请先确认副本会被保留多久。
4. **配方字节仍算配置事实**：如果在新位置「重新生成」一份配方（内容因路径而变），
   `recipe_sha256` 会变，此时需要重新验证服务一次。这是有意的：配方变了就是新配置。
5. 本轮未改导演链路、素材链路与字幕链路，不触发 VLM 重校准义务。
