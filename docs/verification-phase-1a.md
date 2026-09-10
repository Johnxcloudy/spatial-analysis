# Phase 1A 验证记录

日期：2026-09-09。版本：应用 0.2.0、RPC 协议 2、项目 schema 2。

本轮按用户选择使用合成样本。普通二维矢量导入、工作区和 GeoPackage 导出属于本轮；坐标表、栅格显示和正式用地叠加/面积统计属于后续阶段。

## 环境与样本

本机 Windows x64，工作目录位于 I: exFAT；开发环境沿用 [Phase 0](verification.md)。新增 PyArrow 21.0.0，完整依赖由 uv.lock、Cargo.lock 和两份 package-lock.json 固定。

`scripts/generate-vector-fixtures.py` 使用真实 GDAL 驱动生成 GPKG、SHP、GeoJSON 和 OpenFileGDB 数据。GPKG/GDB 包含 land 与 controls 两个数据层；样本覆盖中文、GBK SHP、前导零、空值、大整数、面内洞和多部件。额外回归覆盖稀疏 FID、未知 CRS、CGCS2000、无效/空几何以及 Z/M/曲线拒绝。

样本清单：`.artifacts/vector-fixtures-phase1a/manifest.json`。所有测试产物均留在被 Git 忽略的 `.artifacts` 或新建临时目录，用户项目不作为测试输出。

## 源码与界面

| 检查 | 实际结果 |
| --- | --- |
| npm test | 30 项通过 |
| npm run check | 退出 0 |
| 前端生产构建 | 退出 0 |
| cargo test --locked --lib | 4 项通过 |
| cargo fmt --all -- --check | 退出 0 |
| uv lock --check | 退出 0 |
| Python 最终回归 | 87 项通过 |
| 四格式持续 RPC、跨磁盘导出 | 修正后退出 0，10 项工作流检查通过 |
| 浏览器模式 | 桌面/窄视口无横向溢出，原生操作禁用 |
| 原生 WebView2 | 导入、样式、地图/属性选择、筛选、导出、图层顺序、保存重开及草稿保护通过 |

浏览器证据：`.artifacts/ui-browser-1788930230754/verification.json`。最终原生证据：`.artifacts/ui-native-1788932226528/verification.json`，`ok=true`、无页面异常。桌面 1440×900 与窄视口 390×844 截图已检查；Canvas 检查确认几何非空、缩放改变像素、隐藏图层清空几何、定位后未裁切。窄视口只验证布局，不代表移动操作系统支持。

原生自动化仅替换文件选择器返回路径，数据操作经过真实 Tauri/Python 通信。检查报告与工作区截图在同一证据目录内。

开发引擎最终证据：`.artifacts/vectors-source-phase1a-final2/vector-verification.json`，`ok=true`。测试同时覆盖从 I: 项目向 `C:/Users/pc/AppData/Local/Temp/spatial-phase1a-source-final2` 导出；所有目标均为新建文件。

## 可靠性修正

- 导入前后核对全部属性、几何和 CRS，快照内容 SHA-256 用作数据版本。NULL、大整数及来源 FID 映射保留；源文件不被覆盖。
- 未知 CRS 限制为属性查看；已有 CRS 不可覆盖声明。不能保留的 Z/M、曲线、嵌套 GeoJSON 属性和编码替换字符明确拒绝。
- 缺失 SHP 的 .shx 或 .dbf 时，源检查与真实 worker 导入均拒绝；回归核对未登记数据/图层、暂存已清理、剩余源文件未被修改。
- 属性筛选和排序使用验证过的字段；布尔值支持 true/false/1/0，contains 与显示文本一致。分页、显示要素、顶点和响应字节数受到限制。
- schema v1 先用 SQLite backup 备份，再事务迁移至 v2；不支持的未来 schema 拒绝打开。
- 导入和导出均保留发布日志，恢复文件已发布但登记未完成的状态。发布失败不误报完成；不覆盖已有目标；取消回收子进程。
- 重开时清理无发布日志的遗留暂存目录，保留待恢复的发布文件；导出临时文件按创建所有权清理。
- 四格式集成测试曾捕获 Windows 同时读取进度文件导致的 `os.replace` 文件占用错误。现对 PermissionError / WinError 5、32、33 做有界退避重试；回归先复现失败，再验证修复，最终持续 RPC 和原生界面复测通过。

## 分发

`scripts/build-engine.ps1 -SkipSync` 已完成；PyArrow 原生依赖及其 LICENSE/NOTICE 随引擎打包。

冻结基础验证：`.artifacts/frozen-engine/phase1a-final/frozen-engine-smoke.json`，`ok=true`、`packaged=true`、引擎 0.2.0、协议 2。验证清除 PYTHONHOME、PYTHONPATH、GDAL_DATA、PROJ_LIB、PROJ_DATA，并将 PATH 限制为系统和冻结引擎目录。

冻结四格式验证：`.artifacts/vectors-frozen-phase1a-final2/vector-verification.json`，`ok=true`，10 项工作流检查通过。同样在受限环境下验证真实 worker 子进程，包含 I: 到 C: 临时目录的跨磁盘导出、取消和项目重开。验证脚本已修正复制后的环境字典区分大小写所导致的 SystemRoot 读取问题。

Windows release 编译完成。发布版原生验证：`.artifacts/native-release-phase1a-final/native-smoke.json`，`ok=true`、`packaged=true`、引擎 0.2.0、schema 2；实际完成导入、属性/视窗查询、导出与工作区重开。

NSIS 安装器：`apps/desktop/src-tauri/target/release/bundle/nsis/Spatial Analysis Desktop_0.2.0_x64-setup.exe`，340,596,036 字节，约 324.8 MiB。SHA-256：`8D25F629765DAD06BCF2996F58D102C1438339A932D11E4E5F41A2BEB10E0579`。

本机当前用户静默安装到 `.artifacts/installed-phase1a` 成功。安装前确认没有既有应用安装；安装后 `.artifacts/native-installed-phase1a-final/native-smoke.json` 为 `ok=true`，确认引擎 0.2.0、schema 2、真实矢量导入/查询/导出及重开。随后静默卸载完成，程序文件和卸载注册项已移除。汇总证据为 `.artifacts/installer-phase1a-final.json`；项目与报告保存在安装目录之外。

Git 交付分支为 `feat/phase-1a`，保留远程历史，不强制推送。Windows CI 对推送执行检查、源码与冻结四格式工作流、安装构建及原生验证；远程状态以 [GitHub Actions](https://github.com/Johnxcloudy/spatial-analysis/actions) 的对应提交为准，本机通过不等同于远程已通过。

## 验证边界

本机包含开发工具。冻结引擎即使清除 Python/GDAL/PROJ 环境变量并限制 PATH，也不能替代无开发环境的独立 Windows 10/11 验收。

尚未执行真实规划数据、ArcGIS 互操作、独立离线 Windows、升级安装或大规模性能验收。GDB 仅声明普通要素类支持，高级域/子类型/关系/附件等未读取内容在报告中说明；合成样本通过不能证明完整 GDB 语义兼容。

安装包尚未签名；构建产物与本机验证报告不进入 Git。CI 会保存自身生成的安装器和报告，不自动发布 GitHub Release。
