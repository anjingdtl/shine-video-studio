# R4 修复验收记录：设置向导里节点包降级提示被覆盖

> **文件性质**：本文件由**修复师**按 R4 任务书产出，内容是**修复实现与自证回归**，**不是质检结论**。
> 质检复核结论由质检员另出（按团队职责边界，质检结论的正式载体是质检员自己的 ACCEPTANCE 文档）。

- 日期：2026-11-15
- 分支：`fix/wizard-node-pack-notice-visible-20261115`
- 提交：`dd0c880`（仅 2 个前端文件，+133 / -2）
- 基线：`main` = `e2a4616`（origin/main 同步）
- 缺陷来源：R3 终版质检验收报告 `docs/ACCEPTANCE_20261115_R3_FINAL_GATE_AND_ASSET.md` §4.1 / §5.1 / §6.1

---

## 一、结论摘要

| 项 | 结论 |
| --- | --- |
| 根因 | **真实产品缺陷**：降级提示只挂在阶段 note 上，被后续同 id 阶段的两次写入覆盖 |
| 修法 | 提示改为**独立于阶段列表承载**（新增状态 + 独立警示行），任何后续阶段都盖不掉 |
| 后端门禁 | **未动**：生产默认仍 `enforce`，`diagnose` 仍只能进程内显式设置，环境变量仍无法关掉生产门禁 |
| 改动文件 | 仅 `frontend/src/components/RealProductionWizard.tsx`、`frontend/src/test/setupNodePackNotes.test.tsx` |
| 回归 | build 退出码 0；前端全量 70 文件 / 382 用例全绿，退出码 0；定向 2 文件 / 44 用例全绿，退出码 0 |
| 护栏 | 临时撤掉修复 → 3 failed、退出码 1；恢复后 6/6 绿、退出码 0 |

---

## 二、根因（文件:行，行号为修复后 `dd0c880`）

写入与覆盖都在同一个阶段 id `workflows` 上，`setStage` 是「按 id 覆盖整个 patch」的语义，因此**后写的覆盖先写的**。

| 顺序 | 位置 | 写入内容 | 结果 |
| --- | --- | --- | --- |
| 1 | `RealProductionWizard.tsx:616` | `setStage('workflows', { status: 'warn', note: `运行配置已生成，但${warning}` })` | 降级提示**此时可见** |
| 2 | `RealProductionWizard.tsx:636` | `setStage('workflows', { status: 'ok', note: '生成方案与配置已就绪，等待最后的验证与启用。' })` | **覆盖 1**（`onlyDiagnose` 分支） |
| 3 | `RealProductionWizard.tsx:651` | `setStage('workflows', { status: 'running', note: '正在检查本机 H3 服务与工作流；…' })` | **覆盖 2** |
| 4 | `RealProductionWizard.tsx:662` | `setStage('workflows', { status: 'ok', note: `H3 服务与工作流已验证。…` })` | **覆盖 3**（最终态） |

最终态：阶段区只显示「H3 服务与工作流已验证。」，结果区显示「真实制作已准备好。」，**缺哪几个节点包、渲染器会禁用它们、下一步去哪补装，全部不在屏上** —— 降级对用户等于静默通过。

数据链路本身是通的（R3 报告已核实，本轮未改动后端）：

`deployment.py:929/1032` 写入 `node_pack_notes` → `deployment.py:1182-1199` 进 readiness L3 → 前端 `RealProductionWizard.tsx` 与 `SetupPanel.tsx:554` 读取。

**旁证**：R3 终版质检的诊断探针 `.local-acceptance/r3-final-20261115-qa/r3qaNodePackDiag.test.tsx` 记录到
`DIAG configuration_calls=1`（接口确实被调用）、`DIAG body_has_KJNodes=false`、
`DIAG body_has_install_hint=false` —— 提示被覆盖后屏上确实没有缺失包名与补装指引。

---

## 三、改动点

### 3.1 产品代码 `frontend/src/components/RealProductionWizard.tsx`（+21 / -1）

