# 大纲版本管理修复验收（2026-11-14）

## 症状（用户报障）

大纲确认一次后，编辑正文「保存修改为新版本」，界面没有任何状态变化：版本号停在旧版、
「已确认」徽章常亮、确认按钮消失，用户无法判断新草稿是否存在、是否真的在重做大纲；
已确认后点「重新提取」也看不到任何进度，不知是否在重跑。

## 根因（三个，均已修）

1. **面板以任务附带的旧产物为当前版本**。`OutlineConfirmationPanel` 的横幅/徽章/确认按钮
   全部读 `tasks[].outline`（提取任务产出的旧 artifact，`approved=true`），而新草稿不挂在
   任何任务上。保存新版本后 `!outline.approved` 为假，确认入口被隐藏，横幅不变。
2. **未确认草稿顶掉全项目确认依据**。`director/basis.py::outline_for_document` 取「最新
   非 stale」大纲（含未确认草稿），`_confirmed_outline` 再要求它 approved → 草稿一存在，
   整个项目的确认依据变 None：journey、分集推导、导演包脚本引用全部翻成「大纲未确认」。
   这是「保存新版本后状态乱掉」的后端根因。
3. **相同输入重新提取谎报排队**。`queue_outline` 对相同（剧本+锁定事实+provider）身份
   复用旧 SUCCEEDED 任务（不重跑模型，正确），但前端统一提示「已排队」，用户以为在重做。

## 修复

- `GET /projects/{id}/director/outline` 增加 `versions` 版本链（同文档、最近 30 版，含
  `approved/stale/user_edited/revision_of/confirmed/sha256/created_at`），并支持
  `?artifact_id=` 读取指定版本正文（404 具名）。
- `outline_for_document` 语义改为：**优先该文档最新已确认版本；从未确认时才返回最新草稿**
  （legacy 无 document_id 的回退保留，且 legacy 已确认优先于本文档草稿）。所有消费方
  （`_confirmed_outline`、`_basis_view`、`_script_ref`、journey、basis 阻塞器）语义不变即被修复。
- 面板以 `director/outline` 视图为当前版本真相，任务列表只负责进度：
  - 保存新版本后横幅立即「待你审阅 · 用户编辑稿」、徽章「待确认大纲」、rail/内联确认入口出现、
    提示「当前确认依据是版本 X」；
  - 版本历史折叠列表：vN 编号、时间、确认依据/当前最新/用户编辑/已失效标签，
    「确认此版」「查看正文」（旧版载入编辑器，保存以旧版 sha 为基础）；
  - 编辑器 dirty 跟踪，未改动不可保存；
  - 重新提取按服务端回执如实提示：QUEUED/RUNNING=已排队；SUCCEEDED=「复用已有大纲结果，
    没有重跑模型。要重新生成请修改锁定事实或导入新剧本」。
- 保存新版本是同步草稿创建，**无需模型调用**；提示文案写明这一点，消除「是否在重做」的歧义。

## 定向回归（用户裁定：不做全量）

后端（仓库根，`CODEBUDDY_SAFE_DELETE_ENABLED=0`）：
- `test_director_streamline.py` 17 例（含新增 `test_outline_version_chain_tracks_draft_and_confirmed`：
  版本链、指定版本读取、404、**草稿挂起时分集推导仍 201**）；
- `test_story_preparation.py` 9 例、`test_story_outline_unblock.py` 5 例；
- basis 语义影响面：`test_director_basis_grouping.py` 4、`test_story_section_run.py` 3、
  `test_story_continuity.py` 3、`test_literary_memory.py` 13、`test_literary_low_blocking.py` 4、
  `test_director_long_form.py` 27。合计 85 例，0 FAILED/ERROR。
- `ruff check` 改动三文件通过。

前端：
- 新增 `outlineVersions.test.tsx` 3 例（保存后草稿+确认入口+历史+确认 v2；复用如实提示；
  旧版载入后以旧版 sha 保存）；`storyOutlineUnblock.test.tsx` 7 例；
  `directorEntries/setupEntry/materialPreparation/directorContinuation` 43 例。合计 53 例全绿。
- `tsc --noEmit`、`npm run build` 通过。

## 浏览器实测（真实工作台 + 真实 MiniMax 调用）

隔离作品 `d7aa4250-c4bf-4dd8-85c2-42875b22f92f`（outline-version-live-test），
剧本 `53164f24…`（两场短剧本）：
1. 授权文字发送 → 提取：进度真实透传「逐场提取剧情要点（1/2，50%）」→ 100%；
2. 确认 v1 `fc3f0260…` → 徽章「大纲已确认」、历史 v1 确认依据；
3. 编辑保存 v2 `300db96b…` → 横幅立即「待你审阅 · 用户编辑稿」、徽章「待确认大纲」、
   rail 出现「确认这版总体大纲」、历史 v1 确认依据 + v2 当前最新/用户编辑/确认此版；
4. **草稿挂起时 `GET /journey` 仍 `outline_confirmed=true` 且指向 v1**（根因 2 的反证）；
5. 确认 v2 → 横幅「你已确认 · 用户编辑稿」、历史 v2 确认依据；
6. 相同输入重新提取 → 页面提示「复用已有大纲结果，没有重跑模型…」，无假排队。

## 边界与未做

- 未改 `worker.py` / `preproduction.py`，不触发 `client_review_identity` 重校准义务；
  VLM 正负校准仍未做，不得声称已校准。
- 按用户裁定只跑定向回归，未跑后端全量 8 分片与前端全量。
- 隔离实测作品留在数据目录（项目管理可删）；正式作品未被本轮改动。
- 不涉及真实 H3 出片；无新的硬件验收结论。