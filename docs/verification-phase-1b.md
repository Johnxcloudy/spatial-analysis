# Phase 1B 验证记录

日期：2026-09-09。应用 0.3.0、RPC 协议 3、项目 schema 3。
状态：本机开发与分发验收完成，功能提交已推送，对应远程 Windows CI 通过。

## 范围与环境

本轮使用合成样本实现 CSV/XLSX 表格导入、非空间 GeoPackage、属性浏览、
显式 X/Y/CRS 转点、错误行保留、导出和项目恢复。正式用地叠加与面积统计
仍属于 Phase 2。范围与资源上限见 [Phase 1B](phase-1b.md)。

本机 Windows x64，I: 为 exFAT。新增 openpyxl 3.1.5 与 defusedxml 0.7.1，
依赖由 uv.lock 固定；保留两份 npm 锁文件。安装器尚未签名。

## 源码检查

| 检查 | 本轮实际结果 |
| --- | --- |
| Python 全量回归 | 127 项通过 |
| 前端回归 | 45 项通过 |
| TypeScript | npm run check 退出 0 |
| Rust 协议 | 5 项通过 |
| 调试版 Rust 构建 | 退出 0 |
| Rust 格式检查 | 退出 0 |
| uv lock --check | 退出 0 |
| 源码四格式持续 RPC | 10 项工作流检查通过 |
| 源码表格持续 RPC | 12 项工作流检查通过，包含 I: 到 C: 导出 |
| 实际旧版合成项目副本迁移 | 4 个数据集、图层、任务、元数据与快照字节保留 |
| 冻结引擎四格式/表格 | 分别 10/12 项工作流检查通过 |
| 冻结引擎基础诊断 | 通过，packaged=true |
| 冻结引擎旧项目副本迁移 | 通过，保留 4 个数据集及项目状态 |

证据目录：

- `.artifacts/vectors-source-phase1b/vector-verification.json`
- `.artifacts/tables-source-phase1b/table-verification.json`
- `.artifacts/migration-source-phase1b/migration-verification.json`
- `.artifacts/vectors-frozen-phase1b/vector-verification.json`
- `.artifacts/tables-frozen-phase1b/table-verification.json`
- `.artifacts/frozen-engine/phase1b/frozen-engine-smoke.json`
- `.artifacts/migration-frozen-phase1b/migration-verification.json`

迁移验收从 Phase 1A 留存的 schema 2 四格式合成项目复制到新目录，检查唯一
新增 v2 备份、schema 3 重开以及原文件 SHA-256。没有用用户真实项目作为测试输出。

## 原生界面

新增工作流证据：`.artifacts/tables-ui-1788937943414/verification.json`，
`ok=true`，无页面异常。CSV 预览、表格选择、筛选、导出、草稿保留、显式转点、
检查报告、XLSX 工作表/表头选择、公式原文和项目重开均通过。

桌面 1440×900、窄视口 390×844 截图已检查，未发现页面横向溢出。
Canvas 检查确认点几何非空、缩放响应、定位后未触边。源行号与内部 ID 分列显示。
窄视口是桌面 WebView 的布局验收，不代表支持移动操作系统。

原有矢量工作区回归：`.artifacts/ui-native-1788938121551/verification.json`，
`ok=true`、无页面异常；GPKG/GDB 导入、图层顺序/样式、地图选择、草稿、导出、
保存重开和 GIS 诊断通过。原生自动化仅替换文件选择器路径，数据操作使用真实
Tauri/Python 通信，没有模拟成功的 GIS 结果。

浏览器模式证据：`.artifacts/ui-browser-1788938617795/verification.json`，
原生操作保持禁用，桌面/窄视口空态无横向溢出。最新前端生产构建退出 0，
产物包含修正后的“UTF-8（兼容 BOM）”选项。

## 数据保留与修正

- CSV 严格解码，无类型猜测；前导零、空字符串、文字 NULL 保留。
- XLSX 保留公式文本；类型和数字格式作为辅助属性表保存，不计算公式或宣称
  完整工作簿格式保真。工作表枚举不依赖第一张表是否可用。
