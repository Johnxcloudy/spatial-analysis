# Spatial Analysis Desktop

面向土地利用、用地叠加与面积统计的 Windows 本地 GIS 工作站。当前开发范围为 **Phase 0：工程与分发验证**。

## 当前范围

- Tauri 桌面窗口与 React 中文工作区。
- Python 本地引擎的真实 JSON-RPC 调用、运行时版本、驱动和日志信息。
- SQLite 项目的创建、保存、关闭、重新打开，以及锁定和输入校验。
- 合成几何验证：10,000 m² 矩形、5,000 m² 相交、投影往返、GeoPackage 读写和 GeoTIFF 基础读写。
- OpenLayers 投影验证预览、Python onedir 打包和 Windows NSIS 安装器。

这里的矩形是可核对答案的测试数据，不代表用户项目成果，也不证明真实测绘数据的定位精度。数据导入、图层管理和正式分析工具属于后续阶段。

## 开发环境

Windows x64；Node.js 24；Rust 1.98 或以上；Visual Studio C++ Build Tools 和 Windows SDK；WebView2；uv。冻结引擎验证脚本需要 PowerShell 7。Python 3.12 及 GIS 依赖由 uv 安装到 gis-engine/.venv，最终安装版不需要用户安装 Python。

在仓库根目录执行：

```powershell
npm ci
uv sync --project gis-engine --frozen --group dev
npm run tauri -- dev
```

根目录通过 npm scripts 调用 apps/desktop，并在 postinstall 安装桌面依赖；两份 package-lock.json 都需要提交。该结构不依赖 npm workspace 符号链接，可在本机 exFAT 工作盘上安装。

新安装 Rust 后需要重新打开终端，或为当前 PowerShell 添加路径：

```powershell
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
```

可通过 SPATIAL_ENGINE_PYTHON 指定开发用解释器。正式安装版忽略该开发配置并使用安装目录内的引擎。

## 使用

在桌面窗口中创建项目并选择存放位置，编辑名称、描述和分析 CRS 后保存。打开 project.spa 可以恢复项目。空项目的分析 CRS 默认未设置，诊断工具采用独立的 EPSG:4547 合成小样。

运行环境验证后可检查每项实测结果及投影预览。诊断文件写入独立的缓存子目录；查看界面中返回的报告和 GeoPackage 路径即可定位文件。

```powershell
npm run dev
```

仅启动浏览器界面，默认地址 http://127.0.0.1:1420。浏览器没有 Tauri 原生桥接，因此不能创建本地项目或执行 GIS 引擎操作；完整功能请运行桌面程序。

## 测试与构建

```powershell
npm run check
npm test
npm run build
uv run --project gis-engine --frozen --group dev pytest gis-engine/tests
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml --lib
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
```

安装器输出到 apps/desktop/src-tauri/target/release/bundle/nsis/。构建包含 PyInstaller 引擎和 WebView2 离线安装组件，首次构建会下载较多依赖。安装包尚未签名，不等于正式生产发布。

2026-09-09 本机验证：23 项 Python、19 项前端、4 项 Rust 测试通过；冻结引擎、发布版原生调用及临时目录安装/卸载验证通过。当前安装包约 308.9 MiB，详细证据和 SHA-256 见 [验证记录](docs/verification.md)。

冻结引擎验证：

```powershell
$engine = (Resolve-Path '.\apps\desktop\src-tauri\resources\engine\spatial-engine.exe').Path
pwsh -File scripts/smoke-engine.ps1 -Executable $engine
```

桌面可执行文件支持 `--smoke-test <输出目录>`，用于验证宿主调用、项目保存重开和 GIS 诊断；此模式生成 native-smoke.json 后退出。

GitHub Actions 在 push/PR 时运行检查并构建 Windows 安装器；不自动发布公开 Release。实际执行记录与尚未完成的环境验收见 docs/verification.md。

## 项目结构

```text
apps/desktop/             React 工作区
apps/desktop/src-tauri/   Rust 宿主与安装配置
gis-engine/              Python 引擎、依赖锁和测试
shared/                  TypeScript 类型与 RPC 协议
scripts/                 引擎打包、安装构建和验证
docs/                    方案、数据规范、架构与验收记录
```

项目入口为 project.spa（SQLite 元数据），矢量结果使用 GeoPackage，缓存与原始数据分开管理。不要将真实项目和诊断产物提交到代码仓库。

## 文档

- [优化方案](docs/plan-v2.md)
- [GIS 数据可靠性规范](docs/gis-data-standard.md)
- [当前架构](docs/architecture.md)
- [项目数据模型](docs/data-model.md)
- [验证记录](docs/verification.md)
- [阶段待办](TODO.md)

正式发布前仍需在无开发环境的 Windows 10/11 上验收安装、离线依赖和升级行为，并使用真实规划样本核查互操作性。
