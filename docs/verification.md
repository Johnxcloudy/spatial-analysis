# Phase 0 验证记录

本文件记录实际执行结果，不把计划中的测试当作已通过。

## 环境

2026-09-09：Windows x64，Node 24.11.0，Rust/Cargo 1.98.1，Visual Studio 2019 Build Tools 16.11.53、MSVC 14.29.30133、Windows SDK 10.0.19041.0，WebView2 152.0.4191.66。

Python 3.12.2；GeoPandas 1.1.1、Shapely 2.1.1、pyogrio 0.11.1、pyproj 3.7.2、Rasterio 1.4.3、NumPy 2.4.2、PyInstaller 6.16.0。依赖以 uv.lock、Cargo.lock 和两份 package-lock.json 为准。

工作盘为 exFAT：npm 使用根目录脚本转发和独立的桌面依赖安装；Rust incremental cache 无法硬链接时回退复制，编译测试成功。

## 源码与界面验证

| 检查 | 实际结果 |
| --- | --- |
| 根目录 npm ci | 成功，包含桌面依赖安装 |
| npm run check | 退出 0 |
| npm test | 19 项通过 |
| Python pytest gis-engine/tests -q | 23 项通过，零警告 |
| Python compileall、uv lock --check | 退出 0 |
| cargo test --locked --lib | 4 项通过 |
| cargo fmt --all -- --check | 退出 0 |
| 浏览器模式 Playwright | 桌面与窄视口通过；原生操作明确不可用 |
| 原生 WebView2 Playwright | 真实 IPC 创建、保存、关闭、重开和 GIS 诊断通过 |
| OpenLayers canvas | 有几何像素、缩放改变像素、调整视口后几何未被裁切 |
| 未保存保护 | 取消关闭后保留草稿 |

浏览器证据：`.artifacts/ui-browser-1788924935570/verification.json`。原生证据：`.artifacts/ui-native-1788924061748/verification.json`，包含桌面诊断、桌面投影、窄视口投影截图；均无页面异常和横向溢出。自动化仅替换文件选择器的返回路径，项目操作和诊断均经过真实 Tauri/Python 通信。窄视口检查验证布局适配，不代表支持移动操作系统。

## 修复验证

- SQLite 连接显式关闭；创建失败后可清理并立即重试。
- 深层 JSON、非法请求 ID 和不可序列化响应受到保护，错误请求之后仍可处理新请求。
- RPC 直接向 stdout 字节流写入 UTF-8，不受隐藏进程本地代码页影响；强制 GBK 文本环境的中文项目名和字符串 ID 回归通过。
- 项目时间字段必须带时区并满足先后关系，损坏元数据在打开时拒绝。
- PyProj 与 Rasterio 分别使用各自打包的 PROJ 数据库；Rasterio 在进入 Env 后设置搜索路径，覆盖 GDALEnv.start() 的重置行为。
- 地图在尺寸变化时重新适配几何范围，并取消冲突的缩放动画。
- 构建先准备新引擎目录，再用同卷目录重命名替换旧目录；旧文件占用不会混入新安装资源。Vite 不监听 src-tauri，避免资源目录等待监听句柄释放。

## 分发验证

`scripts/build-engine.ps1 -SkipSync` 完成，冻结引擎在保留 Vite 开发服务器运行的情况下可完整替换资源目录。

`scripts/smoke-engine.ps1` 退出 0：`.artifacts/frozen-engine/20260909-120351-762-f0a7a6b9/frozen-engine-smoke.json` 为 `ok=true`，引擎报告 `packaged=true`。测试清除 PYTHONHOME、PYTHONPATH、GDAL_DATA、PROJ_LIB、PROJ_DATA，并将 PATH 限制为系统目录与冻结引擎目录。

实测包括项目创建、保存、关闭与重开；10,000 m² 面积、5,000 m² 相交；GeoPackage 两个图层写入重读；投影往返；OpenFileGDB 驱动可用；2×2 GeoTIFF 像素、仿射变换、NoData 和 EPSG:4547 读写一致。驱动可用不等于真实 GDB 样本互操作已经通过。

WebView2 官方离线安装器已校验 Microsoft Corporation 的有效 Authenticode 签名。首次 NSIS 构建遇到下载超时，随后使用官方缓存与当前构建进程的网络代理重试，未改变 offlineInstaller 配置。

发布版原生 EXE 验证通过：`.artifacts/native-release-20260909-120431/native-smoke.json` 为 `ok=true`、`runtime.packaged=true`。中文项目目录、创建/保存/重开和六项 GIS 检查均通过。

前端生产构建及 Rust release 编译通过。最终 Python 资源更新后使用 `npm run tauri -- bundle --ci` 重新生成安装器，复用同一份未修改的 Rust 宿主代码。

安装器：`apps/desktop/src-tauri/target/release/bundle/nsis/Spatial Analysis Desktop_0.1.0_x64-setup.exe`，323,930,203 字节，约 308.9 MiB。SHA-256：`3B6539CDCDBDCABE1BC5B946F1E08C13B5F0BA232B5C428350F30F77C7B28D0A`。

本机按当前用户静默安装到 `.artifacts/installed-phase0` 成功。安装后执行 `.artifacts/native-installed-20260909-121059/native-smoke.json` 所记录的测试，结果 `ok=true`、`packaged=true`，中文项目操作和所有 GIS 检查通过。随后静默卸载成功，等待 NSIS 辅助进程结束后核对程序文件及卸载注册项已移除。测试项目与报告保存在安装目录之外。

安装器为未签名的 Phase 0 验证版。构建产物和本地报告不进入 Git；GitHub Actions 配置会保存其自身运行产生的安装器与验证报告，不自动发布 Release。远程 CI 状态以仓库 Actions 页面为准，不将本机通过等同于远程 CI 已通过。

## 验证边界

当前机器含开发工具。即使清除 Python/GDAL/PROJ 环境变量后冻结引擎测试通过，也不能宣称完成了无开发环境的独立 Windows 验收。

尚未提供真实规划数据和 ArcGIS 测试环境。合成面验证面积算法和读写路径，不证明真实项目的坐标基准、位置精度、分类语义或完整 GDB 互操作。