1. **新增独立状态**（`:185`）

   ```ts
   const [nodePackNotice, setNodePackNotice] = useState('');
   ```

   它不属于 `stages`，因此没有任何 `setStage` 能覆盖它。

2. **写入点同时写两份**（`:615-616`）

   原来只写阶段 note；现在先写 `setNodePackNotice(warning)`，再写原来的阶段 note。
   阶段 note 保留，是为了「阶段被覆盖前的那一瞬间」提示也在；两处口径一致（同一句 `nodePackWarning` 文案）。

3. **独立警示行**（`:855-859`）

   ```tsx
   {nodePackNotice && (
     <div className="wizard-reuse" data-testid="wizard-node-pack-notice" role="status">
       <p><b>这次运行有节点包被禁用：</b>{nodePackNotice}</p>
     </div>
   )}
   ```

   位置在阶段列表之后、结果区之前：**最终阶段显示「已验证」、结果区显示「真实制作已准备好」时，这条警示依然在屏上**。

   样式复用已有的 `.wizard-reuse`（`frontend/src/styles.css:849`，虚线黄框 + warn 底色），
   **本轮未新增 CSS、未改 `styles.css`**，以遵守「可改文件仅限两个前端文件」的约束。

4. **重跑时清空**（`:271`）：`run()` 开头 `setNodePackNotice('')`，
   避免上一次运行的残留警示留在「重新检查」后的新结果里。

### 3.2 测试 `frontend/src/test/setupNodePackNotes.test.tsx`（+114）

**没有删任何原有断言**；原有 2 条纯函数断言原样保留，新增 4 条。

新增部分把该文件从「只测文案函数」升级为「**完整 happy-path 渲染回归**」：

- 桩沿用 `realWizard.test.tsx` 的 `fullPath()` 全套（`system/status`、`setup/readiness`、
  `setup/install-plan`、`install-plans/install/approve|execute`、`installation-attempts/*`、
  `setup/next-instance-plan`、`install-plans/instance/approve`、`instance-plans/instance/execute`、
  `setup/profiles`、`setup/configuration`、`setup/runtime`、`setup/services/validate`、`setup/runtime/activate`）。
  **`setup/readiness` 保留 `L2_files_verified` 层** —— 这是 R3 报告指出的桩不足点：
  之前若把 readiness 覆盖成 `{ layers: [] }`，向导会在模型阶段就 `stopWith('models', '模型清单核对失败：…')`
  提前返回，根本走不到 `/setup/configuration`，断言只会超时。
- `setup/configuration` 的返回体带真实形状的 `node_pack_notes`（`code` / `missing` / `message`）。

新增 4 条断言：

| 用例 | 锁住的性质 |
| --- | --- |
| 向导跑完之后仍然看得见缺哪些节点包、会禁用、下一步去哪补装 | 独立警示行存在且含 `ComfyUI-KJNodes` / `comfyui-videohelpersuite` / `安装与下载` / `禁用`；同时断言 `/setup/configuration` 被调用 1 次（防止「根本没走到那步」假绿） |
| 最终阶段文案不会把降级事实顶掉 | 断言阶段区确实显示「已验证」，且 `document.body.textContent` 仍含缺失包名与「安装与下载」 |
| 没有节点包缺失时不出现警示行 | 无缺失时不产生噪音 |
| 重新检查时按本次实际结果更新警示 | 重跑会清空上一次残留 |

### 3.3 未改动 / 已核对

- **后端零改动**：本提交 `git show --stat` 只有 2 个前端文件。生产门禁语义不变：
  `backend/shine/config.py:57` 默认 `enforce`、`:111-113` 刻意不从环境变量读取、
  `backend/shine/comfyops/deployment.py:902` 只有恰好 `== "diagnose"` 才降级。
- **`SetupPanel.tsx` 未改**：已只读核对 `SetupPanel.tsx:554`，它的节点包提示走
  `generateConfiguration()` 的**操作返回值**（`run()` 的结果消息），不依赖阶段 note，
  **没有同类覆盖问题**，故不需要动它。
