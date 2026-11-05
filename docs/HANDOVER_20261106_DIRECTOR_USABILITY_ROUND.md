# 交接：导演模式可用性轮（2026-11-06）

- 分支：`codex/director-closeout-20261105`（相对 `main` 领先 13 个提交），工作树干净（仅未跟踪 `.zcode/`，会话工件，不提交）。
- 真实栈：`http://127.0.0.1:8133`（API）/ `5193`（Web），数据目录 `.local-acceptance/live-browser`，
  `render_provider=comfyui`、`runtime=ACTIVE`、`mode=READY`、`worker_online=true`。写操作带 `x-shine-local-action: 1`。
- 用户授权：local-user 代理操作（评审、晋升、冻结、排分镜、启动 H3）——沿用上一轮授权。
- **本轮以用户要求中止**：后端全量套件在跑到约 50 分钟时被中止，**没有拿到最终结果**（见 §4）。

## 1. 本轮做了什么（用户反馈的 5 个问题）

| # | 反馈 | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | 素材与风格页切走再回来又变成「① 读剧本」 | 服务端返回 `stage="materials_ready"`，前端只映射三个阶段，其余落到 `read_script` | 补全四阶段映射；用户展开的阶段记在本地 `openStage`，不被自动推进覆盖 |
| 2 | AI导演制作页切走再回来回归初始状态 | 页面块切页时被卸载；`autoStage` 一变就清空用户的 `stageOverride` | 三个导演页面保持挂载、只切 `hidden`；`stageOverride` 只在换作品时清空 |
| 3 | 成片导出页被文字占满；点预览没反应；成片中心回来没有预览片段 | 预览指向**不存在的路由** `/api/v1/jobs/{id}/media`（404）；分段卡把整段 `story_beat` 铺在卡面 | 新增 `GET /api/v1/jobs/{job_id}/media` 与 `GET /api/v1/jobs/{job_id}/thumbnail`（均带摘要校验）；分段改海报网格，长描述折进「镜头描述」 |
| 4 | 素材与风格页的报错与素材占满视觉范围 | 候选按**批次**分行，一行一两张图、其余留白；批次 id / attempt / 状态码常驻 | 全部候选汇入一个密排网格；批次信息折进「批次与技术信息」；风格对照与错误码都收进可展开区 |
| 5 | 角色用途固定 / 段间衣服场景衔接 | **用户已澄清**：第一段（林素）与第三段（顾晚）本就是不同人物，不构成衔接问题 | 核实而非改绑定：每人一份已批准锁定的主素材，在其出现的镜头以 `use_as=subject` 绑定；三个镜头是剧本的三个不同场景，导演给的 `continuity.mode` 全为 `cut`，不存在跨场服装衔接合同 |

### 1.1 一个只有实测才会暴露的 CSS 缺陷

1、2 两条的修复第一次**没有生效**：DOM 里 `<div id="assets" hidden="">` 带着属性，页面上却照样渲染出素材面板。
原因是样式表里只有**作用域规则** `#produce[hidden]{display:none!important}`，没有通用 `[hidden]` 规则，
而 `.director-page{display:flex}` 盖过了浏览器默认的 `[hidden]{display:none}`。
改成通用规则 `[hidden]{display:none!important}` 后隐藏才真正生效。
（这解释了为什么前端单测全绿仍有此现象：jsdom 的默认样式表里 `hidden` 是有效的。）

### 1.2 顺带修掉两处会让整页崩掉的空值读取

风格面板在七项未填时 `style[field].trim()` 抛 `TypeError`；
基础配置面板在环境检测未返回 `gpu`/`ram`/`disk`/`port` 段时读 `undefined.name`。
面板现在对缺失字段给出可读值。这两处是「页面保持挂载」的前提：一旦某页崩掉，整棵树都没了。

## 2. 新增要求：素材文件命名

要求：生图文件名自动改成人名 / 物品名 / 场景名并前置编号，便于匹配剧本与分镜。

- 规则：`backend/shine/materialprep/naming.py`，文件名 = `{编号}-{实体名}.png`，
  编号取该实体**在剧本里第一次出现的场次**（不是台账字母序），无对应条目的手工素材保留人自己的文件名。
