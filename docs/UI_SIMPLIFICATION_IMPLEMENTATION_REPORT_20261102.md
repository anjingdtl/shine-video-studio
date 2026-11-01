# UI 收束实施报告（2026-11-02 轮）

状态：**已实施并完成隔离软件/浏览器验证；真实 LLM、真实安装、真实 H3 均未执行**。
配套方案：docs/UI_SIMPLIFICATION_PLAN_20261102.md（验收矩阵 N01–R01）。

- 起始基线：`main` / `6049bb8f21b5619f1e783e34a57f4540295b023b`
- 结束提交：`0b364e387481e04304301d9a85802db465f7cca5`（分支 `codex/ui-simplification-20261102`，未推送）
- 分支提交：60195f9 → aca5c84 → f4f55a4 → a1be643 → b290c64 → 0b364e3
- 个人数据保护：未触碰个人 SQLite/项目/素材/模型/ComfyUI 配置；未跟踪文件
  （scripts/start_dev.ps1、桌面快捷方式、两份本轮文档）保持原状。全部验证使用
  `.local-acceptance/ui-round-20261102/` 下的临时数据目录与随机端口，验证后进程已停止。

## 1. 实现映射（方案 → 提交）

| 方案条目 | 实现 | 提交 |
| --- | --- | --- |
| §4 导航紧凑可读 | styles.css：删除末尾 `.nav-group{flex:1 1 auto}`（根因），导航容器成为唯一滚动元素（min-height:0 + overflow-y:auto）；组头 38px、子项 36px、组间距 7–12px、13px 文字；组标题=切换视图，独立箭头开关（aria-expanded）负责展开/收起其他组 | 60195f9 + 0b364e3 |
| §5 制作环境页 | SetupPanel 默认区改为：检测本机（只读、无需项目）→ 结论块（基础环境/安装空间/生成服务/AI 助手/真实出片分列）→ 按用途分组模型卡（视频生成/画面检查/语音检查/程序与节点，状态=尚未检测/待检查目录/发现文件待校验/未在已检查位置发现/已校验复用）→ 复用与自动配置沿用既有确认链；工程细节留在高级 details | f4f55a4 |
| §6 作品风格 | 新增 frontend/src/styleCatalog.ts（版本 2026-11.1 + FNV 目录哈希）：六套完整七维预设 + 每维确定性选项；StylePanel 改为整套风格下拉 + 七维下拉 + 每项自定义（原文逐字保留，选项按精确字符串匹配，不做关键词猜测）；`02 / STYLE BIBLE` 改为「作品风格」 | aca5c84 |
| §7 AI 推荐 | 风格推荐走既有 `POST /projects/{id}/film-agents/{role}/run`（tool=style_proposal，导演优先路由），建议面板显示逐项差异与理由，采纳走 `POST .../style-draft`；未配置时显示原因 +「去配置 AI 助手」，预设/手工流程不受影响；首次使用需用户显式点击「启用官方规则包」（幂等） | aca5c84 |
| §8/§9 生产收束 | ShotPanel 人物选择器=本项目已批准人物主资产 entity_id + 本项目镜头已用人物名，新增人物走显式输入（不建主资产）；说话人/景别/运镜下拉值写入真实 POST spec（characters/dialogue.speaker_id/camera.shot_size/movement）；序列按项目顺序提交（不按勾选顺序）、预选唯一 Ref2VA 方案、全选可制作镜头并显示排除数；交付可视化片段表（入出点写回同一 EDL JSON 状态），JSON 移入高级 details | a1be643 |
| §13/§15 文案 | 前端文案与测试全部使用中文用户语言；后端枚举未改动 | 全部 |

## 2. 后端合同变化（最小）

- `POST /projects/{id}/film-artifacts/{artifact_id}/style-draft` 现在要求 body
  `{ base_style: StyleBible | None }`：服务端在应用建议前核对「用户当时看到的草稿」，
  不一致返回 409 `STYLE_DRAFT_CHANGED`（并发/过期建议保护，方案 §7.2）。旧无 body 调用
  会得到 422；唯一调用方是本轮前端。
- 其余后端零改动：唯一 H3/ComfyUI 路径、H3PromptCompiler、parameter_map、槽位、
  五道门禁、冻结 grant/expected_style/快照核对链、Worker/租约/series 合同全部未触碰。

## 3. 验证结果（真实命令与数量）

全部从仓库根目录执行；本轮未更新锁文件，`--locked` 语义由既有 venv 保持一致。

| 检查 | 命令（pytest 计数取自日志） | 结果 |
| --- | --- | --- |
| 后端定向集（8 个文件） | `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_style_versions.py test_style_approval_concurrency.py test_film_crew.py test_storyboard.py test_existing_installation.py test_new_machine_setup.py test_production_series.py test_managed_series.py` | **109 passed**（exit 0，日志 `.local-acceptance/ui-round-20261102/.local-acceptance-ui-round-20261102-targeted.log`） |
| v13 + 离线 E2E | 同上解释器 + `test_v13_preparation.py test_v13_migrations.py test_v13_e2e.py test_offline_e2e.py` | **65 passed**（exit 0） |
| 后端全量 | `python -m pytest backend/tests` | **1328 passed, 1 skipped**（gated 真实 ComfyUI 探针）；**2 failed：test_bootstrap_entry.py 两项** |
| bootstrap 失败归因 | 在基线 `6049bb8` 临时 worktree 复跑同两个测试 | **基线同样失败**（子进程输出 GBK→UTF-8 解码错误，本机环境性，非本轮引入；原始错误已保留在全量日志） |
| Ruff | `python -m ruff check backend/shine backend/alembic backend/tests` | All checks passed |
| Alembic 隔离 | `SHINE_DATA_DIR=<isolated> python -m alembic -c backend/alembic.ini upgrade head` | 迁移到 head 成功（exit 0） |
| 前端测试 | `npm.cmd --prefix frontend test` | **32 passed**（8 文件；含本轮新增 styleFreeze 重写、shotDraft P02、seriesOrder P04、deliveryRefresh D01） |
| 前端构建 | `npm.cmd --prefix frontend run build`（含 tsc -b） | built in ~1.1s |
| git diff --check | `git diff --check` | 通过（exit 0） |

