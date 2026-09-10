# Phase 1D 验收记录

日期：2026-09-10。应用 0.5.0、公开 RPC 5、项目 schema 5、worker 协议 4。
功能、源码/最终冻结/原生工作流、本机分发及对应功能提交的 Windows CI
均已通过，阶段交付完成。范围见 [Phase 1D](phase-1d.md)。全部使用合成样本。

## 实现与审查

项目另存为使用 SQLite backup 和完整注册快照，新项目分配新身份，并保留
当前未保存的表单、Dataset JSON/ID/版本、图层设置和终态任务/来源历史。
原项目表单不隐式保存。复制为可取消 worker 任务，预算 32 GiB，目标必须
不存在；拒绝嵌套目录、链接、缺失/变更快照和未解决的发布日志。复制任务
和日志在同一事务创建，目标临时目录有归属标记，恢复时重新核对哈希。

来源重新定位按导入指纹验证后追加历史，不改原始来源和数据快照。存在性
与历史核对时间分开显示。TablePoints 按当前项目内父表 ID/版本解析，整体
移动项目后不依赖历史绝对路径。旧 schema 1-4 先备份再事务迁移。

前端只有在副本打开且工作区加载成功后才接受新状态；打开成功但工作区
加载失败会保留草稿并要求重开。复制期间禁止项目/图层变更，取消保留可用；
重试或重开另存弹窗不沿用上次失败提示。相关状态和过期响应已覆盖回归。

独立审查关闭了两项问题：混合大小写 SHP 原名移动的指纹误拒绝，以及
CART-00 浏览器沿用 Python 哈希造成的错误身份声明。制图脚本现在独立
校验实际读取/发送的文件，四种替换反例均在浏览器启动前拒绝。
审查没有未解决的当前范围问题；不等于分发或真实数据已验收。

## 源码与构建

| 检查 | 本阶段实际结果 |
| --- | --- |
| Python 全量测试 | 227 项通过，包含 32 项 portability 回归 |
| 前端测试 | 最终 90 项通过；工作者早先的 89 项记录保留为历史 |
| TypeScript | 通过 |
| Rust 宿主测试 / 调试构建 | 8 项通过 / 构建通过 |
| 源码持续 RPC | 项目可迁移性 7 项、9 数据集；矢量 10、表格 12、栅格 10 项通过 |
| 真实历史 schema 4 副本迁移 | 源码通过，保留 5 数据集、JSON/字节、图层、任务与项目元数据 |
| 最终冻结引擎 | 构建通过；项目可迁移性/矢量/表格/栅格各 7/10/12/10 项通过，packaged=true |
| 最终冻结隔离环境冒烟 | 59 项通过，无失败，packaged=true |
| 最终冻结历史 schema 4 副本迁移 | 通过，保留 5 个数据集及完整状态，原文件未变 |
| 0.5.0 前端生产构建 | 通过，1802 modules |
| 0.5.0 Windows NSIS / 发布版 EXE | 构建通过 / 原生冒烟 ok=true、packaged=true |
| 临时安装 / 安装后 EXE / 卸载 | 全部通过，安装后 ok=true、packaged=true |
| Rust 格式 / uv lock / git diff --check | 均通过 |

源码证据：

- `.artifacts/phase1d-portability-report.md`、`phase1d-frontend-report.md`
- `.artifacts/phase1d-review-portability.md`、`phase1d-review-ui-cartography.md`
- `.artifacts/portability-source-phase1d-final/portability-verification.json`
- `.artifacts/vectors-source-phase1d-1/vector-verification.json`
- `.artifacts/tables-source-phase1d-1/table-verification.json`
- `.artifacts/rasters-source-phase1d-1/raster-verification.json`
- `.artifacts/migration-source-phase1d-1/migration-verification.json`
- `.artifacts/native-debug-phase1d-1/native-smoke.json`

混合项目含四种矢量格式、CSV/XLSX、派生点和两份 GeoTIFF。八个外部来源
均按身份重新定位；内容不符拒绝，最后有效来源不变。另存后移动整个目录，
重开并执行查询、显示、采样和全部九个数据集导出，快照字节与来源保持不变。
源码迁移在历史项目副本上执行，原 schema 4 项目未改动。

最终冻结证据位于 `.artifacts/portability-frozen-phase1d-final/`、
`vectors-frozen-phase1d-final/`、`tables-frozen-phase1d-final/`、
`rasters-frozen-phase1d-final/`、`engine-smoke-frozen-phase1d-final/` 和
`migration-frozen-phase1d-final/`。执行者报告与核对结果见
`.artifacts/phase1d-frozen-report.md`。最终生产引擎 EXE 为 10412182 字节，
验收前后 SHA-256 均为
`C047E3F6A69AA6FD185B90C0943047EBDAECEC1EA80988CDBE5204EF976B5BDD`。

## 原生界面

重启开发版原生程序后运行 Playwright，仅替换文件选择器，保留真正的
WebView2/Tauri/Python 和 worker。已完成：

- 项目另存/来源：`.artifacts/portability-ui-1789003328210/verification.json`，
  `ok=true`、5 项、7 张截图，页面异常为空，Canvas 非空（114920 像素）。
- 栅格：`.artifacts/rasters-ui-1789003399111/verification.json`，3 组工作流、
  12 张截图、页面异常为空；逐格 RGBA、零值/NoData/mask、RGB、缩放、混合
  图层、草稿、导出与重开通过。
- 表格：`.artifacts/tables-ui-1789003484862/verification.json`，3 组工作流、
  9 张截图、页面异常为空；CSV/XLSX、公式原文、转点、导出与重开通过。
- 矢量与 GIS 诊断：`.artifacts/ui-native-1789003596338/verification.json`，
  `ok=true`、5 项、4 张截图、页面异常为空，GPKG/GDB、样式、查询、导出、
  地图交互、重开及诊断通过。

