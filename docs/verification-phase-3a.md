# Phase 3A 验证记录（0.6.1，本机验收完成，远程验收待闭环）

目标0.6.1；公共协议/schema6、worker5、查询就绪1不变。数据源为用户明确
提供的本地test目录，原文件只读、产物仅本机。本机源码/冻结/原生/安装验收
已完成；功能代码已推送。远程push与PR分别记录，PR测试失败未解释，不能
宣布全部CI通过或整个Phase3完成。

## 环境与代码复核

2026-09-10，本机Windows 11 Pro 10.0.26200、i5-10400F、约16GiB内存，
I:为exFAT。沿用锁定的Python/GDAL/PROJ依赖，未增加运行时依赖。

- 查询定向测试45项通过（9.84秒），最终完整Python312项通过（107.64秒），
  见 `.artifacts/phase3a-python-final.log`。
- 前端12文件/113项通过（24.82秒），类型检查和生产构建通过，见
  `.artifacts/phase3-ui-full.log` 和 `.artifacts/phase3-ui-delivery.md`。
- Rust10项通过、格式检查通过，见 `.artifacts/phase3a-rust.log`。
- 独立后端14个反例探针通过，包括不同排序规则、非规范ID/类型、主键缺口、
  metadata不一致、字段名冲突及响应超限后释放；未发现当前范围阻断项。
  `.artifacts/phase3a-backend-review.md`。根另复核前端身份隔离与禁连点。
- 89个源/契约/锁文件记录在 `.artifacts/phase3a-code-final.json`，封包与本机
  验收后重新计算，drift为空；未重复执行已通过且代码未变的完整回归。

## 默认分页调查与源码压力

只读调查比较原数值CAST排序、表达式索引、旁路排序缓存及每页认证。最终
选择单请求只读事务/短文件lease认证，再主键寻址；无缓存，无快照改写。
认证仍扫描所有行，不能宣称总复杂度为O(页大小)。自定义排序、筛选和未通过
认证的历史布局保留原SQL语义，共用原1.5秒SQL/2秒查询进程保护。

源码报告 `.artifacts/phase3-pagination-source-1/report.json` 为ok=true。
100000/250000/500000分别查询输入及分析成果，每组3个新worker的首/末页，
合计36次查询，p95 267.186ms、最大278.758ms；原快照和测试副本哈希未变。
50万输入末页SQL总时间原320.9–326.8ms、优化193.4–206.0ms；成果末页
原444.6–532.7ms、优化217.1–245.6ms。对比为同机SQL路径测量，非旧版和
新版完整UI之间的速度承诺。

探针使用Phase2已完成样本，实际执行当前源码查询；新连接/新进程、复制与
哈希均不能代表操作系统冷缓存。报告字段后续补充fixtureRuntime与当前源码
版本区别，未变生产查询。原始调查与历史快照反例在
`.artifacts/phase3-pagination-investigation/`。CI加入当前小规模分页流程，
不上传用户真实数据或本机项目。

## 真实数据首轮源码验收

只读初检：一个公开面层，127个MultiPolygon、5170顶点，单几何最多145顶点。
来源声明EPSG:4525；四个float64字段无NULL，没有字符串分类字段和独立研究区。
1个环自相交，无空/缺失/Z/M几何；未检查同层重叠、间隙和GDB高级语义。
45个原文件共244423字节，初检前后集合/哈希/mtime一致。
初检报告：`.artifacts/phase3-realdata-inspection/report.json`。

`scripts/verify-real-vectors.py` 使用真实持续RPC及独立Arrow读取，按源FID
比较全部字段值和规范化WKB的摘要（仅归一环/部件顺序，不修复或舍入），
核对CRS、数量、顶点、无效几何及空值。原始属性值仅在内存比较，不写入报告。

`.artifacts/phase3-realdata-source/report.json` 为ok=true、engine0.6.1。
完整导入3.407秒、导出2.391秒、重开/另存1.766秒；首末分页、数值升降排序、
选择、有界viewport、真正GPKG导出重读和另存身份/字节通过。该首轮执行时
默认分页优化尚在整合，最终冻结须再验收，不冒称已经验证最终二进制。
127面和1个无效几何原样保留；导入报告为restricted、geometry_valid计数1。
快照221184字节，来源45文件前后集合/哈希一致。

该数据不能直接用于当前正式分类面积流程：缺少字符串分类/分类标准、研究区
和无效几何处理政策。未从文件名补造分类，未自动修复、丢弃该图斑或把字段
area/MJ猜成特定单位。数据成功导入不等于通过业务分析或ArcGIS互操作验收。

