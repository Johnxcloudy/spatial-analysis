# Spatial Analysis Desktop

**当前版本：0.7.0 / Phase4A1，内部试用版本。** 新增矢量 Planning/Publication
预设、手动类型分类、符号图例和项目保存。准确范围见 [Phase4A1](docs/phase-4a1.md)。
这只是 CART-01 的一部分；类别发现、数值分级、字段标注、Layout 和正式地图导出仍待开发。
本机源码/冻结/安装及10–50万专题交互已验收，最终push与PR两条Windows CI通过，见[验收记录](docs/verification-phase-4a1.md)。
高频切换测试曾出现两次查询监控失败后恢复，最长分页4.37秒；已列为优先诊断事项，不能承诺所有请求零失败。

历史版本0.6.2 / Phase3B本机验收及其功能CI通过。修复查询截止、
启动回收及旧监控干扰后续查询的问题，增加默认关闭的宿主RPC计时和CI诊断。
Python326、前端113、Rust13通过；10万/25万/50万冻结分页36次零失败，
p95 214ms；50万原生界面反馈p95 13.4ms、最大停顿21.1ms，完整分页helper
最慢1.05秒。真实127面保全、NSIS及本机安装/卸载通过。准确范围与测量边界见
[Phase3B范围](docs/phase-3b.md)、[验收记录](docs/verification-phase-3b.md)及[执行说明](执行说明.md)。

历史0.6.1 / Phase3A交付默认分页优化及属性/统计显式重试，本机验收通过，
当时PR CI的Python测试失败仍未取得具体日志。新版本结果不能解释这次历史
失败，详见[Phase3A验证记录](docs/verification-phase-3a.md)。

面向土地利用、用地叠加与面积统计的 Windows 本地 GIS 工作站。**0.6.0 / Phase 2：土地叠加与分类面积** 已交付为内部可试用版本，使用完整托管快照执行裁剪/相交。本机合成数据五级压力（1 千至 50 万图斑）、最终冻结、发布版完整分析与交互、分发安装及 Windows CI 已通过；不等于真实数据或独立机器验收。当时公共协议/项目 schema 为 6，当前0.7.0为7；worker 协议 5、readiness 协议 1未变。准确证据见 [Phase 2 验证记录](docs/verification-phase-2.md) 与 [执行说明](执行说明.md)，历史交付记录保留。

