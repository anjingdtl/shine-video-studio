# Shine P1 第二轮收尾：资源整备与 H3 生产准入解耦（RTX 4070 实障修复）

- 日期：2026-11-14
- 基线：`6fbf498aa448ed0e9db20b968d8c6915a4434478`（`main`）
- 分支：`fix/p1-setup-resource-gate-decoupling-20261114`
- 触发：真实用户报障 —— RTX 4070 / 32GB / 503GB 可用 / ComfyUI 已有 / H3 INT8 本机 3 个权重文件已找到，
  在「制作环境」勾选素材出图并点「确认并下载安装（约16.1GiB）」，**没有开始下载**，收到
  `HOST_NOT_QUALIFIED`，同时界面出现 `[object Object]; [object Object]`。

---

## 1. 根因定位

### 1.1 前端：H3 安装计划由「综合就绪状态」触发，而不是由「用户选了什么」触发

`frontend/src/components/SetupCenter.tsx::prepare()`（修复前）：

```typescript
const filesReady = ready.layers.some((l) => l.layer === 'L2_files_verified' && l.status === 'READY');
if (!filesReady || !h3Complete) {
  // 创建 H3 安装计划（STRUCTURE_COMPONENTS + 选中变体权重 + 审片组件）
}
if (imageWant) {
  // 创建 Qwen Image 安装计划
}
```

- `filesReady` 是**全局 L2 综合就绪层**，与"用户勾了素材出图"无关；只要本机综合就绪未通过，
  前端就**先**创建 H3 安装计划。
- 该请求携带 H3 INT8 变体组件 → 后端解析出生产变体 → RTX 4070 实测 12282 MiB < 门槛 15360 MiB
  → `qualification.status = BLOCKED` → **HTTP 409 `HOST_NOT_QUALIFIED`**。
- 409 抛出后 `prepare()` 整体进入 `catch`，**第二段图片计划根本没有提交**，所以"点了没反应"。
- 另外，H3 计划里还混入了审片组件（`qwen3-vl-8b-fp8` / `qwen3-asr-0.6b`），
  审片权重被 H3 的显存门禁一起挡住。

### 1.2 后端：变体解析没有把「清单」当作边界

`backend/shine/comfyops/deployment.py::resolve_variant()`（修复前）遍历**全部**变体 manifest，
只要某变体的 `required_component_ids ⊆ requested` 就绑定该变体，与本次请求用的是哪份资源清单无关。
因此"图片计划"在结构上并没有"绝不绑定 H3 生产变体"的保证，只是当前组件集合碰巧不匹配。

### 1.3 后端：`HOST_NOT_QUALIFIED` 之后没有留下"还能做什么"的信息

409 的 detail 只有 `code / message / variant_id / status / blockers / unknowns`，没有能力归属，
前端无法知道"这只是 H3 的事"，于是整条链当成一次总失败。

### 1.4 前端：`blockers` 是对象数组，被直接 `join()`

`frontend/src/api.ts::describeDetail()`：

```typescript
const coded = detail as { code: string; message?: string; blockers?: string[] };
const blockers = Array.isArray(coded.blockers) && coded.blockers.length
  ? `：${coded.blockers.join('；')}` : '';
```

`blockers` 实际是 `[{code, required, actual}, ...]`，`Array.prototype.join` 把每个对象
`toString()` 成 `[object Object]` —— 这就是界面上那两段 `[object Object]` 的出处，
真实的原因、真实数字和原始错误代码**全被吃掉了**。

### 1.5 顺带发现并修复的上游回归（pre-existing，已在 `main` 上失败）

`backend/tests/test_install_plan_qualification.py::test_an_unqualified_host_is_refused_even_when_the_call_bypasses_the_ui`
在 `main`（`6fbf498`）上**本来就是红的**（本地复现：`git stash` 后单跑该模块仍然 FAILED）。

- `cc618a2`（2026-11-14 用户裁定）把生产门禁写成
  `if status == BLOCKED or not candidate_ack: raise` —— 生产变体在 **UNKNOWN**（读不到硬件事实）时也拒绝。
- `89dbfd2`（"feat: update workspace"）把条件改回 `if status == STATUS_BLOCKED:`，
  **丢掉了 `or not candidate_ack`** → 生产变体的 UNKNOWN 主机被放行 → 上面那条用例变红。

本轮按 `cc618a2` 的已裁定语义**恢复**该条件（**没有改测试预期、没有放宽任何资格检查**），
生产变体在未知主机上重新被拒；候选变体显式确认后仍然只对 BLOCKED 拒绝。

