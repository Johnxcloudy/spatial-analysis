# Phase 1A 数据模型

项目格式、协议和应用版本分别管理：项目 schemaVersion=2，protocolVersion=2，应用版本 0.2.0。

Project 保存 id、name、description、createdAt、updatedAt、analysisCrs、displayCrs 和 viewState。projectPath 为打开位置，不应被当作永久来源身份；具体存储结构以引擎 repository 的 schema 为准。

创建项目不能覆盖已存在的 project.spa；打开前检查项目身份、版本和必要结构。引擎对活动项目持有操作系统文件锁。保存使用 SQLite 事务，校验失败保留既有内容。关闭引擎或项目时释放锁。

空项目不推断分析 CRS，analysisCrs 为 null。显示状态的中心是经纬度坐标，显示 CRS 初始为 EPSG:3857。诊断采用独立 EPSG:4547 小样，不修改项目分析 CRS。

诊断报告保存实际测量值、期望值、检查明细、库版本、文件路径和耗时，并提供少量预览几何。它是诊断产物，不冒充未来正式 Analysis/Result 或用户业务成果。

VectorDataset 保存独立数据 ID/版本、源文件与图层、内容指纹、原始和有效 CRS、内部相对路径、字段模型、几何范围、稳定 ID 字段和完整扫描报告。每份快照为独立 GeoPackage，读取来源不因图层隐藏或删除而改变。重新导入创建新数据身份，不能把新文件同一行视为原来的要素。

MapLayer 单独保存名称、Dataset 引用、可见性、顺序、透明度、颜色和分类字段/颜色。图层编辑即时持久化；项目名称、描述、分析 CRS 与地图视图使用项目保存。删除图层只移除显示配置，保留已登记快照及来源记录。

Task 保存 import/export 类型、真实阶段、可用计数、状态、错误、输入/输出身份及时间。running、completed、failed、cancelled、interrupted 含义不同；只有发布与登记成功才进入 completed。任务的临时请求/进度/结果文件不替代 project.spa 的权威登记。

旧 v1 项目在取得独占锁后，通过 SQLite backup API 生成并验证备份，再事务创建新表及更新版本；备份失败或迁移失败不得直接改写版本。未来版本仍拒绝。具体表结构以 projects.py/workspace.py 的实现为准。

Analysis 和 Result 业务对象留到 Phase 2。当前没有正式土地统计成果，不能把导入报告当作分析合格证书。
