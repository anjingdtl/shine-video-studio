# 启动器的 GitHub Actions 工作流

Windows x64 构建由 [GitHub Actions 工作流](../../../.github/workflows/launcher-windows.yml) 定义。EXE、SHA-256、构建信息和 ZIP 由对应 commit 的运行记录提供。仓库根的 `ShineLauncher.exe` 是有独立构建信息的快照，日常开发启动用 `启动 Shine 工作台.cmd`。

本目录的 `launcher-release.yml` 是可选的签名发布模板。需要启用签名发布时，由维护者安装：

```powershell
pwsh -File launcher/packaging/ci/install_workflows.ps1
```

校验已安装的副本是否与源文件逐字节一致：

```powershell
pwsh -File launcher/packaging/ci/install_workflows.ps1 -Verify
```

## 密钥

发布流程使用两个 GitHub Secret：

- `SHINE_STUDIO_RELEASE_SIGNING_KEY` —— 工作台部署清单签名
- `SHINE_LAUNCHER_RELEASE_SIGNING_KEY` —— 启动器部署清单签名

两者都是 base64 编码的 32 字节 Ed25519 种子。**私钥不得提交仓库。** 仓库内
只保存对应的公钥（`config/launcher/trusted-publishers.json`），启动器校验时
只认公钥。缺少密钥时发布流程会失败关闭。