- 出图（`materialprep/execution.py`）与晋升（`comfyops/assets.py`）两处都用它。
- 本项目已晋升的 12 个主素材按同一规则重命名，并写下 `MATERIAL_FILENAME_RENAMED` 审计事件：
  `01-林素` / `02-黄铜怀表` / `03-旧船坞·夜` / `04-周野` / `05-旧船票` / `06-暖黄灯罩` /
  `07-正在修理的表` / `08-齿轮与镊子` / `09-钟表铺（白昼）` / `10-顾晚` / `11-老式煤油灯` / `12-海边石阶 · 黄昏`。

## 3. 浏览器实测（真实栈，2026-11-06）

- 素材与风格：切到成片中心再回来，仍停在「③ 选择素材」，网格与工具条（已选用 0 项 · 候选 12/21 · 失败 9）不变。
- 候选网格：一行 6 格，每格一图一行说明（场景 · 钟表铺（白昼）/ 生成 v2 · 已晋升），
  「尚未出图」的条目（需要澄清的两项）**也占一格**，不再从网格里消失。
- 成片中心：分段网格三张海报（真实帧）+ ▶ + 整段一次生成 + 下载此段；
  点缩略图弹播放器并真的播放（0:00 / 0:07）。
- 完整成片预览：切走再回来，`<video>` 仍在（预览状态不再被重置）。
- AI导演制作：切到我的作品再回来，活动阶段仍是「① 剧本分镜」，折叠行仍是「已应用 3 镜」。
- 新路由实机返回：`/api/v1/jobs/{id}/media` → 200 `video/mp4` 2,411,406 字节；
  `/api/v1/jobs/{id}/thumbnail` → 200 `image/jpeg` 6,673 字节。

## 4. 回归状态（请注意第 1 条）

| 检查 | 结果 |
| --- | --- | --- |
| Ruff（`ruff check shine tests`） | **All checks passed!** |
| 前端 vitest | **19 文件 / 136 测试全绿** |
| 前端 `tsc -b && vite build` | **通过** |
| 后端定向套件（`test_job_media.py` 5 + `test_material_naming.py` 2 + `test_material_preparation.py` + `test_material_director_flow.py`） | **44 项通过** |
| **后端全量套件**（仓库根 `python -m pytest backend/tests -q`） | **被用户要求中止，未拿到最终结果 —— 记为未验证。**上一轮（提交 `9705b8a` 之前的树）全量是 1691 项收集 / 1690 passed / 1 skipped / EXIT=0；本轮新增 7 项（`test_job_media.py` 5 项、`test_material_naming.py` 2 项），已单独跑通，但**全量未复跑** |
| Alembic | 本轮**无迁移改动**，未复跑；上一轮全新库 upgrade + `check` 为 `No new upgrade operations detected` |

## 5. 本轮提交

- `a6deac5` feat(director): keep the workbench state, preview real shots, name the material
  （后端 2 个新路由 + 命名模块 + 2 个新测试 + 前端 4 个组件 + 样式 + 文档）
- 更早的提交见 `git log`。

## 6. 后续接手者要做的事

1. **在仓库根跑一次后端全量**：`backend\.venv\Scripts\python.exe -m pytest backend/tests -q`
   （必须**在仓库根**跑：`backend/tests` 里的测试按 CWD 解析仓库根脚本
   `scripts/managed_comfy_bootstrap.py`，在 `backend/` 目录跑会误报 2 个失败）。
   跑完后把结果写进 `docs/ACCEPTANCE_20261105_DIRECTOR_CLOSEOUT_REPORT.md` §7 的表格，
   并在 §13 末尾注明「全量套件已于 <时间> 复跑，结果 <…>」。
2. **从最终提交树重建安装包**：`pwsh -File scripts\build_test_package.ps1 -OutDir releases -Verify`
   （仓库必须干净；旧包保留）。本轮尚未重建——上一次包是 `ShineVideoStudio-20261105-5d9ebee-win64.zip`，
   **不含本轮的界面与命名改动**。
3. 复核 `docs/ACCEPTANCE_20261105_DIRECTOR_CLOSEOUT_REPORT.md` §13/§14 的事实与实际一致。
4. VLM 重校准仍 **BLOCKED**（正式项目库不在本机，钉住文件本轮未改）。

## 7. 未做 / 不该假装做过

- 后端全量套件本轮**未验证**（被中止）。
- 成片的终审仍是人看：本机没有 VLM/ASR 审片模型，三镜是 `PASSED` 且 `acceptance_status=pending_review`。
- 宽槽位 Ref2VA 方案 `local-h3-ref2va-pdd8-6img` 仍是**本项目私有**方案，未进部署目录。
- `.zcode/` 是会话工件，不要提交。
