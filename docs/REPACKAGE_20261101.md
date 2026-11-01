# 2026-11-01 Web 修复后安装包封装记录

本轮按用户指令把现有主分支的四区 Web、五项新用户流程修复，以及风格批准内容竞态/全局工作流名称并发唯一性修复一起交付。源代码基线为 `af5d6b6`，新增数据库迁移为 `f150a0020001`。不包含本地未跟踪开发脚本 `scripts/start_dev.ps1`，原文件保留。

## 当前工程结果

| 检查 | 已执行结果 |
|---|---|
| 并发修复全量后端 | 1315 passed / 1 skipped / 2 warnings，585.28 秒；包括 offline E2E、film crew、V1.3 套件 |
| Web | 20 passed；TypeScript/Vite 构建通过；真实页面确认内容与服务器冻结快照一致 |
| Ruff | All checks passed |
| 数据库 | 正常升级/回退/历史保留测试通过；重复全局名称旧库明确阻断，不自动删改 |
| 真实 HTTP 并发 | 三轮各 8 请求，每轮 1×201、7×409、全局仅 1 行；过期风格批准 409 |
| 本次包入口/安装合同专项 | 58 passed / 1 warning，2.06 秒；ASCII cmd、PS UTF-8 BOM、凭据、固定目录物料与重定向合同 |
| 补充结构检查 | `alembic check` FAIL：两条基线已有交付表索引差异，非本轮新增 |
| 实机边界 | 本轮不重跑 GPU、干净新机全量安装或影片人工验收；已有影片 pending_review 保持 |

软件全量结果对应本轮并发功能代码。之后只更新包内说明、构建元数据和报告，重新运行上述 58 项封装专项，不将文案更新写成一次新的全量执行。

详细功能证据见 [并发修复报告](WEB_CONCURRENCY_FIX_20261101.md)；专项日志为 `.local-acceptance/repackage-20261101/package-tests.log`。

## 构建与交付约定

包版本：`0.1.0-test.20261101.web-concurrency`；类型仍为 **TEST_BUILD / PARTIAL**。包内说明与 `BUILD_INFO.json` 已加入本次 Web/并发修复和当前验收边界。

先提交，再在独立、干净的 `main` 工作区运行：

```powershell
pwsh -NoProfile -File scripts/build_test_package.ps1 -OutDir F:/ClaudeWorkSpace/projects/shine-video-studio/releases/20261101-web-concurrency -Version 0.1.0-test.20261101.web-concurrency -Verify
```

不用 `-AllowDirty`。构建脚本从 Git 已跟踪文件取源，生成命名 ZIP、`.zip.sha256`、包内 `BUILD_INFO.json` 与 `PACKAGE_MANIFEST.json`，解压后逐文件复核 SHA-256。另从最终 ZIP 检查源提交、迁移/前端修复入包，以及模型、数据库、秘密和验收证据排除情况。

实际构建提交、ZIP 路径/大小/哈希与验证结果以输出目录的 `RELEASE_RECEIPT.json` 为准；这是构建完成后生成的外置收据，不把二进制包或本机证据纳入 Git。包内 `source_commit` 必须等于交付主分支提交，普通 push 后核对 `HEAD`、`origin/main` 与远端 `refs/heads/main`，不强推、不改写历史。

新用户完整解压后双击 `一键安装并启动.cmd`，中文文件名异常时使用 `SETUP.cmd`。干净 Windows、60.8GB 全量下载、约一分钟 M4 影片及最终人工批准仍需独立实测，不能把本次封装校验作为全产品 PASS。
