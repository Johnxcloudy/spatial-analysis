# Phase 3B / 0.6.2 验收记录

日期：2026-09-10。范围见 [phase-3b.md](phase-3b.md)，交接见
[执行说明](../执行说明.md)。实现、本机分发验收及本轮功能push/PR CI通过。
公共协议/schema 6、worker 5、readiness 1不变。所有真实数据、诊断报告、
项目和安装产物只保存在本机忽略目录，不随 Git 上传。

## 已复现问题与修复证据

本轮修复三个独立复现的生命周期缺口，不把它们追认为历史 CI 或原生
3998.075ms 分页事件的根因：

1. 延迟 watchdog 时，父进程会接受超过2秒操作截止的成功帧。现在在读取
   结果后按单调时钟再次核对截止和资源失败原因，超时回收并允许后续恢复。
2. 创建 Job、资源监控或 reader 失败时，已经创建的子进程可能残留。
   现在清理部分初始化的进程、Job、guard和管道，失败重启后的同一runner可恢复。
3. 人为暂停资源采样2.3秒，旧监控在stop返回后可能终止后续查询复用的worker；
   原实现3/3复现。现在短锁同步退休与reason/Job关闭/kill派发，慢采样、树发现、
   join和wait在锁外。显式关闭/取消仍可终止进程。

原始探针与审查：`.artifacts/phase3b-query-probes.py/.json`、
`phase3b-stop-probe.py/.json`、`phase3b-query-audit.md`。runner首轮6失败/1通过，
guard首轮4失败；完成实现后runner8、guard6、既有capacity16共30项通过，
实际子进程取消/迁移互斥2项通过。Rust计时测试先2失败，再完成实现并通过。

边界：stop可能等待已开始的OS关闭/kill；原有最多1.5秒monitor join仍能增加
RPC尾耗时。2秒是就绪后的操作预算，不能称完整UI请求在2秒内返回。
3.5秒人工预热探针产生3.797秒helper、末次操作约16ms，只证明一种可能路径。

## 回归与代码身份

本轮最终运行结果，不因后续仅文档变更重复测试：

| 检查 | 实际结果 | 本机证据 |
| --- | --- | --- |
| Python完整回归 | 326通过，92.75秒 | `.artifacts/phase3b-python-final.log`、同名前缀XML |
| 前端12文件 | 113通过，49.46秒 | `.artifacts/phase3b-frontend-tests.log` |
| TypeScript / Vite | 通过 | `.artifacts/phase3b-frontend-check.log`、`phase3b-frontend-build.log` |
| Rust / format | 13通过、格式通过 | `.artifacts/phase3b-rust-final.log` |
| CI摘要脚本 | 6通过；0.6.2提交前再次执行通过 | `scripts/tests/test_ci_pytest_summary.py` |
| UI诊断工具 | 语法及fake transport检查通过 | `.artifacts/phase3b-ui-investigation-test.json` |

已测生产、契约、测试和锁文件共111份的SHA256清单为
`.artifacts/phase3b-code-final.json`；最终构建前及功能提交前核对无漂移。
功能提交`b3c7100fa81e8fa46c3e1a1ffd3211e47c3657d8`已推送现有`feat/phase-1a`。

## 数据保全与实际分析

- 源码及冻结的真实GDB验收均`ok=true`，运行时0.6.2、协议6，packaged分别
  false/true。127个MultiPolygon、5170顶点、4个数值字段、声明EPSG:4525；
  1个环自相交保留restricted。首末页/排序/地图读取、导出重读、另存/重开及
  快照身份检查通过；45份原文件共244423字节，前后集合和SHA保持一致。
- 证据：`.artifacts/phase3b-realdata-source-final/report.json`及
  `.artifacts/phase3b-realdata-frozen-final/report.json`。未自动修复或补造地类；
  缺少字符串地类及标准、独立研究区与几何处理政策，未生成正式业务面积。
- 当前源码分析1000/10000、冻结分析1000均通过取消后重算、裁剪、统计、
  成果末页、GPKG/CSV、重开及另存追溯哈希；解析矩形相交、密集重叠拒绝后
  恢复通过。取消到终态源码0.339/0.389秒、冻结0.737秒。
- 证据：`.artifacts/phase3b-analysis-source-final/report.json`及
  `.artifacts/phase3b-analysis-frozen-final/report.json`。这些功能验收与构建
  存在并发负载，时间不是独占性能基准；没有重跑完整50万分析。