历史0.6.0回归：Python293、前端106、Rust10项通过。历史功能提交 `9258d7859ef3c832bfd481be266df2e2250ddcf5` 的[Windows CI 34434218036](https://github.com/Johnxcloudy/spatial-analysis/actions/runs/34434218036) 成功；不将其视为当前0.6.2验收。

## 当前范围

- “用地分析”选择两个面数据集、字符串地类字段和分类标准版本，填写适用的二维分析投影与理由，生成完整几何、分类统计和计算记录。
- 用地同层正面积重叠/重复、无效几何会阻止统计；裁剪研究区按并集处理，不隐式修复、去重、吸附或删除碎面。
- 成果 GeoPackage 包含几何、统计和输入 ID/版本/CRS/算法追溯记录；统计 CSV 保留分类文本并用独立列区分 NULL。提供占研究区和占有效覆盖面积两种比例。

- 导入 GeoPackage、Shapefile、GeoJSON 和 File Geodatabase 的普通二维要素，选择源数据层与字符编码。
- 导入 CSV/XLSX 为独立表格，选择编码、分隔符、工作表和表头行；明确 X/Y 字段与来源 CRS 后生成点图层，错误行保留并注明原因。
- 导入自包含 GeoTIFF，查看波段/NoData/掩膜/单位/仿射变换，灰度或 RGB 显示，按位置查询原始像元并导出完整 GeoTIFF。
- 保留原始输入，为矢量和表格生成独立 GeoPackage 快照，记录来源、字段、完整 CRS、版本与导入检查报告。
- 栅格保留原始 GeoTIFF 字节快照，显示图像与原始像元分离；未知 CRS 仅保留元数据与数据文件，禁止地图查询。
- 地图缩放、图层显示与顺序、透明度、单一符号和分类颜色；属性分页、排序、筛选与地图选择联动。
- 导出新的 GeoPackage；项目创建、保存、关闭、重开及旧版项目迁移备份。
- 项目另存为新目录，携带完整托管快照、图层、来源位置记录和已结束的任务历史；验证完成后打开副本。
- 查看来源存在/缺失/不可访问状态；显式重新定位时核对来源身份，并追加独立位置记录。
- 导入、导出、坐标转点、分析、项目另存与来源定位任务进度和取消，日志、运行环境信息和独立 GIS 诊断。

“用地分析”的结果来自项目完整快照，按保存的投影、输入版本和面积口径计算。独立诊断中的已知矩形、相交和投影往返仅用于核对引擎，不是项目分析结果；两者均不自动证明真实数据的测绘精度。

## 使用

1. 在桌面窗口中“新建项目”，选择父目录并输入项目名称；程序创建新的项目目录，不覆盖已有项目。打开其中的 `project.spa` 可恢复工作区。
2. 点击“导入数据”。普通文件选择 `.gpkg`、`.shp` 或 `.geojson`；GDB 选择整个 `.gdb` 目录。检查并选择源数据层；Shapefile 的组成文件应放在同一目录，中文编码可指定 GBK 等选项。
3. 核对来源 CRS。缺失 CRS 时可明确声明；不声明则仅查看属性，不能显示到地图。已有 CRS 不能在导入时覆盖。显示固定使用 EPSG:3857，来源 CRS 和项目分析 CRS 分开保存。
4. 在图层侧栏调整显示与样式，在属性表排序、筛选或选择记录。“数据与检查报告”显示已完成检查、警告和未检查项。无效或空几何会保留并受限，不自动修复；不能无损保留的 Z/M 或曲线几何会被拒绝。
5. 图层名称、顺序、显示和旧样式立即保存；新专题样式先编辑草稿，点击“应用专题样式”后保存。项目名称、描述、分析 CRS 和地图视图需点击“保存项目”。刷新工作区不会替换这些未保存的编辑。移除图层仍保留托管数据快照。
6. “导出当前数据”为矢量/表格写出新的 GeoPackage，为栅格写出完整 GeoTIFF，目标文件必须不存在。重开项目读取托管快照，原始来源被移动后仍可查看已导入数据。
7. “项目另存为”选择父目录和新目录名称。副本使用当前项目草稿的名称、描述、分析 CRS 和地图视图，获得新的项目 ID；数据集 ID、版本及全部托管快照字节保持一致。原项目不会被隐式保存这些草稿修改，复制成功并发布后才切换到副本。
8. “数据与检查报告”的“来源位置”可刷新状态或“重新定位来源”。文件存在只说明可找到路径，不代表当前内容已核对；显示的核对时间是历史记录。重新定位只有在候选文件或组成文件的身份匹配时才登记新位置，保留不可变的原来源元数据；内容改变需重新导入为新数据集。

另存目标必须是已有父目录下的新目录，不能是原项目目录、其子目录或已经存在的目标。程序备份一致的项目数据库，逐份复制和核对矢量、表格、栅格快照，复制预算为 32 GiB。缓存、暂存文件、锁和旧迁移备份不作为副本的权威输入；缺失或被修改的托管快照会阻止另存。整个项目目录移动后仍可重开；由表格生成的点通过当前项目中的父数据集 ID 和版本定位来源，不依赖原项目的绝对路径。

表格使用“导入表格”：CSV 明确选择 UTF-8（兼容 BOM）、GBK 或 GB18030，以及逗号、分号、制表符或竖线；XLSX 必须选择工作表，表头行从 1 开始。预览最多 20 行，完整校验在导入任务中执行。表格出现在侧栏，可直接筛选、排序和导出。

选择表格后点击“生成点数据”，指定 X（经度/东坐标）、Y（纬度/北坐标）和来源 CRS。程序不推断坐标系、坐标顺序或数字区域格式。错误坐标保留为无几何记录，至少需要一个有效点。属性表的“源行号”对应 CSV 逻辑记录或 XLSX 行，内部 ID 独立保存。

CSV 按文本保留，包括前导零、空字符串与文字 NULL。XLSX 主表保存规范化文本/NULL，公式保留原文且不执行；单元格类型、数字格式与完整表头映射保存到同一 GeoPackage 的辅助表中。数字显示格式不应用到主表文本，工作簿版式、富文本、公式缓存和原始 XML 数字写法不作为保真范围。不支持 XLS/XLSM。

每个项目同时执行一个导入、导出、坐标转点、分析、另存或来源定位任务；只显示真实阶段和可用计数。另存期间保留原项目并锁定冲突操作，可取消复制；未完成的副本不会被当作成功项目打开。关闭或切换项目会结束活动计算，计算本身不自动重跑；已经写完但未登记的成果会在重开时按发布日志核对，大文件核对可取消并留待下次重开。引擎连接故障后需重新打开项目，未保存的表单内容会暂时保留。

栅格使用“导入栅格”，选择自包含 `.tif` 或 `.tiff`。灰度/RGB 设置按波段原始值选择显示范围；抽样最小/最大值只用于显示建议。点击地图可查询原始像元及 scale/offset 后的值，NoData 和掩膜不当作零。像元行列从 0 开始。图层样式立即保存，导出复制完整原始 GeoTIFF，不输出屏幕预览图。

栅格文件必须包含明确的仿射地理变换；未知 CRS 不自动推断。外部 `.msk`、`.aux.xml`、`.ovr` 或 worldfile 等附属文件依赖会被拒绝，需先在 GIS 软件中整理为自包含 GeoTIFF。暂不支持 GCP/RPC 定位、复数波段或栅格分析。内部金字塔原样保留，当前显示直接读取原像元，避免平均金字塔改变分类值。

GDB 支持范围是普通要素类；报告中标记“未读取”的高级元数据不应视为已保全。空项目的分析 CRS 默认未设置，填写分析 CRS 本身不会启动分析。

## 当前限制

以下为累计到 [Phase 2](docs/phase-2.md) 的保护上限；实际压力测试与环境见 [验证记录](docs/verification-phase-2.md)：

| 项目 | 上限 |
| --- | --- |
| 单次矢量导入 | 500,000 个要素、10,000,000 个顶点、单几何 100,000 顶点、512 MiB 快照、256 个字段 |
| 数据源检查 | 512 个源数据层 |
| 每个项目 | 64 个托管数据集 |
| 元数据 | 每个数据集 256 KiB；工作区响应 6 MiB |
| 属性查询 | 默认每页 200 条，最多 500 条 |
| 地图查询 | 每次最多 2,000 个要素、100,000 个显示顶点 |
| 交互地图聚合 | 矢量和栅格合计最多显示 8 个图层；矢量合计 2,000 个要素 / 40,000 顶点；省略有明确提示 |
| 土地分析 | 累计 5,000,000 候选对、最多 500,000 成果面 / 10,000,000 顶点 / 512 MiB、10,000 个分类组合 |
| 研究区裁剪边界 | 10,000 个面、1,000,000 顶点；超限拒绝并集 |
| 任务进程保护 | 900 秒、进程树 2 GiB；独立监控不依赖轮询，协作取消 2 秒后终止 |
| 查询进程保护 | 每操作 2 秒、1 GiB；冷启动单独预热，超时回收查询进程并保留项目 |
| 单次属性或地图查询 | 序列化响应最多 2 MiB |
| 表格 | 100,000 数据行、2,000,000 单元格；主表总字段最多 256 |
| 表格列数 | 最多 254 个来源列；转点最多 252 个来源列，另留内部 ID 和状态字段 |
| 表格源文件/规范化内容 | 各 128 MiB；单元格最多 65,536 字符 |
| XLSX 解压内容 | 256 MiB、10,000 个 ZIP 条目、512 张工作表 |
| 表格预览/表头 | 20 行、1 MiB；表头行 1-1000 |
| GeoTIFF | 文件 512 MiB、16 波段、单边 100,000 像元、估计完整解码 2 GiB、源块 16 MiB |
| 栅格采样/显示 | 每波段最多 256×256 抽样；每次显示最多 1024×1024，RPC 总响应 8 MiB |
| 项目另存 / 来源组成文件身份核对 | 各 32 GiB 预算 |

超限会返回错误或明确的截断提示。地图上的部分显示不代表数据集已完整显示，显示限制不改变托管快照。地图选中的要素若不在当前属性页，会在表格上方显示该记录。

自0.6.1起，默认无筛选属性分页先只读验证内部ID与实际整数主键对应，再直接寻址；每次仍有验证扫描，自定义排序/筛选和不符合条件的快照保留原查询语义。查询失败显示“属性读取失败”和“重试当前页”，保持页码、每页数量、筛选和排序；统计页可“重试当前统计页”。程序不会自动无限重试，也不提高原有超时预算。切换项目或数据集后旧请求不再写入当前页面。历史0.6.1原生测试有一次成功读取约4秒，原因仍未确定；0.6.2新增分层计时，本次分页helper最长1.05秒，存在宿主排队的时间关联证据。界面反馈不等于整页完成；详细范围见[本轮验收记录](docs/verification-phase-3b.md)。

栅格导出沿用暂存副本和目标目录待发布副本流程，两个位置均需容纳一份完整文件。64 位整数像元原值以字符串返回；int64/uint64 波段中超出 JavaScript 安全整数范围的 NoData 标签拒绝导入，详见 Phase 1C 范围。正式面积使用指定投影下的平面几何面积，不代表台账面积或测绘精度；未知 CRS、Web Mercator、三维/复合 CRS、范围不适合及缺失转换格网均被拒绝。

50 万图斑是有几何复杂度、候选量和内存边界的验收规模。简单图斑成功不保证任意 50 万复杂图斑都可计算；超预算会明确失败，保留输入和已有成果。显示截断不改变正式计算范围。CSV 请按文本列导入表格软件，避免其自动去除前导零；应用不执行分类文字中的表达式。

历史0.6.0发布版合成50万完整分析354.201秒；计算期间DOM反馈p95 14.7ms、事件循环最大24.8ms、状态p95 295.6ms，另测取消445ms。曾出现一次默认分页query_timeout，后续成功，不能以查询计划断言唯一原因。0.6.1改进默认分页并提供显式重试，最终50万分页交互DOM p95 12.8ms/事件循环最大30ms；这次没有重跑完整50万分析，DOM反馈也不是整页查询耗时。新进程/连接不等于清空系统缓存，仍保留冷读验收。

历史 Shapefile 的组成文件若使用混合大小写基名，保持各文件原有名称与大小写、仅移动目录时可以重新定位；整组改名后可能无法匹配旧身份。旧版来源元数据没有保存每个组成文件的原始名称大小写，不能从已有指纹还原，程序不会猜测或绕过核对。

矢量图层的“专题样式”提供 Planning/Publication 起始预设，可调整填色、描边、线宽和点半径，手动配置文字、数值及布尔类别。NULL 与未配置值单独设色，空字符串和文字 null 不合并。图例只说明已配置符号，不证明类别实际存在、数量或全量覆盖。最多64个类别、32 KiB配置；改色不重新请求几何，分类字段变化才更新视窗属性。应用保存后可重开/另存，恢复原有样式会保留递增版本以拒绝旧草稿覆盖。预设不代表正式地类标准或期刊合规。

CART-00 制图草案和独立渲染探针是历史实验，详见 [探针证据](docs/verification-cartography-probe.md)。产品 Layout、正式制图导出及 Agent 仍按 [制图路线图](docs/cartography-roadmap.md) 后续实施。

## 安装与开发

Windows x64安装器使用NSIS，包含Python/GDAL引擎和WebView2离线安装组件；安装版无需另外安装Python。0.7.0本机临时安装、安装后分析运行和卸载已验收；产物校验和、发布版交互与CI状态见[当前验证记录](docs/verification-phase-4a1.md)。安装包尚未签名，独立Windows10/11、离线/升级和真实正式业务分析仍待验收。

开发环境需要 Node.js 24、Rust 1.98 或以上、Visual Studio C++ Build Tools、Windows SDK、WebView2 和 uv。冻结引擎验证脚本需要 PowerShell 7。Python 3.12 及 GIS 依赖由 uv 安装到 `gis-engine/.venv`。

在仓库根目录执行：

```powershell
npm ci
uv sync --project gis-engine --frozen --group dev
npm run tauri -- dev
```

根目录通过 npm scripts 调用 apps/desktop，并在 postinstall 安装桌面依赖；两份 package-lock.json 都需要提交。该结构不依赖 npm workspace 符号链接，可在本机 exFAT 工作盘上安装。

新安装 Rust 后需要重新打开终端，或为当前 PowerShell 添加路径：

```powershell
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
```

可通过 `SPATIAL_ENGINE_PYTHON` 指定开发用解释器。安装版忽略该开发配置并使用安装目录内的引擎。

```powershell
npm run dev
```

此命令仅启动浏览器界面，默认地址为 [http://127.0.0.1:1420](http://127.0.0.1:1420)。浏览器没有原生桥接，不能进行本地项目或 GIS 操作；完整功能需运行桌面程序。

## 测试与构建

```powershell
npm run check
npm test
npm run build
uv run --project gis-engine --frozen --group dev pytest gis-engine/tests
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml --lib
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
```

安装器输出到 apps/desktop/src-tauri/target/release/bundle/nsis/。构建包含 PyInstaller 引擎和 WebView2 离线安装组件，首次构建会下载较多依赖。安装包尚未签名，不等于正式生产发布。

实际测试、原生工作流、安装包和校验和见[Phase4A1验证记录](docs/verification-phase-4a1.md)，未完成项以记录为准。历史[Phase3B](docs/verification-phase-3b.md)、[Phase3A](docs/verification-phase-3a.md)、[Phase 2](docs/verification-phase-2.md)、[Phase 1D](docs/verification-phase-1d.md)、[Phase 1C](docs/verification-phase-1c.md)、[Phase 1B](docs/verification-phase-1b.md)、[Phase 1A](docs/verification-phase-1a.md)与[Phase 0](docs/verification.md)记录保留，不代替当前版本验收。

生成真实 GDAL 驱动写出的合成样本，并验证开发引擎的四类格式导入、查询、导出和项目恢复：

```powershell
$fixtureDir = '.artifacts/vector-fixtures-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$verificationDir = '.artifacts/vector-acceptance-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
uv run --project gis-engine --frozen --group dev python scripts/generate-vector-fixtures.py $fixtureDir
uv run --project gis-engine --frozen --group dev python scripts/verify-vectors.py --output $verificationDir --fixtures "$fixtureDir/manifest.json"
```

两个输出目录均须不存在。验证脚本写入 `vector-verification.json` 和引擎日志；合成样本仅用于验收，不代表真实规划数据或完整格式兼容性。

完成构建后，使用上节生成的样本验证冻结引擎：

```powershell
$engine = (Resolve-Path '.\apps\desktop\src-tauri\resources\engine\spatial-engine.exe').Path
pwsh -File scripts/smoke-engine.ps1 -Executable $engine
$frozenVerificationDir = '.artifacts/vector-frozen-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
uv run --project gis-engine --frozen --group dev python scripts/verify-vectors.py --executable $engine --output $frozenVerificationDir --fixtures "$fixtureDir/manifest.json"
```

表格验收脚本自行生成 UTF-8、GB18030 与多工作表 XLSX 合成样本，通过持续 RPC 和真实 worker 检查导入、源行号、错误坐标、导出、取消与重开：

```powershell
uv run --project gis-engine --frozen python scripts/verify-tables.py --output .artifacts/tables-source-new
uv run --project gis-engine --frozen python scripts/verify-tables.py --executable $engine --output .artifacts/tables-frozen-new
```

栅格验收生成已知原值的灰度/RGB、旋转投影网格和未知 CRS 样本，检查解码后的 PNG 像元、NoData、原始像元位置、精确导出和取消：

```powershell
uv run --project gis-engine --frozen python scripts/verify-rasters.py --output .artifacts/rasters-source-new
uv run --project gis-engine --frozen python scripts/verify-rasters.py --executable $engine --output .artifacts/rasters-frozen-new
```

项目携带验收自行生成混合矢量、表格/派生点和栅格样本，检查当前草稿另存、全部快照身份、原项目保留、来源缺失/重新定位、整项目移动及重开后的查询和导出：

```powershell
uv run --project gis-engine --frozen python scripts/verify-portability.py --output .artifacts/portability-source-new
uv run --project gis-engine --frozen python scripts/verify-portability.py --executable $engine --output .artifacts/portability-frozen-new
```

输出目录须不存在，结果写入 `portability-verification.json`。`scripts/verify-migration.py --source-project <旧版项目.spa> --output <新目录>` 复制 schema 2/3/4/5 项目后检查升级到 schema 6 和迁移备份，不直接升级提供的原项目；应用也支持打开 schema 1 项目后先备份再迁移。

桌面可执行文件支持 `--smoke-test <输出目录>`，用于验证宿主调用、项目保存重开、GIS 诊断、矢量导入、表格转点、真实栅格导入/渲染响应/查询/导出及项目另存/来源状态；此模式生成 native-smoke.json 后退出。它不操作 React/OpenLayers 界面。

原生界面验收使用 `scripts/verify-rasters-ui.mjs`，通过开发版 WebView2 的本机 CDP 端口运行 Playwright，检查桌面/窄视口截图、实际 Canvas 像元、透明度、缩放和混合图层。只替换文件选择器，GIS 调用保留真实原生链路。该脚本及历史项目副本迁移脚本目前在本机执行；CI 包含单元测试、源码/冻结 RPC 工作流和宿主冒烟，不包含完整原生 UI 自动化。实际结果见当前验证记录。

另存和来源定位原生工作流使用已通过的混合项目报告，在启用 CDP 的开发版桌面窗口运行：

```powershell
node scripts/verify-portability-ui.mjs --fixtures .artifacts/portability-source-new/portability-verification.json --cdp http://127.0.0.1:9224
```

土地分析压力验收使用完整合成快照、预设解析面积和资源门槛；完整运行需要数十分钟，输出目录必须是新目录：

```powershell
uv run --project gis-engine --frozen python scripts/verify-analysis.py --output .artifacts/analysis-source-new --counts 1000 10000 100000 250000 500000
uv run --project gis-engine --frozen python scripts/verify-analysis.py --executable $engine --output .artifacts/analysis-frozen-new --counts 1000 10000 100000 250000 500000
uv run --project gis-engine --frozen python scripts/verify-analysis-guards.py --executable $engine --output .artifacts/analysis-guards-new
```

每级核对导入、末页、取消后新任务、完整分析/统计、GPKG/CSV导出、重开及另存追溯。
guards 单独验证复杂几何成功、真实候选预算和单面顶点超限拒绝、随后小任务恢复。
`scripts/verify-analysis-ui.mjs --fixtures <成功的report.json> --cdp <本机CDP地址>`
测量真实桌面的交互、事件循环、任务状态和取消；加 `--packaged --complete-large`
用于发布版及完整大任务。仅替换文件选择器，原生GIS调用保持真实；两种RPC测量
层级的差别见 [验收记录](docs/verification-phase-2.md)。

独立制图探针的隔离依赖、源码/冻结检查和资产替换负例命令见 [CART-00 探针说明](scripts/cartography-probe/README.md)。其依赖未加入产品引擎，实验输出不能视作已交付制图功能。

“运行诊断”保留合成几何、投影、GeoPackage 和 GeoTIFF 基础读写检查；报告写入独立缓存子目录，路径在界面中显示。GitHub Actions 在 push/PR 时运行检查并构建 Windows 安装器，不自动发布公开 Release。

## 项目结构

```text
apps/desktop/             React 工作区
apps/desktop/src-tauri/   Rust 宿主与安装配置
gis-engine/              Python 引擎、依赖锁和测试
shared/                  TypeScript 类型与 RPC 协议
scripts/                 引擎打包、安装构建和验证
docs/                    方案、数据规范、架构与验收记录
```

项目入口为 `project.spa`（SQLite 元数据），托管矢量和表格使用真正的 GeoPackage，栅格使用原生 GeoTIFF，缓存与原始数据分开管理。当前应用为0.7.0，公开协议和项目schema为v7，内部worker协议为v5；打开schema1–6项目时先备份，再通过事务迁移到v7，保留历史数据集JSON、旧样式和托管快照字节。新专题样式存于独立的 vector_cartography 表。不要将真实项目、缓存和诊断产物提交到代码仓库。

开发排查慢请求时，可在启动桌面EXE前设置`SPATIAL_TRACE_RPC=1`，退出后移除
该环境变量。额外计时默认关闭，写入既有`desktop.log`（Windows通常为
`%LOCALAPPDATA%/com.johnxcloudy.spatialanalysis/logs/`）。记录请求ID/固定方法、
宿主排队/进程启动/交换/收尾/总时长与错误种类，不含请求参数或响应数据。
启动时长只计创建进程/管道，Python初始化落在交换阶段；总时长不含新增日志
落盘或Tauri返回/UI排队。原有2秒查询预算不等于整次UI操作耗时上限。

## 文档

- [执行说明与上下文交接](执行说明.md)
- [优化方案](docs/plan-v2.md)
- [后续制图与成果输出路线图](docs/cartography-roadmap.md)
- [GIS 数据可靠性规范](docs/gis-data-standard.md)
- [当前架构](docs/architecture.md)
- [项目数据模型](docs/data-model.md)
- [Phase 1A 实施范围](docs/phase-1a.md)
- [Phase 1B 实施范围](docs/phase-1b.md)
- [Phase 1C 实施范围](docs/phase-1c.md)
- [Phase 1D 实施范围](docs/phase-1d.md)
- [Phase 1D 验证记录](docs/verification-phase-1d.md)
- [制图契约草案](docs/cartography-spec-draft.md)
- [CART-00 探针证据](docs/verification-cartography-probe.md)
- [Phase 1C 验证记录](docs/verification-phase-1c.md)
- [Phase 1B 验证记录](docs/verification-phase-1b.md)
- [Phase 1A 验证记录](docs/verification-phase-1a.md)
- [Phase 0 历史验证记录](docs/verification.md)
- [阶段待办](TODO.md)

正式发布前仍需在无开发环境的 Windows 10/11 上验收安装、离线依赖和升级行为，并使用真实规划样本核查互操作性。