---

## 2. 修复内容

### 2.1 后端：能力门禁（新增 `backend/shine/comfyops/capability.py`）

- **能力由服务端按清单判定**：`CATALOG_CAPABILITIES` 显式登记「清单 → 能力 → 期望 catalog_id」，
  并核对清单自身的 `catalog_id`；未登记的清单一律 409 `CATALOG_CAPABILITY_UNKNOWN`
  （既不借别人的门禁，也不逃掉自己的门禁）。
- **计划能力再按组件细分**：H3 清单里只带审片模型的计划 = `review` 能力，不继承视频变体的准入。
- **运行时门槛只报告、不拦下载**：`runtime_requirements()` / `runtime_advisories()` 把该能力
  **自己**的显存/内存门槛连同实测数字带出来（图片能力要 15GB / 32GB，审片 VLM 8000MB、ASR 4000MB）。
  下载是存储行为，推理才是算力行为；门槛没有被取消，只是回到它该在的地方（渲染器启动时）。
- 仍复用既有 `ControlledDownloader` / `InstallationPlan` / `InstallationAttempt` / 审批 / 缓存 / 断点续传。

### 2.2 后端：变体解析以清单为边界

`resolve_variant()` 现在要求 **清单本身就是该变体的清单**（`catalog["variant_id"] == manifest["variant_id"]`）。
Qwen Image 清单没有 `variant_id` → 图片计划**在结构上**不可能绑定 H3 生产变体。

### 2.3 后端：`/setup/install-plan` 按能力分派门禁

- `InstallPlanRequest.capability` 是**声明**而非开关：服务端重新推导后不一致即
  409 `CAPABILITY_CATALOG_MISMATCH`（附 `declared_capability` / `actual_capability`）。
- H3 能力：保留原有生产准入（BLOCKED 拒绝；生产变体 UNKNOWN 也拒绝；候选变体需显式确认）。
  409 detail 增加 `capability: "h3"` 与"其它能力不受影响"的说明。
- 其它能力：不套用 H3 门禁，改为返回自己的 `runtime_requirements` / `runtime_advisories`。
- 保留 `include_image_capability` 伴生计划语义与 `configuration` 字段既有取值。

### 2.4 前端：一份计划 = 一个能力

- `SetupCenter` 的「H3 视频生成」由"必装"改为**可勾选能力**（默认勾选），素材出图 / 画面审片 /
  语音核对同理；四类能力各自独立成计划，**一份计划只带自己能力的组件**。
- 一项能力失败（409 / 异常）被单独记为该能力的结论，**其它合法能力继续执行**。
- **不再因为综合就绪未通过就创建 H3 计划**：H3 计划只在"权重确实不在本机"或"人明确要求重下"时创建；
  权重已在本机时走**只读哈希核验 + 登记**，**核验没做完不会重新下载几十 GB**；
  只有哈希**核验不通过**（文件不是钉定字节）才按固定清单重下。
- 生产激活（受管实例 → 方案登记 → 配置 → 服务核验 → 激活）**只在勾选了 H3 能力且其资源就绪时**进行；
  只下图片权重时不会触发 H3 正式生产激活。
- 按钮与分项：按钮文案按实际选择计算（"确认下载素材出图权重（约 16.1GiB）" / "确认准备 H3 候选模型" /
  "确认整备所选能力" / "自动补装缺失基础工具" / "确认并完成配置（不下载）"），
  并展示**分项下载量**；每项能力的结果单独列出（就绪 / 复用 / 未完成 + 原因）。
- 本机已有 H3 文件但硬件不达标时，方案卡片明说：
  **"本机已有这套权重，但当前硬件不具备这一方案的正式生产准入条件"**（不暗切模型、不自动改下 W6A8/NVFP4）。

### 2.5 前端：错误对象不再渲染成 `[object Object]`

`describeDetail()` 改为逐条渲染结构化诊断：
`VRAM_INSUFFICIENT（要求 15360 MB（约 15.0 GB），本机 12282 MB（约 12.0 GB））`，
原始代码、原始数字与 `（HOST_NOT_QUALIFIED）` 全部保留，技术支持读得到原始事实。

---

## 3. 回归测试

### 3.1 新增后端 `backend/tests/test_setup_capability_gates.py`（21 例）