## 最终源码与冻结真实数据

最终 `.artifacts/phase3-realdata-source-final/report.json` 与
`.artifacts/phase3-realdata-frozen-final/report.json` 均ok=true、engine0.6.1。
两次分别核对来源FID对应的全部字段值/类型/NULL、规范化WKB、CRS、127面/
5170顶点/1个无效几何。首末页、数值升降排序、选择、127个完整viewport、
GPKG导出独立重读、重开与另存身份/字节均通过，45个原文件前后集合和SHA256
未变。报告不含原始属性值或几何；真实项目、截图和输出只留在本机忽略目录。

| 流程 | 最终源码秒 | 最终冻结秒 |
| --- | ---: | ---: |
| 枚举/独立扫描 | 3.531 | 11.766 |
| 导入/独立重读 | 5.922 | 5.641 |
| 分页/排序/选择/viewport | 0.516 | 0.406 |
| 导出/独立重读 | 10.922 | 3.157 |
| 重开/另存 | 2.688 | 3.234 |

并行封包期间的单次测量，不作为源码/冻结性能差异的结论。精确命令、日志
与环境见 `.artifacts/phase3a-assigned-acceptance.md`。无效几何保留restricted，
没有修复、舍弃或用文件名制造地类；未执行正式业务面积分析。

## 最终冻结分页与分析回归

`.artifacts/phase3-pagination-frozen-final/report.json` 为ok=true：
100000/250000/500000输入与分析成果、每组3个新worker首末页共36次，零失败，
p95 **265.148ms**、最大**304.710ms**，输入及副本哈希不变。实际执行当前
冻结查询，复用Phase2已完成合成快照；不是重新进行50万完整分析。复制/哈希
及SQL比较会预热文件缓存，新worker不等于真正OS冷缓存；运行时NSIS封包仍在
进行，见 `.artifacts/phase3-pagination-frozen-final-environment.md`。

最终当前源码分析1000/10000、冻结分析1000及解析相交/密集重叠拒绝后恢复
通过：`.artifacts/phase3a-analysis-source-final/report.json`、
`.artifacts/phase3a-analysis-frozen-final/report.json`。源码取消到终态
0.318/0.263秒，冻结0.765秒。源码task.get p95 172.804ms、最大1294.319ms；
冻结p95 150.378ms、最大293.079ms。采样进程树RSS峰值437739520/426909696字节，
不等于瞬时峰值或独占机器测量。0.6.0完整50万分析仍只作历史证据。

隔离冻结冒烟 `.artifacts/phase3a-frozen-smoke/frozen-engine-smoke.json`
59/59通过。首次命令误用相对EXE路径，脚本在启动前拒绝；更正绝对路径后通过，
该调用错误保留在验收记录，不计为首次通过或产品失败。

## 最终发布版与本机安装

`scripts/build-engine.ps1 -SkipSync`、`scripts/build-windows.ps1 -SkipEngine`
均exit0。日志 `.artifacts/phase3a-engine-build.log`、
`.artifacts/phase3a-windows-build.log`。Rust release编译2分02秒；MSVC创建库/
对象信息被列为linker_messages警告，未发生构建错误。

发布EXE和安装后EXE的native smoke均ok=true、packaged0.6.1、protocol/schema6，
包括真正10000m²分析/CSV、另存与来源定位。报告分别位于
`.artifacts/native-release-phase3a/native-smoke.json`、
`.artifacts/native-installed-phase3a/native-smoke.json`。本机临时安装、登记版本/
路径、引擎字节核对、卸载和注册项清理通过：`.artifacts/installer-phase3a.json`。
测试前没有已有安装；没有覆盖用户安装。独立机器、离线和升级尚未执行。

| 产物 | 字节/身份 |
| --- | --- |
| `apps/desktop/src-tauri/target/release/bundle/nsis/Spatial Analysis Desktop_0.6.1_x64-setup.exe` | 341436559字节 |
| 安装器SHA256 | `B005C7A797FFFE8BAE49C1FE92F6C125EC1B0C88D480D1538FA60DC22C0DC82A` |
| 冻结引擎SHA256 | `BCA2DC2D4DCCA84D26403B685EB66FB772F01BC945DA99011313F5A88FAC4A8F` |
| 发布桌面SHA256 | `c90a9c1b8d811c5b5131d900440698ceba2e8e248251f2102790030889a2e6a0` |
| 安装后桌面SHA256 | `CAD4C87B41DA080073DE90111727A053CFE35006A62BBADBB76A6B1F85FD70DA` |