四组原生流程共 32 张截图。浏览器空态另外执行通过，3 张截图与报告见
`.artifacts/ui-browser-1789003656443/verification.json`；原生操作正确禁用。

视口覆盖桌面 1440×900、中等 900×900 和窄视口 390×844，无页面横向溢出。
窄视口仅验证桌面 WebView 布局，不代表支持移动操作系统。另存成功工作区
和窄视口弹窗截图已人工复核。
RGB 栅格桌面截图和派生点窄视口截图也已打开复核，控制项与数据无不合理遮挡。

## CART-00

完整结果见 [制图探针证据](verification-cartography-probe.md) 与
[配置草案](cartography-spec-draft.md)。独立源码/冻结 Python、OpenLayers
和对照实验通过：2103 要素/10525 顶点，孔洞、多部件、中文、NoData、透明度
和完整数据哨兵均核对；PDF 保留矢量面与可检索嵌入中文，背景为栅格。
浏览器实际文件身份以 final2 证据为准，旧错误证据保留且注明边界。

该实验使用独立依赖，未装入产品引擎或安装器。浏览器 PNG 尚无 DPI 元数据，
中文字体未打包，也未交付 Layout、正式导出 RPC 或 Agent。

## 分发与 Git

最终冻结构建成功后，旧生成资源目录清理遇到非空目录警告，保留于
`.artifacts/engine-old-eb5ea0f1f8e64b6e81316dfd7262bea1`；新 resources/engine
已发布。该目录是历史构建产物，不是用户数据，不影响新引擎验收。

`pwsh -NoProfile -File scripts/build-windows.ps1 -SkipEngine` 退出 0，生成
`apps/desktop/src-tauri/target/release/bundle/nsis/Spatial Analysis Desktop_0.5.0_x64-setup.exe`，
341260830 字节，SHA-256：
`75CD4E827A4F450EEAC45B6B95D9A8687ED46A38BBCC89601C5258C1F6668D3F`。
Rust 链接器仅报告正常创建导入库/对象的信息，没有构建失败。

发布版 `.artifacts/native-release-phase1d-final/native-smoke.json` 为
`ok=true`、`packaged=true`、engine 0.5.0、protocol/schema 5，4 个数据集
完整保留，另存和重新定位任务均 completed。主程序退出 0 后，root 首次
读取报告时误用不存在的 `portability.copiedProject` 路径，导致外层断言失败；
按实际顶层 `copiedProject`、`copyTask` 与 `relocationTask` 重新核对通过。
这是报告读取命令的问题，没有修改产品代码或将失败报告改为成功。

确认没有既有用户/系统安装后，临时安装至 `.artifacts/installed-phase1d`。
`.artifacts/installer-phase1d.json` 的 installPassed/installedSmokePassed/
uninstallPassed/ok 均为 true。安装版引擎 EXE 哈希与已验收打包资源一致，
注册版本和位置正确。`.artifacts/native-installed-phase1d/native-smoke.json`
为 `ok=true`、`packaged=true`、0.5.0、protocol/schema 5，4 个数据集、
新项目身份、内部父表和另存/重新定位任务均核对通过。随后卸载，临时目录、
程序及注册项已移除；测试项目与报告位于安装目录外并保留。

功能提交 `41fe4b522eca506da9b0db99d13f60c10d12eb3e` 已推送至
`feat/phase-1a`，`git ls-remote` 确认远程 SHA 一致；推送后工作区干净。
Phase 1C 的成功 CI 不作为本阶段证据。公共 GitHub REST 曾出现共享 IP
限流，改用公共 Actions HTML 核对完整提交链接、工作流状态及 job。
[Windows CI #8 / 34426091682](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34426091682)
于 2026-09-10 01:47:51 UTC 核查为 `completed` / `success`，job
`102711521833` 标记 `completed successfully`，页面耗时 11m 31s。
公开未登录页面没有完整步骤日志，不据此宣称逐步检查数量。安全状态、
原始页面与其 SHA-256 见 `.artifacts/ci-phase1d.json`；root 再次验证页面
哈希和完整 commit 链接一致。产物、项目和诊断不进入 Git。

公开页面同时显示已上传归档 `10132968139`，名称为
`spatial-analysis-windows-41fe4b522eca506da9b0db99d13f60c10d12eb3e`，
显示大小 326 MB，归档摘要为
`sha256:1093e6b78a570e9636a39c54eae9c241eb190f4a5bd38f32d47fc6e5afca227e`。
没有下载该归档；这不是本地 NSIS 的 SHA-256，不能混用。分支 Actions
列表也独立显示同一完整提交和成功状态，页面快照保存在上述证据索引中。

收尾只更新文档，完成事实复核与 `git diff --check`，不重复已通过的应用
测试。最终文档提交使用 `[skip ci]`，上述 CI 对应功能代码提交，不泛指
文档提交。可用 `git log -1 --grep="docs: close Phase 1D delivery"` 定位
收尾提交；恢复时重查 HEAD、工作区和远程。测试用原生进程与 Vite 服务
均已结束，安装测试程序/注册项已清理，验收项目和证据保留。

## 未覆盖范围

真实规划数据、ArcGIS 互操作、独立无开发环境 Windows、离线/升级及大规模
性能尚未验收，安装器未签名。历史 SHP 的各组件若原本使用不同大小写，
保留文件名移动已支持；完全改名可能因缺少原组件名称记录而拒绝重新定位。
正式土地叠加与面积统计属于 Phase 2，当前显示或引擎诊断不能代替分析结果。
CI 原生冒烟只覆盖宿主/RPC，完整 UI 和历史项目副本迁移由本机单独验收。