| 用例 | 断言 |
| --- | --- |
| CASE-01 | 4070 上图片计划 200，`capability=image`、`variant_id=null`、`payload.variant is None`；同机 H3 计划仍 409 |
| CASE-01 | 图片计划自报运行时门槛 15360 与实测 12282（诚实告知，不拦下载） |
| CASE-02 | 4070 选 INT8 正式生产仍 `HOST_NOT_QUALIFIED`；未知主机仍被拒（恢复 `cc618a2` 语义） |
| CASE-03 | W6A8 候选显式确认后可备资源，且 `acceptance_status=CANDIDATE`、`auto_download_allowed=false`；未确认仍 409 |
| CASE-04 | 下载中断后续传并复验摘要；被篡改的字节绝不落盘 |
| CASE-05 | 缺 FFmpeg/PowerShell 的发现项 scope 只到 BOOTSTRAP，不带 RESOURCE_PREPARATION / VARIANT_QUALIFICATION |
| CASE-07 | H3 被自己的门禁挡住时，图片能力照常完成 |
| CASE-08 | 已缓存的权重不再计入下载量；同一计划不重复创建 |
| CASE-10 | 伪造能力、非法组件、错误变体绑定、伪造资格字段、未知清单一律具名拒绝 |

### 3.2 新增前端 `frontend/src/test/setupCapabilityGates.test.tsx`（10 例）

- CASE-01：只勾素材出图 → **不提交 H3 安装计划**、提交独立图片计划、进入受控下载队列、
  无 `HOST_NOT_QUALIFIED`、不触发 H3 生产激活；
- CASE-09：图片资源完成后显示"素材出图已就绪"，不误报整个任务失败；
- CASE-07：H3 被门禁挡住时素材出图仍完成（两份计划都提交，互不连坐）；
- 核验进行中时**不重复下载**权重；
- 按钮文案与分项下载量按实际选择计算；
- CASE-06：`describeDetail()` 对对象数组不产生 `[object Object]`，保留代码与数字；
- "文件已找到 ≠ 生产就绪"的卡片文案。

### 3.3 既有测试更新（仅文案契约）

`setupCenter.test.tsx` / `setupEntry.test.tsx` / `directorEntries.test.tsx` 中
`/确认并下载安装/` 的按钮文案断言改为 `/确认(下载|准备|整备|并完成配置)/`
—— 这是本轮 §7 明确要求的按钮语义变更，不是放宽断言。

---

## 4. 验收证据

见文末"回归执行结果"一节（命令、分片、结果）。

---

## 5. 真实用户机器上仍需确认的项目（本轮无法在云端证明）

1. **RTX 4070 真机**：本轮全部是"注入硬件事实 + 端到端请求"验证，云端没有 4070 真机，
   **不构成任何真机生产通过结论**。真机上需确认：图片权重下载 → SHA-256 核验 → 受管实例绑定 →
   真实出图三分类小样。
2. **Windows 真实安装**：本轮只验证了请求链与准入判定，**没有**在 Windows 上跑过
   FFmpeg/PowerShell 补装与断点续传的真实安装。
3. **12GB 显存下的出图可行性**：Qwen Image 清单自身要求 15GB 显存 / 32GB 内存，
   4070（12GB / 30.8GiB）**下载得到但跑不动**。本轮按清单如实告知；是否需要降档出图方案，
   属于产品决策，需用户裁定（不得为了"跑通"而私自换模型）。
4. **本机 H3 INT8 文件的哈希核验**：用户 3/3 文件"已找到"不等于"已核验"；
   本轮已提供只读核验 + 补充登记路径，真机上仍需实际跑一次核验。

---

## 6. 变更文件清单

| 文件 | 变更 |
| --- | --- |
| `backend/shine/comfyops/capability.py` | **新增**：能力分类表、计划能力推导、运行时门槛与告知 |
| `backend/shine/api_setup.py` | `/setup/install-plan` 按能力分派门禁；能力声明校验；409 增能力归属；恢复生产变体 UNKNOWN 拒绝 |
| `backend/shine/comfyops/deployment.py` | `resolve_variant()` 以清单为边界；审片运行时门槛单一来源 |
| `frontend/src/components/SetupCenter.tsx` | 能力可勾选；一份计划一个能力；分项下载量；分项结论；H3 生产激活按需 |
| `frontend/src/api.ts` | `describeDetail()` 结构化诊断渲染；导出以便测试；新增错误码文案 |
| `frontend/src/styles.css` | 分项下载量与分项结论样式 |
| `backend/tests/test_setup_capability_gates.py` | **新增** 21 例 |
| `frontend/src/test/setupCapabilityGates.test.tsx` | **新增** 10 例 |
| `frontend/src/test/setupCenter.test.tsx` / `setupEntry.test.tsx` / `directorEntries.test.tsx` | 按钮文案契约更新 |
