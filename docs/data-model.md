# 当前数据模型：schema7

项目格式、协议和应用版本分别管理：当前代码为 schemaVersion=7、protocolVersion=7、应用版本0.7.0，私有任务worker协议5、查询worker就绪协议1。范围见[Phase4A1](phase-4a1.md)，验收状态以[执行说明](../执行说明.md)和阶段验证记录为准；本页仅说明数据结构和行为。

Project 保存 id、name、description、createdAt、updatedAt、analysisCrs、displayCrs 和 viewState。projectPath 为打开位置，不应被当作永久来源身份。`project.spa` 是项目元数据SQLite，版本保存于`PRAGMA user_version`，应用标识为`0x53504131`；它不是GeoPackage。结构由[projects.py](../gis-engine/src/spatial_engine/projects.py)创建和验证，公开形状由[contracts.ts](../shared/contracts.ts)定义。

当前项目元数据表如下。schema7沿用既有表结构，新增`vector_cartography`，没有独立的`analyses`或`results`项目表。

| 表 | 主要列与职责 |
| --- | --- |
| `project_metadata` | singleton=1；identity、project_id、名称/描述/时间、analysis_crs、display_crs、view_state |
| `datasets` | dataset_id主键、version、唯一relative_path、dataset_json、created_at；UPDATE/DELETE触发器禁止原地修改已登记数据 |
| `map_layers` | layer_id主键、dataset_id外键、name、visible、opacity、color、category_field、category_colors、唯一display_order、可空raster_style |
| `tasks` | task_id主键、kind、status、stage、completed/total、时间、dataset_id、destination、error |
| `pending_publications` | task_id外键、dataset_json、暂存/最终相对路径、artifact_size及artifact_sha256；用于快照发布和登记恢复 |
| `pending_exports` | task_id外键、临时/目标路径、artifact_size及artifact_sha256；用于外部导出发布恢复 |
| `source_locations` | location_id主键、dataset_id外键、source_path、fingerprint、verified_at、唯一task_id外键；只追加，UPDATE/DELETE触发器保留历史 |
| `pending_copies` | task_id外键、临时/目标路径、新project_id、plan_json、可空manifest_json；用于另存发布恢复 |
| `vector_cartography` | 非空layer_id主键并外键引用map_layers，删除图层时CASCADE；非空INTEGER revision为1–9007199254740991，spec_json为可空TEXT |

创建项目不能覆盖已存在的 project.spa；打开前检查项目身份、版本和必要结构。引擎对活动项目持有操作系统文件锁。保存使用 SQLite 事务，校验失败保留既有内容。关闭引擎或项目时释放锁。

空项目不推断分析 CRS，analysisCrs 为 null。显示状态的中心是经纬度坐标，显示 CRS 初始为 EPSG:3857。诊断采用独立 EPSG:4547 小样，不修改项目分析 CRS。

诊断报告保存实际测量值、期望值、检查明细、库版本、文件路径和耗时，并提供少量预览几何。它是诊断产物，不冒充未来正式 Analysis/Result 或用户业务成果。

VectorDataset 保存独立数据 ID/版本、源文件与图层、内容指纹、原始和有效 CRS、内部相对路径、字段模型、几何范围、稳定 ID 字段和完整扫描报告。每份快照为独立 GeoPackage，读取来源不因图层隐藏或删除而改变。重新导入创建新数据身份，不能把新文件同一行视为原来的要素。

MapLayer 单独保存名称、Dataset 引用、可见性、顺序、透明度、旧颜色和分类字段/颜色。已提交的图层编辑即时持久化；项目名称、描述、分析 CRS 与地图视图使用项目保存。删除图层只移除显示配置及其vector_cartography行，保留已登记快照及来源记录。

矢量层可显式采用`VectorCartographySpec`版本1；它是图层样式组件，不是页面或正式导出契约。配置保存kind=vector-layer、revision、不可变dataset ID/version、起始Planning/Publication预设ID及版本、已解析的填色/描边/pt线宽/点半径、single或categorized渲染器，以及图例标题/可见性。分类保存显式的有类型值、标签和颜色，NULL与未配置值各有独立符号。图例不记录或推断类别实际存在、数量或完整覆盖；主题也不代表地类标准或期刊合规。

未提交过配置应用或恢复操作时没有vector_cartography行，MapLayer可省略cartography和cartographyRevision，语义revision为0，原样式保持生效。采用后新配置是唯一活动矢量样式；旧color/categoryField/categoryColors保留且不能同时编辑。每次应用或恢复都通过layer.update携带expectedCartographyRevision；事务内核对当前版本，配置revision必须为当前+1。`cartography=null`恢复原样式并将spec_json置为SQL NULL，同时递增并保留revision，避免旧草稿在恢复/再采用后意外生效。名称、可见性和图层透明度仍独立管理；栅格层不接受此矢量配置。