## 大规模冻结分页

最终封包结束后执行，未并行运行本任务的构建或其他GIS验收：

```powershell
uv run --project gis-engine --frozen python scripts/verify-pagination.py `
  --fixtures .artifacts/phase2-analysis-frozen-final/report.json `
  --counts 100000 250000 500000 --workers 3 `
  --executable 'I:/spatial analysis/apps/desktop/src-tauri/resources/engine/spatial-engine.exe' `
  --output .artifacts/phase3b-pagination-frozen-final
```

进程exit0、报告`ok=true`。10万/25万/50万输入和成果，每组3个新worker读取
首末页，共36次零失败；p95 **214.047ms**、最大**215.984ms**。六组来源及
副本哈希不变；保留SQL1.5秒、查询2秒/1GiB预算，无自动失败重试。
冻结EXE SHA256：`48BDDFFCEA62E577492DD92977E99E98ACC644598D83F742095A35FBB923F24F`。

这次实际执行0.6.2查询，但复用Phase2合成快照；详细SQL阶段使用当前源码，
冻结worker时间独立记录。复制/哈希/SQL比较会预热文件缓存；新worker不等于
OS冷缓存，逐页认证仍扫描全行，也不构成正式复杂面分析吞吐量承诺。

## 原生计时与界面

`SPATIAL_TRACE_RPC=1`才启用宿主计时；默认关闭。既有轮转desktop.log追加
版本、hostPid/requestId、允许的方法名、epoch及queue/startup/exchange/finish/total、
outcome/errorKind，不含参数、路径、值或几何。新增计时日志在会话锁释放后写。

queue不含编码；startup只计原生spawn/管道，Python预热在exchange；exchange计
写入/刷新/读取，finish计解析及必要回收，total包含编码但不含新增日志写入和
Tauri/UI返回。不能把这些字段直接等同SQL耗时。

诊断工具单列helper每次native尝试及query_unready等待，通过InvokeOptions
header标记测试请求；观察器开始前的尝试保留且不计标记覆盖分母。缺标记、
无可覆盖请求或溢出时不宣称完整归因。fetch完成表示响应收取，WebView fallback
仅有派发时间；未观测产品JS队列入口。模拟失败独立于真实IPC性能采样。

最终原生报告`.artifacts/phase3b-ui-investigation-1789022312654/report.json`
`ok=true`、packaged0.6.2。真实127面首50/末27、质量1、地图及重开通过；
50万合成输入16轮页容量/隐藏/切换通过。32次DOM反馈p95 **13.4ms**，
898次定时采样最大停顿**21.1ms**，保留150/500ms门槛；7份截图、页面错误0，
根代理复核质量详情、末页及超时提示截图。独立注入一次query_timeout，显式
重试同参数转发真实后端成功；不将这次人为失败计入性能或称为自然超时。

42次helper均为单次native尝试、没有query_unready等待；38次分页helper的
p95 **443.481ms**，最慢分页helper
**1050.791ms**，其中native **1038.700ms**、其余工具开销**12.091ms**。
浏览器观察161个事件、66个操作，42个helper标记覆盖完整、无溢出；产品自身
请求仍出现初始化query_unready，不能由helper无等待推断整个应用没有预热。

宿主追加日志只读取`[732158,799389)`字节范围并过滤本次hostPid24416，
161条记录中150成功、11个query_unready。全部方法宿主total p95 442.300ms、
最大1726.882ms（初次runtime.info含启动）；87条vector.page宿主total
p95 452.778ms、最大1034.933ms。证据同目录`host-trace-analysis.json`。

最慢helper时间窗口与host请求45高度一致：queue **826.682ms**、exchange
**207.764ms**、finish **0.469ms**、total **1034.933ms**；相邻请求44为
vector.viewport，exchange **851.561ms**，请求46为后续vector.page。
这支持当前长请求包含宿主排队的判断，但host与helper没有共享ID，只能称
时间关联，不能宣称严格唯一的因果配对。旧3998.075ms事件未复现且缺少旧
分层证据，原因仍未知。开启诊断有额外开销，界面反馈不等于整页完成时间。

本轮原生验收在本任务其他GIS/构建/安装结束后进行；测试app/engine已清理，
release哈希前后相同。复制项目上的页切换验证不是新完整50万分析。

## 构建与安装