两份桌面哈希差异已精确验证为Tauri唯一包类型标记UNK→NSS，偏移7626802；
内存中只替换此标记所得哈希与安装版一致，未改发布文件。证据
`.artifacts/phase3a-bundle-identity.json`。安装器尚未签名。

## 发布版界面

`scripts/verify-pagination-ui.mjs` 使用真实native IPC、最终release EXE和独立
项目副本。首轮 `.artifacts/phase3a-ui-1789018696444/report.json` ok=true：
真实127面首50/末27、无效计数1、地图及重开；合成50万首末页、16轮页容量/
隐藏标签切换通过。32次DOM反馈p95 **12.9ms**，902个定时样本最大停顿
**22.4ms**，页面错误为0，无正向页面溢出。根复核50万末页和错误重试截图。
测量门槛DOM p95≤150ms、事件循环最大≤500ms未调整；RPC helper计时不含
应用JS排队，但包含实际native调用及可能的query_unready等待/重发，不能
将DOM反馈时间说成整页查询时间，也不能一概当作单个native调用耗时。

另行注入一次明确的模拟query_timeout，验证不自动循环、局部显示实际失败，
点击“重试当前页”将相同参数送给真实后端并成功。此注入不计入性能样本，
不称真实冷读超时。初次补拍质量详情时原进程已不在，CDP连接拒绝；报告
`.artifacts/phase3a-ui-1789018804192/report.json` 保留，未执行GIS。此前安装器
正在运行，不以该连接前置失败否定已结束的首轮成功，也不算产品回归。

安装/卸载完全结束后，最终脚本单次复验
`.artifacts/phase3a-ui-1789018953302/report.json` ok=true；额外滚动展示无效
计数1，7张截图均无正向页面溢出、页面错误0。全部真实/合成/重试流程再次
通过，32次DOM反馈p95 **12.8ms**、1037个定时样本最大**30ms**；此轮作为
最终UI脚本证据。根复核质量详情、50万末页和重试界面，release哈希前后
相同，代理所属app/engine进程已清理。日志 `.artifacts/phase3-ui-native-final.log`。

最终报告38次vector.page helper计时p95 **460.731ms**，其中一次50万末页
offset499800/limit200成功耗时 **3998.075ms**（error=null）。它发生在定时/
DOM采样区间前；未定位具体耗时层级，helper会包含query_unready就绪等待/
重发但没有逐次计时，不能断言此长调用由该等待或冷读造成，也不能据此断言
单次SQL/worker突破原限时。38次不是独立冻结36次探针，不混合汇总；保留此
尾延迟供后续分层诊断，不因界面响应通过而隐去。收尾只修正脚本报告的计时
范围文字，原报告保留；未改变运行逻辑或重复执行已通过的UI流程。

## Git与CI

功能提交 `4db4999c5075123e5e99b2853130864d7b0621ab` 已推送至现有
`feat/phase-1a`，远程SHA核对一致，没有强推或上传用户数据。

- [push 34441137906](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34441137906)：
  completed/success，job102756247329；30个可见步骤全部success，包括Python/
  前端、源码格式与新增分页、冻结工作流、NSIS、native smoke及上传。
  完整SHA链接吻合，公开页面可见对应安装产物名称，未下载检验内容。
- [PR 34441140400](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34441140400)：
  head_sha同上，Test engine失败，后续GIS与封包步骤跳过。公开注释仅exit1。
  API先前可读随后限流；当前浏览器明确“Sign in to view logs”，没有读取单条
  失败日志或下载CI产物。PR实际checkout可能是合并引用，不能仅凭相同head_sha
  假定完全相同执行树；未证实其原因，也未用重跑掩盖原失败。

证据 `.artifacts/ci-phase3a.json`。收尾文档/本地UI验收脚本另行提交，按主题
`chore: record Phase 3A local acceptance`定位，不能把功能CI覆盖到新脚本。
**PR CI失败尚未闭环；本机验收通过不代表远程全部通过。**

## 剩余条件

1. 读取PR失败步骤的授权日志，核实checkout与具体测试，按证据修复/重验。
   另为原生分页约4秒尾延迟补分层计时，区分就绪等待、宿主排队与实际查询。
2. 提供明确地类字段/标准、独立研究区和无效几何处理政策后，再验真实业务
   叠加/分类面积；继续ArcGIS及GDB高级语义独立对照。
3. 真正OS冷缓存、独立无开发环境Windows、离线/升级与签名验收。

正式制图和Agent尚未实施；Phase3A本机交付不等于整个稳定版阶段完成。
