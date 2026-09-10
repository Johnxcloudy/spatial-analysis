# Phase4A1：专题样式与图例基础

状态：0.7.0内部试用版已交付，本机及最终功能push/PR CI通过。两次原生查询
监控失败后恢复的限制见[验收记录](verification-phase-4a1.md)，不宣称零RPC错误。

用户于2026-09-10授权开始下一步、完成后提交Git。本轮选择路线图中可在
现有条件下完成的CART-01A，目标应用0.7.0、公开协议/项目schema7，worker5、
readiness1不变。Phase3外部验收依赖地类/研究区/几何政策、ArcGIS及独立环境，
继续保留；不以新增制图能力宣称那些验收已经完成。

## 本轮交付

矢量图层可显式采用Planning或Publication预设，编辑单一/明确分类的填色、
描边、线宽与点半径，保存图例标题及可见性。控件使用本地草稿，点击应用后
由Python校验和事务保存；重开/另存保留完整配置。提供显式恢复原有样式。
分类可手工录入文字、有限安全数值及布尔值，空字符串可合法录入；NULL独立
设色，未配置值统一使用“其他”，不把NULL、文字null、0、false合并。

图例只说明当前配置的符号，不宣称某类别真实存在、数量或全量分类覆盖。
本轮不读取类别全集，也不从视窗/属性页抽样生成完整图例；用户可以逐项
配置关注类别并明确“其他”。类别发现、数值分级、字段标注、Layout及正式
PNG/PDF分别留给后续子阶段，CART-01整体仍是部分交付。

## 契约和持久化

`shared/contracts.ts`拥有`VectorCartographySpec`版本1（kind=vector-layer）：
revision、不可变dataset ID/version、起始预设ID/version、解析后的符号数值、
single/categorized renderer及图例。它是未来完整Cartography Spec的图层样式
组件，不冒充已实现页面或导出契约。预设仅记录起点、允许手动覆盖，Planning
不等于正式地类标准，Publication不代表期刊合规。

另建`vector_cartography(layer_id, revision, spec_json)`表，避免改变历史map_layers
严格列校验。schema1–6迁移先备份，新增表不改旧颜色/分类配置；旧图层在
显式采用新配置前保持原显示。存在新配置时它是唯一活动矢量样式；旧color/
categoryField/categoryColors修改被拒绝，名称、可见性、透明度仍独立管理。
`layer.update`中的cartography=null恢复未改动的旧样式，但表中保留递增revision
与NULL配置，避免恢复/再采用后旧编辑重新生效。新配置revision从1开始，每次
应用/恢复都携带expectedCartographyRevision并递增；配置revision须为当前+1。
MapLayer返回cartographyRevision，未曾采用时可缺省为0。过期编辑报明确冲突；
数据输入版本不匹配、未来spec版本或未知字段一律拒绝。

每份配置最多64个非NULL类别，类型和值联合去重；文字值最多1024字符、
标签/图例标题最多120字符，配置编码不超过32KiB。数值有限且绝对值不超过
9007199254740991，较大整数按既有传输规则使用文字类别。颜色仅#RRGGBB，
strokeWidthPt为0–6、pointRadiusPt为1–16，屏幕按96/72换算CSS像素。
这些尺寸不承诺地图上的地面宽度或正式输出纸面比例。

Python保存和读取时均严格验证，事务失败不留半配置；来源数据、快照哈希、
分析记录/面积/单位保持不变。旧类别对象改为自有属性查找，避免constructor/
toString命中原型产生非法颜色；除此之外不默默重绘旧分类。

## 性能、范围和方案比较

选择“显式采用的小型版本化样式组件”，使旧项目保留原样式，新样式/图例
共用解析器。直接扩充旧字符串颜色字典无法可靠区分NULL/文字null；一次性
实现完整Layout与分类计算则引入不同的资源/坐标/输出验收，本轮分开推进。

前端按配置编译类型安全的颜色映射和OpenLayers样式缓存。改颜色/线宽/
预设不重新读取几何；分类字段改变才改变viewport propertyFields。继续使用
最多8个显示图层、2000要素/40000前端顶点预算；后台viewport100000顶点
预算不变。没有新增全量同步遍历，原数据/分析参数不因制图改变。

## 验收与分工

- backend负责cartography.py、projects.py/workspace.py及对应测试：schema1–6
  迁移/备份、未知或损坏配置拒绝、字段/版本/类别校验、过期revision不覆盖、
  恢复旧样式、Save As与快照/分析统计不变。
- frontend负责vector-style、VectorStyleEditor、VectorLegend、LayerPanel、
  VectorMap、相关样式与测试：类型区分/原型名称、图例与地图共用解析、草稿
  应用/恢复及失败保留，不因改色重新取几何。
- root拥有公开契约、bridge/use-workspace、版本/锁、验收脚本、文档与Git；
  先聚焦RED/GREEN，再整套回归、合成/真实127及10–50万原生响应、冻结分发/
  本机安装与当前功能CI。生产完成前再次独立审查。

全部真实数据及生成物留在忽略目录；保持feat/phase-1a、不强推。完成后更新
执行说明/短交接/TODO，核对实际Git/CI；无手动压缩接口时如实说明。