- 转点保留每条输入记录、源行号和父表版本；非法类型、非有限数、越界或转换
  失败记录为 NULL geometry，并有状态和原因；至少需要一个有效点。
- 仅接受支持的二维地理/投影 CRS；地理 CRS 目前要求角度单位为度。数字使用
  ASCII 十进制/科学计数法，不解释区域千分位或自动猜测坐标顺序。
- XLSX 按解析后的实际内容控制预算；稀疏表格后部增宽时先检查补齐矩阵的单元格
  预算，避免通过少量远端单元格绕过限制。预览提前结束时关闭源文件句柄。
- 项目迁移先备份再事务更新；保留发布/导出日志、不可变约束、外键、任务历史。
  表格发布不建图层；转点单独发布矢量及图层。任务取消不会登记半成品。
- 前端丢弃过期预览/查询结果与错误，保留项目草稿；切换表格后返回地图不重放
  旧定位请求。UTF-8 选项文案统一为兼容 BOM，与解析行为一致。

## 分发与 Git

`scripts/build-engine.ps1 -SkipSync` 已完成。冻结验证清除 Python/GDAL/PROJ
环境变量并限制 PATH，仅使用系统及冻结引擎目录；表格测试覆盖实际 XLSX
解析、worker 转点、源行号、NULL geometry 和 I: 到 C: 的跨磁盘导出。

发布版原生验证：`.artifacts/native-release-phase1b/native-smoke.json`，
`ok=true`、`packaged=true`、引擎 0.3.0、schema 3；真实矢量工作流和表格转点
均通过，3 条表格记录保留，地图返回 2 个有效点。

NSIS 安装器：`apps/desktop/src-tauri/target/release/bundle/nsis/Spatial Analysis Desktop_0.3.0_x64-setup.exe`，
341,164,136 字节。SHA-256：`A0143408B44929052F232489432EBA7907814B4DB0BD465044582F4AF70E4802`。

确认没有已有安装后，当前用户静默安装到 `.artifacts/installed-phase1b`，
注册版本/位置正确；安装后 `.artifacts/native-installed-phase1b/native-smoke.json`
为 `ok=true`，确认冻结 0.3.0 引擎、真实 CSV 导入/转点/导出和项目重开。
随后静默卸载通过，程序文件与卸载注册项已移除。证据：
`.artifacts/installer-phase1b.json`。项目和报告保存在安装目录之外。

源码、依赖锁和验收脚本已提交并推送至 `feat/phase-1a`，功能提交为
`a8879f8156e7d3392f5883d0b7f7e4cc80b26d8f`，通过 `git ls-remote` 核对远程 SHA。
未强制推送；构建产物和合成项目不进入 Git。

2026-09-09 已通过 GitHub API 核对上述功能提交的
[Windows CI 34324666936](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34324666936)：
`status=completed`、`conclusion=success`。Windows job `102379065170` 于
07:47:58 UTC（北京时间 15:47:58）结束，前端/Python/Rust 检查、源码与冻结引擎
工作流、安装器构建、原生 EXE 冒烟测试及产物上传全部通过。

CI 产物 `spatial-analysis-windows-a8879f8156e7d3392f5883d0b7f7e4cc80b26d8f`
（artifact ID `10093641628`）已上传，查询时未过期。此 CI 结论只对应上述功能提交；
后续阶段收尾文档提交不改变已验证的应用代码或安装器，使用 `[skip ci]`
跳过重复构建；其验证为文档复核与 `git diff --check`。

收尾复核重新读取本机工作流/UI/安装报告，均为 `ok=true`，并重新计算本地安装器
SHA-256，与本记录一致。此时只更新文档，未重复执行已通过的本机应用测试。

## 验证边界

真实规划数据、ArcGIS 互操作、独立无开发环境 Windows 10/11、离线环境、
升级安装和大规模性能尚未验收。本机冻结环境检查不能替代独立机器验收。
表格上限和合成结果不构成大数据性能或完整 Excel/GDB 兼容性的承诺。
