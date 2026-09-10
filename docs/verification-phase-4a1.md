# Phase4A1 / 0.7.0 验证记录

日期：2026-09-10。范围见[阶段说明](phase-4a1.md)及[实施计划](superpowers/plans/2026-09-10-phase-4a1.md)。本机交付及最终功能CI通过；这是带明确查询恢复限制的内部试用版本，外部待办未标为通过。

## 当前证据

- Python全套：`uv run --project gis-engine --frozen --group dev python -m pytest gis-engine/tests --junitxml=.artifacts/phase4a1-tests/python.xml -q`，386 passed，313.34秒；日志`.artifacts/phase4a1-python.log`。
- Rust格式与`cargo test --locked --lib`：13 passed；日志`.artifacts/phase4a1-rust.log`。
- 最终前端：135 passed / 25.23秒；类型检查、Vite生产构建通过。日志`.artifacts/phase4a1-frontend-final.log`、`phase4a1-check-final.log`、`phase4a1-build-frontend.log`。首轮119 passed / 5 failed均为既有测试5000ms超时，当时与Python/Cargo并行运行；保留首轮日志，单独最终复测没有重现，不从时间相关性断定唯一原因。
- 新源码流程：`.artifacts/phase4a1-cartography-source-2/report.json`，6步骤、34预期拒绝，8面/8类与800m²覆盖面积，3份快照、来源、属性/统计在配置修改、重开、另存后保持。首轮`.artifacts/phase4a1-cartography-source/report.json`失败是验收脚本请求统计limit200超过接口100，已修正脚本并保留原报告。
- 聚焦后端：132 passed / 26.89秒。新增畸形schema表RED5项后修复主键/类型/非空约束验证；读取损坏配置拒绝且事务不留下名称修改。
- 独立复核发现业务字段与OpenLayers内部属性冲突、Unicode/空标签前后端限制不一致；补充RED回归后修复。地图通过WeakMap隔离原属性，保留几何和要素ID；文字按Unicode字符计数，不截断代理项，允许合法空标签。原生验收使用独立地图canvas像素，不仅检查图例。
- 真实源码：`.artifacts/phase4a1-realdata-source/report.json`，127面、5170顶点、1无效几何保持受限；导入/分页/导出独立重读/重开/另存通过，45原文件244423字节哈希不变。
- 迁移源码：`.artifacts/phase4a1-migration-source/migration-verification.json`，历史schema6/1000面clip项目副本迁到7，3快照/元数据/图层/任务保持，生成一次schema6备份，重开无重复备份。补充audit验证备份记录等价；SQLite备份头部3字节不同，不宣称整个项目数据库备份逐字节相同。
- 最终冻结引擎冒烟通过，`.artifacts/phase4a1-frozen-smoke/frozen-engine-smoke.json`。124个代码/契约/锁文件的本轮清单为`.artifacts/phase4a1-code-final.json`，引擎独立54文件清单为`.artifacts/phase4a1-engine-final.json`，构建后继续核对漂移。

冻结专题/真实/迁移三个流程均通过，汇总`.artifacts/phase4a1-frozen-workflows-audit.json`核对0.7.0/协议7/packaged=true及全过程引擎SHA不变。专题仍为6步骤34拒绝、8类800m²，真实仍为127/5170/1无效与45原文件不变，迁移仍为6→7及3快照/备份记录保全。冻结专题步骤耗时合计26.735秒、真实14.343秒，只适用于本机本次小样本。

## 分发与本机安装

最终Windows构建成功，安装器`apps/desktop/src-tauri/target/release/bundle/nsis/Spatial Analysis Desktop_0.7.0_x64-setup.exe`，341476773字节，SHA-256为`5CF649F3C193B7725BA3EF635AD408453F6F8BC5471A094FAD6707F236491405`。

发布版`.artifacts/native-release-phase4a1/native-smoke.json`和安装版`.artifacts/native-installed-phase4a1/native-smoke.json`均通过：打包0.7.0、协议/schema7、10000m²解析分析、CSV、另存/定位。临时目录安装与卸载成功，应用文件及注册项清理，见`.artifacts/installer-phase4a1.json`；这只是当前开发机的本机验收。

引擎SHA-256为`EB82C9185B499C8D6DC5F5D590EC46F84E7D9E4B45B4214D889D53915A0F7AE9`；发布EXE为`3F1B885FF52430195B80B0FCA6BD83F173D8FD596FEE2ED2C1D03F36F6D4EAB5`，安装EXE为`549613C926A27E9871F98B8220E2D5CD338BE2C7DF99995017F7AC70B06F03A9`。差异经`.artifacts/phase4a1-bundle-identity.json`验证仅为Tauri的UNK→NSS标记，不修改产物来迎合哈希。

## 原生专题与响应性