- **`styles.css` 未改**：复用既有 `.wizard-reuse` 类。
- **他人未提交改动未碰**：工作树里 `AGENTS.md`、`docs/EVAL_TEAM_MINIMAX_BOT.md` 的未提交改动
  未读取、未修改、未纳入本提交（`git diff --cached --name-only` 只有本轮两个文件）。

---

## 四、回归命令与退出码

全部在 `F:\ClaudeWorkSpace\projects\shine-video-studio` 执行。

### 4.1 改动前基线（main = `e2a4616`，用于确认起点不是红的）

```
cd frontend; npm.cmd test -- --no-file-parallelism
```

- **Test Files 70 passed (70)**
- **Tests 378 passed (378)**
- Duration 140.07s
- **退出码 0**

> 说明：R3 终版报告里的「375 passed / 1 failed（setupNodePackNotes.test.tsx）」在**当前 main 上不复现**；
> 当前 main 的 `setupNodePackNotes.test.tsx` 是 2 条纯函数断言的版本，跑出来是绿的。
> 但 R3 报告指出的**产品缺陷本身是真实的**（本文件 §二 已用代码与质检探针双向坐实），
> 所以本轮把该文件补成完整 happy-path 渲染回归，让这条缺陷以后有能独立复现的护栏。

### 4.2 定向回归（改动后）

```
cd frontend; npm.cmd test -- --no-file-parallelism setupNodePackNotes realWizard
```

- src/test/realWizard.test.tsx — 38 tests passed
- src/test/setupNodePackNotes.test.tsx — 6 tests passed（2 原有 + 4 新增）
- **Test Files 2 passed (2) / Tests 44 passed (44)**
- **退出码 0**

### 4.3 护栏验证（错误实现必须当场红）

临时把 `RealProductionWizard.tsx:615` 的 `setNodePackNotice(warning);` 注释掉（其余不动）：

```
cd frontend; npm.cmd test -- --no-file-parallelism setupNodePackNotes
```

- **Test Files 1 failed (1) / Tests 3 failed | 3 passed (6)**
- 失败点：`setupNodePackNotes.test.tsx:137` `getByTestId('wizard-node-pack-notice')` 找不到元素
- **退出码 1**

恢复后复跑：**6 passed (6)，退出码 0**。

⇒ 这条回归**能独立复现该缺陷、修完即绿**，不是靠删断言换绿。

### 4.4 构建

```
cd frontend; npm.cmd run build
```

- `tsc -b && vite build`，1654 modules transformed
- `✓ built in 1.65s`
- **退出码 0**
- （只有既有的 chunk > 500 kB 提示，无新增告警）

### 4.5 改动后前端全量

```
cd frontend; npm.cmd test -- --no-file-parallelism
```

- **Test Files 70 passed (70)**
- **Tests 382 passed (382)**（= 基线 378 + 本轮新增 4）
- Duration 139.98s
- **退出码 0**

### 4.6 为什么没有重跑后端 8 分片

**理由**：本轮后端代码**零改动**（`git show --stat` 只有 2 个前端文件），后端树与
R3 终版已出证据的那棵树完全一致；R3 终版的后端 8 分片结论是「238 文件、8 片退出码全 0、
零 FAILED/ERROR」。重跑后端只会把同一棵树的同一结果重复一遍，收益不抵成本，且会与在跑的三套实例抢资源。

**这不等于「后端已由本轮复验」**：后端在本轮属于**未重跑、未复验**，沿用 R3 终版证据。

---

## 五、环境取证

按团队铁律，回归前取证依赖来源，避免「别的 checkout 的 venv / 依赖混进来」：

