# Phase 1C 验证记录

日期：2026-09-09。应用 0.4.0、协议 4、schema 4、worker 协议 3。
状态：源码、冻结引擎、原生界面验收通过；安装器与 Git/CI 收尾进行中。

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
  和引擎均正确返回整数原值 "1"。已修正该断言，最终发布/安装报告另行重验。
  原生验收必须检查 native-smoke.json 的 ok=true，进程退出码不足以证明通过。

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
0.4.0 NSIS 安装器、发布版/安装后 EXE 冒烟、本机临时安装与卸载待完成。
功能提交、推送和对应 Windows CI 待完成，不能引用历史 CI 代替本轮验证。

## 验证边界

CI 的 native smoke 验证 Rust 宿主和 RPC，不操作 React/OpenLayers；其中
PNG 是传输冒烟，实际像元正确性由源码/冻结解码测试和本机 Canvas 验收证明。
完整原生 UI 与历史项目副本迁移在本机执行，尚未接入 CI。schema 迁移单元
测试已纳入 CI，不与真实历史副本迁移混为一谈。

真实规划数据、ArcGIS 互操作、独立无开发环境 Windows、离线/升级安装和
大规模性能尚未验收。安装器尚未签名。显示不等于栅格分析或正式面积计算。
