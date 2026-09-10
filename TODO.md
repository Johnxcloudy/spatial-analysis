# 阶段待办

阶段收尾固定执行：更新 [执行说明](执行说明.md) 的阶段记录与精简交接摘要，同步待办和验证证据，再调用可用的上下文压缩功能；宿主未提供时如实说明。恢复工作先读取该摘要，再核对最新用户指令和仓库状态。

## Phase 0

- [x] 保留方案文档并接入远程初始历史。
- [x] 固定进程职责、RPC 契约与项目字段。
- [x] 安装并验证 Rust/MSVC 开发工具链。
- [x] 项目存储、引擎协议与 GIS 单元测试通过（23 项，零警告）。
- [x] 前端类型、交互测试和构建通过（19 项）。
- [x] Rust 宿主编译检查与协议测试通过（4 项）。
- [x] 冻结引擎在隔离环境变量下通过验证（含 GeoTIFF CRS 与读写）。
- [x] Windows 可执行程序和 NSIS 安装器构建成功。
- [x] 本机临时目录安装、安装后原生验证、卸载通过。
- [x] 原生桥接完成创建、保存、重开和 GIS 诊断。
- [x] 桌面/窄视口截图、缩放交互、几何未裁切和无横向溢出检查完成。
- [x] 提交并推送开发分支，核对远程提交。
- [ ] 无开发环境的独立 Windows 10/11 安装验收。

## Phase 1A

- [x] 用户授权进入普通矢量与 GDB 子阶段；验收使用合成样本。
- [x] 固定协议 v2、schema v2 和独立 Dataset/Layer/Task 契约。
- [x] 锁定 PyArrow 以保留大整数和空值。
- [x] 四类格式导入、字段/几何保全、查询与导出回归。
- [x] 项目迁移备份、任务取消、发布故障与恢复回归。
- [x] 原生地图、图层、属性表、检查报告与导出工作流验证。
- [x] 新冻结引擎、0.2.0 安装包、本机安装与卸载验证。

本轮验收证据见 [Phase 1A 验证记录](docs/verification-phase-1a.md)。Git 交付使用 `feat/phase-1a`，远程提交和 CI 状态见该分支的 GitHub Actions。

## Phase 1B（已交付，本机验收与功能提交 CI 通过）

- [x] 用户授权继续开发，沿用合成样本与阶段总结流程。
- [x] 固定独立表格/派生点方案、协议 3、schema 3 和 0.3.0 目标。
- [x] CSV/XLSX 保留规则、非空间 GPKG 与坐标转点。
- [x] 属性工作区、导入预览、错误行报告、源行号、导出。
- [x] v1/v2 迁移、任务取消、发布恢复与源码回归。
- [x] 源码/冻结引擎、原生 UI、0.3.0 安装包与本机安装/卸载验证。
- [x] 执行说明、Git 推送及对应功能提交 CI 核查。

功能提交 `a8879f8156e7d3392f5883d0b7f7e4cc80b26d8f` 已推送至 `feat/phase-1a`；
[Windows CI 34324666936](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34324666936)
为 success，安装器与报告已上传。详细结果与未覆盖环境见
[Phase 1B 验证记录](docs/verification-phase-1b.md)。

## Phase 1C（已交付，本机验收与功能代码 CI 通过）

- [x] 用户授权栅格基础，继续使用合成样本。
- [x] 自包含 GeoTIFF、不可变字节快照、独立 RasterDataset 和协议/schema 4。
- [x] 灰度/RGB、原始像元、NoData/掩膜、旋转/未知 CRS 与混合图层。
- [x] 导出、schema 1-3 迁移、复制中途取消、发布与恢复边界。
- [x] Python 195、前端 65、Rust 7；源码/冻结 RPC、旧项目副本迁移和原生 UI。
- [x] 最终 0.4.0 NSIS、发布版/安装后报告、本机安装与卸载。
- [x] 执行说明、功能提交推送及对应 CI，阶段总结与精简交接。

当前证据见 [Phase 1C 验证记录](docs/verification-phase-1c.md)；完整原生 UI
与真实历史项目副本迁移在本机执行，CI 的 native smoke 为宿主/RPC 检查。
功能提交 `3311d09` 与验收脚本修正 `edb43ad` 已推送；后者的
[Windows CI 34335919203](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34335919203)
为 success，安装器、原生冒烟及上传通过。阶段文档使用 `[skip ci]` 收尾。

## Phase 1D（已交付，本机验收与功能提交 CI 通过）

- [x] 用户于 2026-09-10 授权进入下一步，确定 [范围](docs/phase-1d.md) 和 [实施计划](docs/superpowers/plans/2026-09-10-phase-1d.md)。
- [x] 项目另存为、来源状态/重新定位与 schema 1-4 迁移。
- [x] 桌面工作流、跨格式整合及失败/取消/恢复验证。
- [x] CART-00 配置草案与渲染路径小样，源码/独立冻结/PDF/浏览器像素验证见 [探针证据](docs/verification-cartography-probe.md)。
- [x] 契约与版本整合、独立复核、回归测试和构建；源码/冻结引擎工作流及原生验收。
- [x] 完成 NSIS 安装包构建、本机临时安装、安装后 EXE 验证与卸载。
- [x] Git 提交/推送、对应 Windows CI 核查及阶段总结/精简交接收尾。

