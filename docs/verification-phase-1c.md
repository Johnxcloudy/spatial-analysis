# Phase 1C 验证记录

日期：2026-09-09。应用 0.4.0、协议 4、schema 4、worker 协议 3。
状态：本机开发与分发验收通过；功能和验收脚本已推送，对应远程 Windows CI 通过。

## 范围

使用合成 GeoTIFF 检查导入、元数据、灰度/RGB 显示、原始像元查询、导出、
取消和项目恢复。具体支持范围与限制见 [Phase 1C](phase-1c.md)。本机 Windows
x64，I: 为 exFAT；Rasterio 1.4.3 与 affine 2.4.0 由 uv.lock 固定。

## 发现与修正

- 新 RPC 回归先观察到 9 项预期失败，确认旧实现缺少栅格方法与协议版本；
  已添加方法、严格参数与 Dataset 类型校验，完整集成回归通过。
- 严格栅格测试复现 affine 3.0.1 与 Rasterio 1.4.3 的弃用警告冲突；
  已固定 affine 2.4.0 并更新 uv.lock，没有全局禁用警告。
- NoData 与内部掩膜需要共同判断；显示、采样统计和像元查询使用一致规则。
  真实 PNG 验收进一步复现 WarpedVRT 的默认源 NoData 会遮蔽内部 mask；
  显式 src_nodata=None 后结合原始 NoData 过滤，两个无效来源和有效零均通过。
- GDAL 可能为最近邻请求复用平均金字塔，棋盘格测试复现了原数据不存在的
  中间类别；读取使用 OVERVIEW_LEVEL=NONE，保留原始金字塔但不用于此阶段显示。
- 原生 Canvas 像元验收发现 Rust JSON 解析会把部分经纬度改变 1 ULP，
  导致前端严格响应校验丢弃像元结果。已用实际失败坐标复现 Rust 测试失败，
  启用 serde_json float_roundtrip 后 7 项宿主测试通过，完整原生工作流已重验。
- 栅格长标签按总计 256 KiB 元数据预算保留，避免检查成功后因旧的较小单值
  限制发布失败；矢量/表格的既有约束不变。
- 导出目标侧车文件在任务开始、发布及恢复阶段检查；冲突时保留已有侧车、
  待发布文件和日志，项目重开可再次处理，不误报导出完成。
- 各模块与最终集成已独立复核，侧车/标签和 UI 像元/缩放断言问题已闭环。
- 第一轮发布版原生报告发现冒烟断言把 uint16 的原值预期为 "1.0"；诊断样本
  和引擎均正确返回整数原值 "1"。已修正该断言，最终发布/安装报告均重验通过。
  原生验收必须检查 native-smoke.json 的 ok=true，进程退出码不足以证明通过。
- 首次远程 CI 在父 Python 生成栅格样本时找不到 proj.db，前置冻结引擎、矢量
  和表格均通过。已在本机复现：PowerShell/.NET 将 SetEnvironmentVariable 的
  $null 转为空字符串，PROJ_DATA/PROJ_LIB 变为存在但为空。删除改用 Remove-Item
  Env:，finally 区分原先不存在和原有值；同一 PowerShell 进程内严格核对恢复值，
  再运行完整冻结栅格 10 项通过，证据 `.artifacts/environment-recovery-phase1c.json`
  及 `.artifacts/rasters-frozen-phase1c-environment-recovery/raster-verification.json`。
  补充 `.artifacts/environment-states-phase1c.json` 验证原先缺失、存在但为空、
  已有非空值三种状态均正确恢复，错误的继承资源路径不会影响冻结引擎诊断。

## 源码与冻结检查

| 检查 | 本轮实际结果 |
| --- | --- |
| Python 全量回归 | 195 项通过，包含复制中途取消和导出侧车恢复 |
| 前端回归 | 65 项通过 |
| TypeScript / 生产构建 | 退出 0 |
| Rust 宿主测试 | 7 项通过，含实际失败坐标的逐位 JSON 往返回归 |
| Rust 调试构建 / 格式检查 | 退出 0 |
| uv lock --check | 退出 0 |
| 源码栅格 / 表格 / 矢量持续 RPC | 分别 10 / 12 / 10 项通过 |
| 最终冻结栅格 / 表格 / 矢量持续 RPC | 分别 10 / 12 / 10 项通过 |
| 最终冻结引擎隔离环境基础诊断 | 通过，packaged=true |
| 源码 / 最终冻结历史 schema 3 项目副本迁移 | 各保留 7 个数据集与原状态，通过 |

主要证据目录：

- `.artifacts/phase1c-gis-report.md`、`phase1c-store-report.md`、`phase1c-frontend-report.md`
- `.artifacts/rasters-source-phase1c-release/raster-verification.json`
- `.artifacts/tables-source-phase1c/table-verification.json`
- `.artifacts/vectors-source-phase1c/vector-verification.json`
- `.artifacts/rasters-frozen-phase1c-release/raster-verification.json`
- `.artifacts/tables-frozen-phase1c-release/table-verification.json`
- `.artifacts/vectors-frozen-phase1c-release/vector-verification.json`
- `.artifacts/frozen-engine/phase1c-release/frozen-engine-smoke.json`
- `.artifacts/migration-source-phase1c-release/migration-verification.json`
- `.artifacts/migration-frozen-phase1c-release/migration-verification.json`