最终发布EXE通过`.artifacts/phase4a1-ui-1789027798118/report.json`，真实127面/质量/重开、50万16轮分页与切换、独立注入一次超时后的同参数真实重试均通过，未发生JS pageerror。截图均未出现横向溢出，已人工检查25万及50万专题截图。

三档均隔离显示输入层、定位后放大到图斑可读的视窗；检查真实成功的viewport以及地图canvas的精确不透明RGB像素，独立核对图例。两次改色不重取几何，Planning/Publication、4个手动类别加NULL/其他、重开/恢复通过，每档3份快照和数据集JSON保持。

| 项目图斑数 | 专题操作事件循环最大停顿 | 两次改色观测耗时 |
| --- | --- | --- |
| 100000 | 37.8 ms | 206.6 / 160.8 ms |
| 250000 | 31.6 ms | 173.4 / 154.3 ms |
| 500000 | 20.3 ms | 173.9 / 152.6 ms |

改色耗时包含Playwright输入、应用、原生workspace查询和canvas核对，不能当作纯引擎计算时间。50万分页阶段DOM反馈p95为13.9ms，最大事件循环停顿25.9ms；DOM反馈不等于整页完成。使用既有规模快照而非新全量分析，屏幕仍执行2000要素/40000顶点预算。

**本轮存在真实查询恢复问题，report.ok不是零RPC错误的声明。** 原生诊断捕获2次viewport `query_monitor_failed`、44次`query_unready`（含启动/恢复）及关闭切换中的1次`project_not_active`。helper28分页共4371.587ms，其中首次请求在宿主队列等待2224.908ms，7次就绪等待合计1821.6ms，最后成功尝试259.5ms；同期viewport112/200分别在2314.139/2295.324ms后报monitor_failed。界面没有卡死，后续查询和三档专题检查成功，但这些失败不能隐去。

宿主日志按本次PID26256和追加偏移取证，保存338条计时于`.artifacts/phase4a1-ui-launch-26256-host.json`。底层psutil异常类型/发生阶段没有被现有日志保留，无法证明监控失败唯一原因。代码还存在清理异常可能覆盖原始预算错误类别的歧义；后续应先增加有界诊断和受控回归，再决定修复，保持资源保护。当前4.37秒的可观测分解不能反推历史3998ms原因。

## Git与CI

功能提交`ff7d6c8d04aa0ae1489a8b858f0a1f29a37f078b`及根package版本补全`2f445d0d2baace83a8b4579e32998873bf8c73b6`已推送`feat/phase-1a`。后者是最终代码核对HEAD；两份根package内容从本机测试起就是0.7.0，第二提交补齐首次暂存遗漏，不改变已验收应用代码。

最终push [34454596269](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34454596269)和PR [34454602030](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34454602030)均success，各33个可见步骤全部成功，包括源码/冻结专题、Windows安装器、原生冒烟及产物上传。两份公开pytest notice均386 tests、0 failures、0 errors、0 skipped。

Push实际checkout为`2f445d0d2baace83a8b4579e32998873bf8c73b6`；PR实际checkout为`652997991953ed1efd9eac742155eff4ebc22cd6`，公开提交页核对父提交为`64f417deed92f365522630c15effbc5fd540b5a8`和`2f445d0d2baace83a8b4579e32998873bf8c73b6`。不把移动中的merge ref或PR快照说成功能HEAD。证据在`.artifacts/ci-phase4a1-functional.json`、五份原始run/job/commit页面及其SHA清单；未读取需登录的完整步骤日志，也未下载CI产物。

最终收尾仅修改README、AGENTS、TODO、阶段范围/计划/验收/路线图和执行说明，提交主题`chore: close Phase4A1 delivery [skip ci]`。本轮功能CI不冒充文档提交另跑CI；实际HEAD/remote/干净工作区另存`.artifacts/phase4a1-git-closeout.json`。

本机最终收尾检查通过：97个本地链接、18行交接、11份报告、124代码/54引擎文件无漂移、45份原文件244423字节不变、安装器及卸载和两份准确CI证据；见`.artifacts/phase4a1-closeout-check.json`。进程复核无本轮GIS程序残留。

## 持续边界

本轮不做类别发现、数值分级、字段标注、Layout、正式地图输出或Agent。配置图例不证明类别存在、数量或完整覆盖，预设不构成规划或期刊合规认证。

大规模专题交互复用历史100000/250000/500000合成快照副本，不是新一轮完整50万分析，也不代表OS冷缓存。真实GDB仅127面，缺明确地类标准/字段、独立研究区和无效几何政策，不编造正式业务面积。

独立Windows、ArcGIS、离线/升级、真实正式业务、OS冷缓存和签名仍待。历史0.6.1的PR测试失败和约3998ms分页事件原因仍未知；本轮结果不解释这些历史事件。
