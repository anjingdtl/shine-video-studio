# Shine AI 管家 V2.2 与最新 main 的集成校准

- 最新 main SHA：`a1d05498e5fd654656981f3e2ad364b561bb2462`
- V2.2 开发基线：PR #59 HEAD `ead511b95c62122d2f7047c4e049d634890e89ed`
- 临时集成分支：`temp/main-v22-integration`（仅本地工作树，不推送、不合并）
- 集成工作树：`/workspace/worktrees/main-int`

## 1. 基线核实

开发前重新 fetch 全部远端 refs，实际状态：

| 项目 | 结果 |
| --- | --- |
| main | `a1d05498e5fd654656981f3e2ad364b561bb2462` |
| PR #55 `feat/shine-ai-copilot-support-agent-v1` | OPEN，未合入 main |
| PR #56 `feat/shine-ai-copilot-v2-full-capabilities` | OPEN，base 为 V1 |
| PR #59 `feat/shine-copilot-v21-auto-sync` | OPEN，base 为 V2，HEAD `ead511b...` |

因此采用任务规定的**情况 B**：V2.2 以 V2.1 HEAD 为功能基线，PR base 为 V2.1。
没有在 main 或 V1/V2/V2.1 分支上直接修改代码，也没有自行合并任何 PR。

main 在远端是单个压缩提交（`git rev-list --count origin/main == 1`），与 V1/V2/V2.1 没有
共同祖先。因此不能用普通 `git merge` 判断兼容性；本轮使用“把 V2.2 的增量应用到 main 树”
的临时集成工作树做静态/构建级校准。

## 2. 集成工作树结果

### 2.1 前端

在 `temp/main-v22-integration` 中：

- 复制 V2.2 新增的前端文件：
  - `frontend/src/components/CopilotSettingsPage.tsx`
  - `frontend/src/copilotProfile.ts`
  - 对应 CSS 片段
- 按 main 的 `App.tsx` 结构做**最小增量**：
  - `DirectorPage` 增加 `copilot`
  - `DIRECTOR_PAGE_LABELS` 增加 `AI 管家配置`
  - 左侧导航在「高级设置」后增加一个入口
  - `#d/copilot` 渲染配置页
  - journey rail / demo ribbon / first-run 空态排除 copilot 页
  - 素材中心 `onWorkspace('copilot')` 接到同一 hash 路由
- 未改动 main 已有的导演工作台、项目导航、阶段条、素材中心页面结构。

结果：

| 检查 | 结果 |
| --- | --- |
| `npx tsc -b --pretty false` | 通过 |
| `npm run build` | 通过（Vite 6.4.5，1647 modules） |
| `npx vitest run --no-file-parallelism` | **395/395 通过、78/78 文件通过、exit 0** |

说明：第一次完整跑遇到 2 个已有用例的时序抖动（`setupEntry` 的重连进度条断言等）；
单独重跑该文件 8/8 通过，随后完整重跑 78 文件全部通过。未修改 main 的测试来换取通过。

### 2.2 后端

main 当前**没有** V1/V2/V2.1 的 `shine/support`、`shine/copilot`、Support 表和迁移，
因此 V2.2 后端无法脱离前置 PR 单独落到 main 上。校准结论：

| 检查 | 结论 |
| --- | --- |
| `backend/pyproject.toml` 依赖 | main 与 V2.1 的顶层依赖一致；V2.2 **未增加任何 Python 依赖** |
| LLM Gateway | V2.2 只调用既有 `complete_for_role`；main 的签名与 V2.1 一致，无路由/计费复制 |
| 数据库迁移 | main head `f180f0010001`；V2.1 提供 `0002..0005`；V2.2 追加 `f180f0010006`，链路线性 |
| Support 模型 | V2.2 只向 V2.1 的 `shine/support/models.py` 追加 `copilot_memories` / `copilot_memory_vectors` |
| 启动器/安装包 | 未修改；`hatch` 仍打包整个 `shine` 包，新模块自动进入；无新模型权重/二进制/数据库 |
| H3 生产链路 | 未修改 ComfyUI、Worker、租约、工作流、任务状态机或交付路径 |

结论：**在前置 PR #55 → #56 → #59 依次合入 main 后，V2.2 的迁移与代码均为增量，
不覆盖 main 最新成果。** 在该条件满足前，V2.2 PR 以 V2.1 为 base，避免在 PR 中重复
包含整个 AI 管家历史。

## 3. 受 main 影响的风险点与处理

| 风险 | 处理 |
| --- | --- |
| main 已删除部分旧面板（`AssetPanel`、`FourStagePanel` 等） | V2.2 只新增独立配置页，不复用/恢复旧面板 |
| main 的 `App.tsx` 与 V2.1 有差异 | 集成工作树按 main 结构手工落最小增量，构建与 395 项前端测试通过 |
| main 的迁移 head 不同 | 新迁移明确 `down_revision = f180f0010005`；前置 PR 合并后自然接上 |
| main 的单提交历史导致无 merge base | PR base 用 V2.1；不向 main 强推、不重写历史 |
| main 的 Windows 启动/打包路径 | 未修改脚本、未新增依赖；Windows 真机仍为 NOT_RUN（见验收报告） |

## 4. 明确未做

- 未合并 PR #55/#56/#59；
- 未向 main 推送或强推；
- 未在 main 上创建提交；
- 未把“让 V2.2 直接跑在 main 后端”宣称为已验证——这需要前置 PR 合并后才能做真实集成。

## 5. 建议的合并顺序

1. 合并 PR #55（V1）到 main；
2. 合并 PR #56（V2）到 main；
3. 合并 PR #59（V2.1）到 main；
4. 将 V2.2 PR 的 base 改为 main，按需 rebase；
5. 在真实 main 上重跑后端八分片、前端全量、Windows 安装包与 H3 真机验收。