功能、源码/冻结/原生、安装包与本机安装/卸载验收已完成。
功能提交 `41fe4b522eca506da9b0db99d13f60c10d12eb3e` 已推送；
[Windows CI 34426091682](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34426091682)
经公共 Actions 页面核对准确 SHA 与 Success，job `102711521833` 成功。
详细边界见 [验收记录](docs/verification-phase-1d.md)，收尾文档使用 `[skip ci]`。

## Phase 2（0.6.0 已交付内部可试用版本，本机合成验收与功能提交 CI 通过）

- [x] 用户授权下一阶段与压力测试，明确日常 10–50 万图斑；范围和实现计划已落盘。
- [x] 投影分析、严格同层重叠校验、裁剪/相交与分类统计、成果 GPKG/CSV（源码实现及解析样本通过）。
- [x] 50 万流式导入、独立查询进程、自动资源监控、优先级请求调度及取消（最终源码/冻结和发布版原生验收）。
- [x] 分析记录/输入版本追溯、schema 1–5 迁移、另存/移动/重开与发布恢复。
- [x] 1000/10000/100000/250000/500000 合成压力测试及复杂/超预算失败保护。
- [x] Python 293、前端 106、Rust 10；源码/冻结/发布版完整原生响应性、NSIS/安装后运行与卸载。
- [x] 功能提交/推送及准确 SHA 的 Windows CI。
- [x] 阶段证据/总结与精简交接、收尾脚本复核；本收尾提交使用 `[skip ci]`，按主题 `chore: close Phase 2 delivery` 定位，远程状态在推送后核对。

版本为应用 0.6.0、公共协议/schema 6、worker 5、readiness 1。功能提交
`9258d7859ef3c832bfd481be266df2e2250ddcf5` 已推送，
[Windows CI 34434218036](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34434218036)
成功（29 个可见步骤）。完整发布版 50 万分析 354.201 秒，DOM p95 14.7 ms、
事件循环最大停顿 24.8 ms、状态 p95 295.6 ms（native invoke，不含 JS 排队），
取消到终态 445 ms。详细证据见 [Phase 2 验证记录](docs/verification-phase-2.md)。
本阶段并行分工已结束，原生验收进程与 Vite 已清理；恢复时重新核对进程状态。

## 下一阶段优先项（选择授权里程碑后实施）

- [ ] 默认属性分页稳定排序键/索引优化，保留数字 ID 顺序和旧快照兼容性。
- [ ] 明确查询超时后的重试路径，开展受控冷读和首/末页验收。曾发生一次无筛选
  `vector.page query_timeout`，后续成功；EXPLAIN 不是唯一根因证明，有界响应不等于永不超时。
- [ ] 真实规划数据与 ArcGIS 独立对照。
- [ ] 独立无开发环境 Windows、离线安装及升级验收；本机安装不能代替这些检查。

## 后续阶段

- Phase 1C：已完成，见 [实施计划](docs/superpowers/plans/2026-09-09-phase-1c.md) 与 [范围约束](docs/phase-1c.md)。
- Phase 1D：项目另存/重新定位、跨格式回归、本机分发与功能提交 CI 已完成。
- Phase 2：土地叠加、分类面积与成果导出内部试用版已交付；上述独立验收仍待。
- Phase 3：在已交付取消、恢复和有界压力验证上，继续强化复杂/真实数据、异常恢复和分发稳定性。
- Phase 4A：土地利用成果制图，主题/图例/比例尺、Layout、PNG/PDF。
- Phase 4B：DEM 地形增强、影像合成与可选在线底图。
- Phase 4C：高级标注/坐标网、出版预设、TIFF/SVG 与高级 PDF。
- Phase 5：通过结构化配置工作的 Cartography Skills 与 Agent 制图顾问。

## 制图任务跟踪

- [x] CART-00：Phase 1D 制图配置草案与渲染选型探针；结果见 [实测证据](docs/verification-cartography-probe.md)，尚未交付 Layout 或正式制图导出。
- [ ] CART-01：Planning/Publication 主题、明确语义的分类/分级、NoData、基本标注。
- [ ] CART-02：可保存的 Layout、真实比例尺/方向、完整数据 PNG/PDF 导出。
- [ ] CART-03：可追溯 Hillshade/高程着色/等高线，栅格拉伸/分类与受控影像混合。
- [ ] CART-04：本地影像及分批接入的 XYZ/WMTS/WMS，署名、离线与导出能力检查。
- [ ] CART-05：高级标签/坐标网、出版参数、TIFF/SVG/PDF 能力与字体验收。
- [ ] CART-06：Agent 配置建议、校验、预览、采纳、撤销及离线回退。

详细依赖和验收标准见 [制图与成果输出路线图](docs/cartography-roadmap.md)。CART-00 实验已完成，CART-01 至 CART-06 仍为后续任务；主线继续完成数据基础交付、用地分析与稳定性。合成样本与本机安装测试不能替代真实数据和独立环境验收。
