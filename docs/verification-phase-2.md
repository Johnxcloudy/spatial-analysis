# Phase 2 验证记录（进行中）

本阶段针对用户日常 10–50 万图斑，使用确定性合成数据。最终源码、冻结、
原生 UI、安装器和 CI 的状态分别记录；以下部分通过不代表阶段已经交付。

## 环境与初次源码压力结果

2026-09-10，本机 Windows 11 Pro 10.0.26200、Intel i5-10400F（6 核/12 线程）、
17,109,073,920 字节物理内存；工作盘 I: 为 exFAT。库版本见各原始报告。

`uv run --project gis-engine --frozen python scripts/verify-analysis.py --output .artifacts/phase2-analysis-source --counts 1000 10000 100000 250000 500000`

完整报告 `.artifacts/phase2-analysis-source/report.json` 为 `ok=true`。
每级包含两层真实导入、取消后重新裁剪、解析面积/分类核对、输入与成果末页、
GPKG/CSV 导出、重开及另存的 ID/版本/哈希核对。另有交叉矩形相交与
1000 个密集重叠面拒绝，并在失败后完成下一小任务。

| 用地数 | 两层导入秒 | 裁剪/统计秒 | 成果末页及统计秒 | 取消到终态秒 |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 6.27 | 4.32 | 0.042 | 1.015 |
| 10,000 | 17.01 | 13.31 | 0.042 | 0.816 |
| 100,000 | 25.02 | 71.09 | 0.143 | 0.257 |
| 250,000 | 52.86 | 约 198 | 0.282 | 0.891 |
| 500,000 | 78.68 | 358.51 | 0.563 | 0.257 |

精确阶段时间以 JSON 为准。此轮共 5660 次 task.get，p95 119.98 ms、
最大 1924.55 ms（门槛是 p95≤1000 ms）；100 ms 采样所得最大进程树 RSS
719,015,936 字节，不宣称捕获瞬时分配峰值。500000 简单图斑成功不代表
任意复杂度都能计算。源码压力期间仍在修正发布/恢复边界，此报告不是最终
提交的精确代码快照；最终冻结验收将重新测量并单列证据。

独立流式导入探针 `.artifacts/phase2-capacity-probe/report.json`：500000 面、
2500000 顶点，导入并完整回读比较 69.688 秒、末页 0.328 秒、进程峰值 RSS
196,509,696 字节、快照 128,544,768 字节。它只验证导入/查询，不能替代上表。

## 回归与审查记录

- 初次整合 264/265 通过，失败原因是 table.inspect 未在预热前校验必填参数；已修。
- 后续完整 Python 273 项通过（92.04 秒），之后新增发布反例测试，最终数量待重跑。
- Rust 实际运行 10 项通过，包含队列等待超时不终止健康会话；此前 cfg(test)
  未编译 engine 模块的 0 项结果没有算作证据。
- 发布核对子进程 DomainError 和监控启动失败遗留外部临时文件：分别复现 RED，
  修正清理顺序后 GREEN，保留原数据与下一任务成功。
- Windows 同长改写后恢复 mtime 可绕过纯时间戳证明：改用父进程禁止写入的
  lease；绑定 HANDLE 的文件身份，兼容 NTFS 64-bit volume / 128-bit file ID
  与 exFAT 跨目录重命名后的身份变化。10 项 lifecycle 测试已通过。
- 前端独立审查发现满显示队列拒绝取消/状态请求、文本上限不一致；修正与回验
  见 `.artifacts/phase2-ui-review.md` 和 `.artifacts/phase2-frontend-report.md`。

## 待完成的阶段验收

普通另存发布一致性最终回归、最终源码/冻结全规模、发布版完整大任务原生测量、
发行 EXE/NSIS/安装后验收、Git 推送和准确 SHA 的 CI。
真实规划数据、ArcGIS、独立无开发环境 Windows、离线/升级仍为独立待验收；
不会使用合成数据和同机测试代替。安装器尚未签名。

## 后续源码复核与复杂度保护

- `.artifacts/phase2-python-acceptance.log`：288 项通过，95.54 秒，随后继续
  修正普通另存的后台发布核对，因此仍需最终全量回归。
- 最终 `.artifacts/phase2-python-delivery.log`：293 项通过，114.07 秒。
  `.artifacts/phase2-frontend-ui-ready.log`：12 文件/106 项通过，22.52 秒；
  前端类型与构建通过。前一轮有1项测试未等待异步项目挂载，补awaited act后
  保持原精确像元查询断言通过；未调整生产行为、超时或性能门槛。
- `.artifacts/phase2-rust-final.log`：10 项通过；Rust 格式检查通过。
- 最终前端 12 文件/106 项通过，TypeScript/Vite 构建通过；随后仅补当前已知
  统计政策的中文显示映射，原始分析记录及导出不变，构建再次通过。
- 大文件恢复测试验证管理进程不读取大文件哈希、取消保留日志/重开恢复、
  deadline、复制和导出恢复；损坏导入日志在 hash/rename 前绑定任务/数据集/
  暂存位置/最终位置/版本。独立反例曾移动旧快照，修正后旧快照保留且拒绝发布。
- Windows 目录包含打开的子文件时可能拒绝目录重命名；另存保留所有权标记，
  先移动到最终目录名，再持文件 lease 进行后台哈希，验证通过才移除标记。
  含标记目录不能作为成功项目打开。普通另存与中断恢复均核对全部注册文件。

