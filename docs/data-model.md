# Phase 0 数据模型

项目格式、协议和应用版本分别管理：项目 schemaVersion=1，protocolVersion=1，应用版本 0.1.0。

Project 保存 id、name、description、createdAt、updatedAt、analysisCrs、displayCrs 和 viewState。projectPath 为打开位置，不应被当作永久来源身份；具体存储结构以引擎 repository 的 schema 为准。

创建项目不能覆盖已存在的 project.spa；打开前检查项目身份、版本和必要结构。引擎对活动项目持有操作系统文件锁。保存使用 SQLite 事务，校验失败保留既有内容。关闭引擎或项目时释放锁。

空项目不推断分析 CRS，analysisCrs 为 null。显示状态的中心是经纬度坐标，显示 CRS 初始为 EPSG:3857。诊断采用独立 EPSG:4547 小样，不修改项目分析 CRS。

诊断报告保存实际测量值、期望值、检查明细、库版本、文件路径和耗时，并提供少量预览几何。它是诊断产物，不冒充未来正式 Analysis/Result 或用户业务成果。

Dataset、Layer、Analysis、Result、Task 的正式模型在后续阶段按已批准方案扩展。未实现的数据表不提前伪造记录。未来变更 schema 必须有迁移和备份策略，不能直接写入不支持的新版本。