最终冻结版本在存储边界修正后重新打包；上述 `release` 证据对应最后版本。
栅格冻结工作流包含 I: 到 C: 的跨磁盘导出，SHA-256 与原文件一致。
迁移使用 Phase 1B 留存的 schema 3 合成项目副本，检查唯一新增 v3 备份、
图层/任务/元数据、数据集 JSON 和全部快照字节，原项目和来源 SHA-256 不变。
schemas 1/2/3 的单元迁移回归也保留。

复制中途取消使用真实子进程，暂停时已有部分 GeoTIFF 字节；取消后进程
结束、暂存目录清理、项目无新增登记及日志，源 SHA-256 不变。持续 RPC
脚本中的立即取消只证明未发布，不用于替代中途取消证据。

## 原生界面

三组工作流均为 `ok=true`，页面异常数组为空，共 25 张截图：

- 栅格：`.artifacts/rasters-ui-1788944379240/verification.json`，12 张。
- 表格：`.artifacts/tables-ui-1788944540756/verification.json`，9 张。
- 矢量与 GIS 诊断：`.artifacts/ui-native-1788944611353/verification.json`，4 张。

栅格验证使用真正的开发版 WebView2、Tauri 和 Python，仅替换文件选择器。
逐格读取 Canvas 的 4×4 RGBA，核对 NoData 和内部 mask 透明、有效零不透明、
灰度范围和 RGB [40,120,220,255]。Canvas 非空、定位后未触边，缩放检查实际
像元数量变化。像元点击、scale/offset/单位、透明度、隐藏/显示、未知 CRS、
旋转网格、导出哈希、混合矢量选择、顺序/样式/草稿和重开均通过。

桌面 1440×900、中等 900×900、窄视口 390×844 的相关截图已复核；页面无
横向溢出，栅格样式/像元信息可读取。窄视口属于桌面 WebView 布局验证，
不表示支持移动操作系统。原有 CSV/XLSX、转点和 GPKG/GDB 工作流回归通过。

浏览器空态：`.artifacts/ui-browser-1788944661192/verification.json`，
`ok=true`；原生功能禁用，桌面/窄视口无页面横向溢出。

## 分发与 Git

最终 `scripts/build-engine.ps1 -SkipSync` 成功，隔离环境冻结检查通过。
发布版最终报告 `.artifacts/native-release-phase1c-final/native-smoke.json` 为
`ok=true`、`packaged=true`、引擎 0.4.0、schema 4，核对诊断像元 row/column/raw
值和导出逐字节相等，保留 4 个数据集与 3 个图层，保存重开通过。

NSIS 安装器：`apps/desktop/src-tauri/target/release/bundle/nsis/Spatial Analysis Desktop_0.4.0_x64-setup.exe`，
341,220,393 字节。SHA-256：`E63048884A73B2D8CF2925AB2365BC08DECF9D5031FD7FF135E9A8A4470B7849`。

确认无既有安装后，静默安装到 `.artifacts/installed-phase1c`。注册版本与位置
正确；NSIS 注册表路径带引号，验证脚本去除外层引号后核对。安装 EXE 仅有
Tauri 正常写入的 UNK→NSS 三字节 bundle 标记差异，其余字节一致。
`.artifacts/native-installed-phase1c/native-smoke.json` 为 `ok=true`，确认冻结
0.4.0 的真实矢量、表格转点和栅格导入/查询/导出、项目重开通过。随后静默卸载，
程序及注册项已移除；项目与报告位于安装目录外。证据：`.artifacts/installer-phase1c.json`。

功能提交 `3311d091d89251aadecf633d2f88d3755aae944d` 已推送至 `feat/phase-1a`。
其 [CI 34334757220](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34334757220)
因上述验收脚本环境恢复问题失败，原始安全日志保留在 `.artifacts/ci-phase1c-3311d09.json`。
修正提交 `edb43ada82211d580f8a2cdee361fe12b608f1f9` 已推送并核对远程 SHA，
仅改变验收脚本，应用代码及已验收安装器不变。该提交的
[CI 34335919203](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34335919203)
已最终核对为 `status=completed`、`conclusion=success`，Windows job
`102415152655` 的 27 个步骤全部成功，包括前端/引擎/Rust、源码/冻结工作流、
安装器构建、发布版原生冒烟及产物上传。CI 产物
`spatial-analysis-windows-edb43ada82211d580f8a2cdee361fe12b608f1f9`
（ID `10098146834`）已上传，查询时未过期；CI 归档与本地安装器是不同文件，
不得混用其哈希。安全状态快照保存在 `.artifacts/ci-phase1c.json`。

收尾时再次核对本机最终工作流、迁移、发布/安装和环境恢复报告，均为 `ok=true`，
安装器 SHA-256 一致。收尾只改文档，经复核与 `git diff --check`，不重复应用测试。
文档提交使用 `[skip ci]` 跳过重复构建；通过的 CI 对应上述代码及验收脚本提交，
不泛指文档提交。可用 `git log -1 --grep="docs: close Phase 1C delivery"` 定位收尾提交，
恢复时重新检查 HEAD、工作区和远程分支。

## 验证边界

CI 的 native smoke 验证 Rust 宿主和 RPC，不操作 React/OpenLayers；其中
PNG 是传输冒烟，实际像元正确性由源码/冻结解码测试和本机 Canvas 验收证明。
完整原生 UI 与历史项目副本迁移在本机执行，尚未接入 CI。schema 迁移单元
测试已纳入 CI，不与真实历史副本迁移混为一谈。

真实规划数据、ArcGIS 互操作、独立无开发环境 Windows、离线/升级安装和
大规模性能尚未验收。安装器尚未签名。显示不等于栅格分析或正式面积计算。