`.artifacts/phase2-analysis-guards-source-2/report.json`：`ok=true`。

| 场景 | 实际结果 |
| --- | --- |
| 1000 个正 2000 边形、2001000 顶点 | 裁剪/统计 10.423 秒；解析面积误差 3.933×10⁻⁷ m²，在预设容差内 |
| 3200 个嵌套不相交方环、5118400 潜在包围盒候选对 | 189.225 秒明确触发累计 500 万候选预算，无部分成果 |
| 单面 100002 顶点 | 导入明确超预算失败，原输入不变 |
| 失败后 1 面 / 100 m² 小任务 | 4.912 秒完成导入与分析 |

此轮 task.get p95 61.10ms / 最大 857.29ms；100ms 采样的进程树 RSS 峰值
523673600 字节。首次脚本仅因错误文字断言不符而未跑完，保留 source-1 为
未完全通过；没有修改生产预算、面积容差或响应性门槛。

## 原生调试界面证据

`.artifacts/analysis-ui-1789008679742/report.json`：`ok=true`。真实50万输入任务
在地图/标签切换后取消，未登记部分结果；随后1000面任务完成、CSV/GPKG导出
重读、项目重开后统计一致，页面零错误/无文档横向溢出。

| 指标 | 调试版实测 | 预先门槛 |
| --- | ---: | ---: |
| DOM 反馈 p95 | 32.5ms，17样本（历史脚本包含取消点击） | 150ms |
| 前端事件循环最大停顿 | 35.2ms，319样本 | 500ms |
| task.get p95，包含 JS 调度队列 | 747.9ms，17样本 | 1000ms |
| 取消至终态 | 822.3ms | 5000ms |

5 张工作流截图及后续桌面/窄屏 CSS 复核图在同目录。此组覆盖大任务取消，
不代表发布版完整50万计算；最终发布脚本另加持续交互及完整计算/统计/重开。
发布版状态请求测量是测试脚本的真实 native invoke，包含 Rust/后端等待，
不包含应用 JavaScript 队列；DOM 和事件循环仍从真实界面采样。

## 最终源码与冻结基础工作流

最终源码矢量/表格/栅格/混合项目回归分别10/12/10/7项通过；混合项目包含
9个数据集。历史0.5.0真实schema5项目副本迁移至6，通过源文件、Dataset JSON/
字节、图层、项目元数据和任务历史核对，保留迁移备份且原项目不变。报告位于
`.artifacts/phase2-{vectors,tables,rasters,portability,migration}-source-final/`。

`.artifacts/phase2-analysis-source-final/report.json` 为 `ok=true`，在最终生产
Python代码上完成1000/10000两级、解析相交和密集重叠拒绝后恢复成功。
初次全规模源码结果仍单独保留，最终冻结全规模另行记录。

`scripts/build-engine.ps1 -SkipSync` 成功生成0.6.0引擎；隔离PATH/Python/GDAL/
PROJ环境的smoke 59项通过，`ok=true`，见 `.artifacts/phase2-frozen-smoke/`。
冻结格式和真实历史项目迁移亦全部通过，报告路径将source替换为frozen。

## 分发验收

0.6.0 NSIS构建成功。发布EXE原生smoke与安装后smoke均 `ok=true`、
`packaged=true`，协议/schema 6；包括1面10000m²分析和统计CSV导出。
本机临时安装/卸载通过，应用和卸载注册项已移除；未操作既有用户安装。
证据：`.artifacts/native-release-phase2/native-smoke.json`、
`.artifacts/native-installed-phase2/native-smoke.json`、`.artifacts/installer-phase2.json`。

- 安装器：`apps/desktop/src-tauri/target/release/bundle/nsis/Spatial Analysis Desktop_0.6.0_x64-setup.exe`
- 文件大小：341438948字节。
- SHA-256：`981B38F236E3BE390DC75704C6627EB7B110B40D9DF06BC4F9CDBB3C9D8274E2`。
- 冻结引擎EXE：`014154A05399091B3C6D8748D4B116EF2FB5389328B047A4CF93BABB779FC916`。
- 构建目录发布EXE：`BF7D7F97A772AB424C9CF5E7B8C26393A6CB202FF67C6321F12F3691C20B722D`。
- 安装后EXE：`F1EC9783BFC0CF084FA3C893E8ECEBB219A09A06813F09C7F75E9B052278CFD9`。

两份桌面EXE的哈希差异经精确核对，仅来自Tauri的NSIS包类型标记：将发布文件
唯一的`__TAURI_BUNDLE_TYPE_VAR_UNK`（偏移7626898）在内存中换为`..._NSS`，
得到的完整哈希恰为安装后哈希；未修改文件，见`.artifacts/phase2-bundle-identity.json`。
发布smoke执行于NSIS打包时的标记版本；最终发布UI会使用已恢复UNK标记的EXE。

最终冻结guards：`.artifacts/phase2-analysis-guards-frozen-final/report.json`，
`ok=true`。复杂计算13.750秒；候选超限209.737秒明确拒绝；单面超限拒绝后
小任务5.422秒成功。状态p95 101.726ms、最大1810.957ms；树RSS采样峰值
531521536字节，2088样本，零采样错误。运行时有并发构建/轻量回归，记录于
该目录`environment-note.md`，不宣称独占机器。

Git功能提交时，最终冻结1000/10000/100000已通过，250000已完成分析/统计，
500000完整冻结与发布UI、准确提交的CI仍在验收；收尾将补齐实际结果。