| 项 | 实测值 |
| --- | --- |
| `frontend/node_modules` | Junction → `.local-acceptance/r1-20261115-assets/wt-a1/frontend/node_modules` |
| ↑ 再一层 | Junction → `F:\ClaudeWorkSpace\projects\shine-video-studio-setup-v41\frontend\node_modules` |
| 后果 | `@testing-library/dom` 等测试依赖实际来自 **setup-v41 这个 checkout**（traceback 路径可见 `../../shine-video-studio-setup-v41/frontend/node_modules/...`） |
| 是否本轮引入 | **否**。06:25 实测时该 junction 链已存在，是既有共享状态 |
| 可比性 | 基线（4.1）与改动后（4.5）跑的是**同一棵依赖树**，口径可比、可比价 |
| 处置 | **未动**。改共享 junction 会打断三套在跑实例；任务书也明确「依赖已就绪，不要执行 npm ci / npm install」。**如实上报给总控，建议后续单独立项处理跨 checkout 共享依赖** |

其它纪律核对：

- 全程**未执行** `npm ci` / `npm install`。
- **未启动、停止、重启任何工作栈**，未结束任何进程（含 8000 端口那个 `python -m http.server`）。
- **未触碰** 5173 / 8010 / 8020 / 8030 / 5180 / 5190 / 5200 上的任何实例。
- **未写入** `.local-data/` 等用户数据目录。
- 提交使用**显式路径** `git add frontend/src/components/RealProductionWizard.tsx frontend/src/test/setupNodePackNotes.test.tsx`，
  **未用** `git add -A` / `git commit -a`；提交前 `git diff --cached --name-only` 核对为 2 个文件。
- 分支纪律：feature 分支 + 日期，未直推 main。

---

## 六、仍未验证的部分（诚实边界）

1. **离线模拟，非真实出图/出片验收**：全部为 mock / 离线验证，`h3_connected=false`、
   `production_mode=BLOCKED/DEMO`，**未发生真实 H3 渲染**，不构成出图或成片验收。
2. **本轮验证的是前端可见性契约，不是真实降级环境的端到端行为**：要端到端复现「门禁真的在
   `diagnose` 下降级」，需要一台真缺节点包的机器或离线夹具组合；本轮用桩直接返回
   `node_pack_notes` 来驱动同一条渲染路径。
3. **未做浏览器实景截图**：三套实例由他人占用，任务书禁止启动/停止任何工作栈，本轮全程未碰。
   可见性由 DOM 级断言（`getByTestId` + `document.body.textContent`）证明，未做像素级留证。
4. **未逐一枚举所有可能覆盖该阶段的写入点**：已核对的是本轮实证链路（`workflows` 阶段的
   4 次写入）。若将来新增同一 id 的阶段写入，独立警示行依然不受影响 —— 这正是本次改法的目的。
5. **本机无任何文本模型**：三套栈 `/api/v1/provider-profiles` 均为空数组，依赖文本工种的链路本轮无法真实驱动；
   本轮未触及该链路，相关阶段记为**未运行**。
6. **A4（出图按钮永久禁用 / `CATALOG_VARIANT_UNDECLARED` 未进可见文本）尚未动手**：
   分支 `fix/asset-image-capability-reason-a4-20261115` 已建但未改码，
   根因已只读定位（`config/deployment/qwen-image-2.1-windows-x64.json` 的 `variant_id` 为空 →
   `variant_binding.py:154-160` 抛 409 → 出图能力无法登记 → 按钮永久禁用），
   按「一次只修一个缺陷」排在 R4 之后。

---

## 七、复现命令清单（可直接粘贴）

```
cd F:\ClaudeWorkSpace\projects\shine-video-studio
git fetch origin
git checkout fix/wizard-node-pack-notice-visible-20261115
git log --oneline -1

cd frontend
npm.cmd run build
npm.cmd test -- --no-file-parallelism setupNodePackNotes realWizard
npm.cmd test -- --no-file-parallelism
```

护栏验证（在修复分支上临时改坏）：

```
# 把 RealProductionWizard.tsx 里 setNodePackNotice(warning); 注释掉
cd frontend
npm.cmd test -- --no-file-parallelism setupNodePackNotes   # 期望：3 failed，退出码 1
# 恢复后再跑一次                                                          # 期望：6 passed，退出码 0
```