Python对写入与读取均执行严格字段、版本、输入身份和数值校验。配置最多64个非NULL类别；文字值最多1024字符，标签/图例标题最多120字符，紧凑UTF-8 JSON最多32KiB。数值有限且绝对值不超过9007199254740991；更大的整数字段值沿既有传输约定用文字配置。类别按类型和值联合去重，NULL、字符串`"null"`、空字符串、0和false互不代替。颜色仅接受#RRGGBB；线宽0–6pt，点半径1–16pt，屏幕以96/72换算CSS像素。样式不读取类别全集、不修改数据快照或分析口径。

RasterDataset 保存独立原生 GeoTIFF 快照及宽高、仿射变换、分辨率、波段类型、NoData、掩膜、单位、scale/offset、颜色解释和抽样显示范围。路径为 rasters/<UUID>.tif，版本为完整文件 SHA-256，不填造矢量字段、要素数或 ID。Raster MapLayer 另存 rasterStyle（灰度/RGB、波段、原值范围、最近邻）。显示 PNG 与原始数据分离，像元查询返回源行列和精确原值字符串。

TableDataset 保存独立 CSV/XLSX 表格，geometryType、CRS 和范围均为 null，featureCount 表示行数。数据写入真正的 GeoPackage attributes 表 records；XLSX 单元格类型和数字格式放在同一文件的 cell_metadata 中。主表保存文本或 NULL，不承诺工作簿排版保真或公式计算。表格不创建 MapLayer，属性页与导出可直接使用。

坐标转点创建新的 VectorDataset，记录父表 ID/版本、X/Y 字段和声明 CRS。每行对应一条结果，错误坐标保留属性、来源行号和错误原因，几何为 NULL；父表快照不被改写。

Task 保存 import/export/points/save_as/relocate/analysis 类型、真实阶段、可用计数、状态、错误、输入/输出身份及时间。running、completed、failed、cancelled、interrupted 含义不同；只有发布与登记成功才进入 completed。任务的临时请求/进度/结果文件不替代 project.spa 的权威登记。

旧v1–v6项目在取得独占锁后，通过SQLite backup API生成并验证备份，再事务迁移到schema7。schema5增加来源位置历史和复制发布日志，schema6增加analysis任务类型；schema7新增独立的矢量制图表，不自动采用新样式或改写历史图层配置。备份失败或迁移失败不留下部分迁移；不支持的新版本拒绝打开。项目schema7、AnalysisRecord schema1与VectorCartographySpec版本1分别演进。

另存为给新项目分配独立 ID 和创建时间，保留 Dataset JSON、ID、版本、快照字节、图层、已提交制图配置及其revision和历史记录。当前项目表单草稿只写入副本，不隐式保存原项目；尚未点击应用的专题样式草稿不属于已存项目。通过 SQLite backup 建立一致性数据库，按注册表复制全部数据；缓存、锁文件、旧迁移备份和暂存任务不属于复制输入。文件夹移动后，托管路径和派生点的父表按当前项目目录解析。

来源重新定位核对原始导入指纹后写入 source_locations，不修改只读 datasets。来源存在与身份已核对是不同状态；verifiedAt 仅表示过去一次成功核对。更改了内容的来源必须重新导入，不可借重新定位替换数据版本。具体存储、迁移和恢复见 projects.py、workspace.py、portability.py。

Phase2起已实现clip/intersect分析记录及分类面积结果。每次分析创建新的analysis ID和VectorDataset ID；成果以source.driver=SpatialAnalysis登记到datasets，实际文件为`datasets/<UUID>.gpkg`。来源metadata记录两份输入dataset ID/version、analysis ID及operation；成果图层复用普通MapLayer，显示样式不参与分析输入选择或统计分母。

分析GeoPackage包含`features`几何层及登记为GeoPackage attributes的`analysis_record`和`analysis_statistics`表。analysis_record以id=1保存AnalysisRecord schema1 JSON，记录输入版本/地类字段/要素数/CRS、classificationStandard、投影分析CRS及选择理由、单位换算、变换操作/格网、算法与库版本、几何和重叠政策、研究区/覆盖/未覆盖/记录面积、输出数量与警告。classificationStandard是请求中显式保存的标准说明，不构成外部标准合规证明。

analysis_statistics保存input_class、overlay_class、feature_count、area_m2、area_ha、area_mu、study_ratio、coverage_ratio。NULL类别保持SQL NULL；coverage_ratio在覆盖面积为0时为NULL。当前面积方法为projected_planar，拒绝正面积重叠，不自动修复、吸附或移除碎面；研究区由overlay几何并集定义。真实数据是否满足业务政策及外部对照验收仍需相应证据，不能把导入报告或图面外观当作分析合格证书。具体实现见[analysis.py](../gis-engine/src/spatial_engine/analysis.py)。