冻结引擎和Windows/NSIS构建exit0，证据`.artifacts/phase3b-engine-build.log`及
`phase3b-windows-build.log`。MSVC输出一条本地化linker信息警告，构建成功。
环境隔离的冻结smoke59/59通过，见`.artifacts/phase3b-frozen-smoke-final/`。
release native smoke通过packaged0.6.2、协议/schema6、10000m²分析/CSV及另存，
见`.artifacts/native-release-phase3b/native-smoke.json`。

本机临时安装及卸载exit0，版本/位置核对、安装版原生分析/CSV/另存/来源定位
均通过，安装引擎SHA与冻结引擎一致；卸载后app与注册项均已移除，未覆盖已有
安装。证据`.artifacts/installer-phase3b.json`及
`.artifacts/native-installed-phase3b/native-smoke.json`。产物位置：
`apps/desktop/src-tauri/target/release/bundle/nsis/Spatial Analysis Desktop_0.6.2_x64-setup.exe`。
安装器341440164字节，SHA256
`C4BC53E851EECE82EC05D62940B7FF6A9CE216F67D4F486910301E4090540C4D`。
release桌面SHA256为`1AF53D85984D079CA3AAE97B51E7D7EAA4C2EA1B59B7EE44EA1D94B962913E0E`；
安装桌面SHA256为`4BB8D286DFACD7E89F986A15420342C3647D9CD9A5491837CE4E3B1CEF4A1C54`。
在内存中仅替换唯一Tauri `__TAURI_BUNDLE_TYPE_VAR_UNK`为`NSS`后，哈希与
安装版完全一致；标记偏移7635442，未改release文件，见
`.artifacts/phase3b-bundle-identity.json`。未沿用上一版本的偏移假设。
未签名。本机验收不能替代独立无开发环境Windows或离线/升级验收。

## Git / CI 与历史边界

诊断提交`4fcf6827b3b8564cb55ff3928a83422f5d3b2c6c`的push34443699050与
PR34443702648均成功，各32可见步骤；它们的生产版本仍为0.6.1，不能作为
0.6.2功能验收。步骤摘要正文未能公开读取，因此本轮新增有界notice展示实际
checkout、事件、原pytest outcome及JUnit汇总，保留原退出码。

0.6.2功能提交`b3c7100fa81e8fa46c3e1a1ffd3211e47c3657d8`对应的两条运行均
completed/success，各32个可见步骤全通过，包括安装器、native smoke及上传：

| 事件 | 运行 | 公开notice实际checkout / GITHUB_SHA |
| --- | --- | --- |
| push | [34445680269](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34445680269) | `b3c7100fa81e8fa46c3e1a1ffd3211e47c3657d8` |
| pull_request | [34445683926](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34445683926) | `f8d8e1c046cb1420fa32bb1a6b504f815914e8f0`（合并快照） |

两份公开notice均为outcome success、JUnit available，326 tests、0 failures、
0 errors、0 skipped；新诊断的公开可见性已经实际验证。证据
`.artifacts/ci-phase3b-functional.json`及其记录的run/job HTML，未下载CI产物，
也未读取需登录的完整日志。PR合并快照不冒充feature HEAD。

收尾只改README、AGENTS、TODO、范围/计划/验收文档和执行说明，使用主题
`chore: close Phase 3B delivery [skip ci]`。不把功能CI说成对尚未生成的文档
提交另跑CI；文档链接、空白、原文件保全、111份源码清单和产物身份在收尾
检查中复核，Git提交后再保存实际HEAD/remote/干净工作区证据。

最终收尾检查`.artifacts/phase3b-closeout-check.json`通过：77个本地文档链接
无缺失、交接19行、111份已测代码无漂移、10份本机报告均通过，安装器及
桌面/引擎哈希一致，45份原始文件244423字节再次核对未变。原生测试进程与
临时安装已经清理。Git收尾结果写入`.artifacts/phase3b-git-closeout.json`。

历史0.6.1 PR34441140400在Test engine失败，公开日志要求登录，原因仍未知。
新成功运行不解释旧失败；不提取凭据或猜测失败测试。历史原生约3.998秒helper
也没有可追溯的分次计时，不能用本轮探针反推其唯一根因。

## 待办边界

真实分类/研究区/几何政策、ArcGIS与GDB高级语义对照、独立Windows、真正
OS冷缓存、离线/升级及签名仍待条件。正式制图和Agent仍按未来路线推进。
本阶段不代表所有Phase3外部稳定版验收完成。