## 4. 浏览器验证（隔离实例，真实 Chromium + IAB DOM 交互）

隔离栈：临时数据目录 + 随机回环端口（API 14923 / UI 14924），mock 模式，验证后停止。
截图存 `.local-acceptance/ui-round-20261102/shots/`（本机留存，不提交）。

- **N01/N02**：1366×768 与 1920×1080 实测几何：组头 38px（合同 36–40）、子项 36px（34–38）、
  组间距 7–12px、导航容器为唯一滚动元素（overflowY=auto，未溢出时不滚动）；四板块/展开/高亮/
  `#setup` 深链（无项目落地）、前进后退锚点行为未改动。
- **E01**：无项目 #v/setup 落地：检测本机可见、检测只读说明、无需项目；高级细节在 details 内。
- **S01**：UI 选「自然写实」→ 保存草稿 → 服务端 `style_draft` 七项与目录预设**逐字一致**
  （curl GET 比对）；不打字即可保存。
- **S02/历史自定义**：styleFreeze 前端回归覆盖「自定义（原内容）」路径：旧自定义七项
  逐字往返，冻结合同（保存→一次性授权→expected_style→服务端核对）6 项测试全过。
- **A01**：无 LLM 配置时推荐区显示「尚未配置 AI 助手 + 去配置 AI 助手」，预设/手工可用。
- **U01 部分**：暗色主题切换后 body/card 背景色计算值正确；键盘焦点可达导航按钮（programmatic
  focus 验证；:focus-visible 样式存在）。125%/150% 缩放、窄屏与长名称：NOT RUN（浏览器截图面
  在本会话不可用，见 §6）。
- **A02/A03（合同层）**：后端 adopt 并发合同测试（409 STYLE_DRAFT_CHANGED）+ 前端 stub 测试
  覆盖双击去重、跨项目隔离、失败保留。真实 LLM 调用 **NOT RUN**（无既有授权凭据，方案 §7 允许）。
- **P02**：前端 stub 测试证明人物/说话人/景别/运镜选择值进入 POST spec 并被服务端 schema 校验；
  浏览器目视核对镜头表单正常渲染。
- **P04（Web 发起）**：seriesOrder 测试证明按项目顺序提交、唯一方案预选、双击不重复提交。
  真实 Worker 连续生产：NOT RUN（无真实 H3）。
- **D01**：deliveryRefresh 新增用例：可视化入点编辑写回提交体（1.5s），无需手写 JSON；
  待审样片标识沿用既有逻辑（未改动）。
- **P03**：映射缺失/尺寸冲突/缺审片阻断未改动（test_production_series / test_managed_series /
  test_offline_e2e 全过）。未重跑浏览器目视核对（NOT RUN 部分）。

## 5. 交付快照证据（1366×768）

截取于隔离实例（无项目 setup 页 + 已保存预设的风格页），证明：四板块自然高度、
「制作环境」标题、检测本机只读说明、作品风格预设下拉 + AI 推荐空配置引导。

## 5b. 实施期间修复的自身缺陷

- 自定义文本编辑时清空 textarea 会卸载控件（StylePanel customActive 派生缺陷），
  编辑中强制保持自定义模式修复；styleFreeze 反例回归覆盖。
- 序列提交顺序曾是勾选顺序（P04 反例），改为按项目镜头顺序过滤提交。
- 导航组双箭头视觉冗余（head 内 + 开关各一），head 内箭头移除。

## 6. NOT RUN / 边界（如实）

- **真实 LLM**：风格推荐与分镜链未调用真实 provider（无既有授权凭据；按方案 §7 完成代码 +
  合同测试，能力检测/路由/同意门禁为既有实现）。
- **真实安装/复制/下载**：未执行（既有审批链未动）。浏览器验证期间的检测/盘点为只读 mock 栈。
- **真实 H3/真实出片**：未执行；Mock 证据不作为真实验收。
- **人工视觉验收**：本轮截图由无头 Chromium 生成并通过几何测量交叉验证；IAB 截图面在当前
  会话不可用（screenshot surface 超时），125%/150% 缩放与窄屏目视 NOT RUN，留待用户在真机浏览器
  人工复核。
- **Windows hosted CI**：未运行（NOT RUN），本地全量套件通过。
- **未跟踪文件**：scripts/start_dev.ps1 与桌面快捷方式保持未跟踪原状；本轮两份文档
  （方案 + 本报告）为新文档，是否入库由用户决定。

## 7. 用户视角的变化

- 少填：七段风格文案（预设/下拉直达）、人物名手输（项目实体选择器）、景别/运镜手输
  （下拉）、EDL JSON 手写（可视化片段表）、方案选择（唯一方案自动预选）。
- 已接真实 API：风格 AI 推荐链（run → 差异展示 → 采纳 → base_style 并发保护 → 冻结不变）。
- 检测/复用/安装链路保持既有确认与门禁，检测零写入。
