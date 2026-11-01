# 已有 ComfyUI 检测与模型复用修复

日期：2026-11-02。基线：`351067f`；分支：`codex/reuse-existing-comfyui`，本轮工作树变更。

## 问题与修复边界

新机页原先只检查基础工具、硬件、端口和工作台的安装记录，未调用已有安装盘点，
却用“模型与服务尚待准备”和“下载缺项”引导操作。已有安装未登记并不代表模型不存在。

- 新增固定本地磁盘常见目录的有界检测，以及用户手动指定整合包/ComfyUI 目录的入口。
  检测不运行该目录中的代码，不启动 ComfyUI，不扫描整个磁盘。
- 选择已有安装后，先显示模型盘点与待哈希校验项，暂停自动安装入口。
  用户也可明确选择独立安装、不导入原模型；没有发现安装时明确说明检测覆盖范围。
- 根据固定清单的文件大小筛选候选，允许文件改名；仅在用户确认后复制、流式哈希、原子写入缓存。
  校验不匹配的文件被拒绝，残余临时文件清理；源文件不覆盖、不硬链接、不执行。
  检测快照变化拒绝导入，源文件同大小变化仍由最终哈希拒绝。
- 安装计划重新校验缓存，以实际缺少的字节数计算 `download_bytes`。
  既有下载安装审批、托管运行配置、租约与真实 H3 验证门禁继续适用。
- 原有 ComfyUI 不会被接管为托管生产进程。工作台仍需独立配置固定版本的程序和节点；
  已复制到缓存不等于已安装，更不等于生产就绪。

当前只读取选中 ComfyUI 的 `models` 目录；外部链接、共享模型目录及 `extra_model_paths`
指向的目录不自动读取，页面明确提示。VLM/ASR 模型文件也按固定清单盘点。

## 验证

- 新增后端回归 9 项：整合包发现、只读盘点、改名候选、同大小错误内容、复制后哈希验证、
  快照变化、网络/相对路径拒绝、本地操作边界与审计、缓存扣除下载量。
  定向 `backend/tests/test_existing_installation.py` 与 `test_new_machine_setup.py`：20 passed。
- 前端全套：29 passed。最终交互定向：7 passed。
  覆盖发现安装后阻止直接下载、只读检测不执行安装、取消复用无写入、成功复用后才开放安装、
  改目录作废结果、检测失败不开放安装。
- Ruff、TypeScript/Vite、`git diff --check`：PASS。
- 完整后端：1330 passed / 1 skipped / 2 warnings，exit=0。
  双重 quiet 配置不显示计数汇总，计数通过 1331 项收集结果与进度中的一个 skip 核对。
  跳过的是门控的真实 ComfyUI 启动探针；警告分别为 AnyIO 弃用提示及旧质检路径的空主键查询。
  首次误从 backend 目录启动导致两个 Alembic 测试找不到仓库相对配置，已中止；
  从仓库根目录复验 Alembic 2 passed，并重新执行完整后端。
- 真实本机只读验证与在线网页代理 HTTP 验证：PASS。
  自动发现已有 ComfyUI；扫描 1,877 个文件；10 项候选，43,978,717,531 bytes，19 项未找到固定清单文件。
  候选仍是 `PENDING_HASH`，不据此宣称真实模型内容已通过校验。
- 原工作台无任务/生产序列/安装执行/租约，按记录的进程身份重启后端、Worker、Web；
  保留原数据库、端口 8000/5173 和 mock 模式。未启动原 ComfyUI，未复制个人模型或下载组件。
- 浏览器目视检查：NOT RUN。Computer Use 因无法可靠识别当前浏览器 URL 而停止，未继续浏览器输入。
  真实模型复制、下载安装及 H3 生产验收：NOT RUN，留待用户手工验收。

## 手工验收

1. 刷新 `http://127.0.0.1:5173/#setup`，点击“检测本机环境”。
2. 确认显示已有 ComfyUI，默认选择复用；自动下载按钮此时禁用。
3. 点击“检查已有目录”，查看候选/未找到文件及“待哈希校验”提示；此时没有复制、下载或启动服务。
4. 如决定复用，点击“校验并复用匹配模型”，核对来源、目标目录与空间后确认。
   此步骤会复制模型并校验哈希，耗时取决于磁盘；取消不执行。
5. 查看实际复用字节数与拒绝项，再决定是否点击“下载缺项并自动配置”。
   该按钮仍会请求下载/配置确认，且固定程序和节点需要独立准备。
6. 手工改目录或重新检测后，旧盘点失效；已有安装未出现在常见目录结果时可以手动输入路径。

## 可复现命令

从仓库根目录执行（全部为隔离软件测试，不提交真实生成任务）：

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_existing_installation.py backend/tests/test_new_machine_setup.py -q
.\backend\.venv\Scripts\python.exe -m ruff check backend/shine backend/tests
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
git diff --check
```

完整后端原始日志：`.local-acceptance/existing-comfy-backend-tests.log`（本机留存，不打包、不提交）。
本轮没有新增数据库迁移、改动依赖或封装测试包。用户原有未跟踪的开发启动脚本与快捷方式保持不动。
