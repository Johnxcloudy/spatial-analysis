# Spatial Analysis Desktop

面向土地利用、用地叠加与面积统计的 Windows 本地 GIS 工作站。当前版本为 **0.4.0 / Phase 1C：GeoTIFF 基础**，本机交付与对应代码的 Windows CI 已通过；范围和验证边界见 [Phase 1C 验证记录](docs/verification-phase-1c.md)。

## 当前范围

- 导入 GeoPackage、Shapefile、GeoJSON 和 File Geodatabase 的普通二维要素，选择源数据层与字符编码。
- 导入 CSV/XLSX 为独立表格，选择编码、分隔符、工作表和表头行；明确 X/Y 字段与来源 CRS 后生成点图层，错误行保留并注明原因。
- 导入自包含 GeoTIFF，查看波段/NoData/掩膜/单位/仿射变换，灰度或 RGB 显示，按位置查询原始像元并导出完整 GeoTIFF。
- 保留原始输入，为矢量和表格生成独立 GeoPackage 快照，记录来源、字段、完整 CRS、版本与导入检查报告。
- 栅格保留原始 GeoTIFF 字节快照，显示图像与原始像元分离；未知 CRS 仅保留元数据与数据文件，禁止地图查询。
- 地图缩放、图层显示与顺序、透明度、单一符号和分类颜色；属性分页、排序、筛选与地图选择联动。
- 导出新的 GeoPackage；项目创建、保存、关闭、重开及旧版项目迁移备份。
- 导入、导出、坐标转点任务进度与取消，日志、运行环境信息和独立 GIS 诊断。

**尚不提供正式叠加、裁剪、分类面积或占比计算。** 诊断中的已知面积矩形、相交和投影往返仅用于核对引擎，不是项目分析结果，也不证明真实数据的测绘精度。

## 使用

1. 在桌面窗口中“新建项目”，选择父目录并输入项目名称；程序创建新的项目目录，不覆盖已有项目。打开其中的 `project.spa` 可恢复工作区。
2. 点击“导入数据”。普通文件选择 `.gpkg`、`.shp` 或 `.geojson`；GDB 选择整个 `.gdb` 目录。检查并选择源数据层；Shapefile 的组成文件应放在同一目录，中文编码可指定 GBK 等选项。
3. 核对来源 CRS。缺失 CRS 时可明确声明；不声明则仅查看属性，不能显示到地图。已有 CRS 不能在导入时覆盖。显示固定使用 EPSG:3857，来源 CRS 和项目分析 CRS 分开保存。
4. 在图层侧栏调整显示与样式，在属性表排序、筛选或选择记录。“数据与检查报告”显示已完成检查、警告和未检查项。无效或空几何会保留并受限，不自动修复；不能无损保留的 Z/M 或曲线几何会被拒绝。
5. 图层名称、顺序、显示和样式立即保存；项目名称、描述、分析 CRS 和地图视图需点击“保存项目”。刷新工作区不会替换这些未保存的编辑。移除图层仍保留托管数据快照。
6. “导出当前数据”为矢量/表格写出新的 GeoPackage，为栅格写出完整 GeoTIFF，目标文件必须不存在。重开项目读取托管快照，原始来源被移动后仍可查看已导入数据。

表格使用“导入表格”：CSV 明确选择 UTF-8（兼容 BOM）、GBK 或 GB18030，以及逗号、分号、制表符或竖线；XLSX 必须选择工作表，表头行从 1 开始。预览最多 20 行，完整校验在导入任务中执行。表格出现在侧栏，可直接筛选、排序和导出。

选择表格后点击“生成点数据”，指定 X（经度/东坐标）、Y（纬度/北坐标）和来源 CRS。程序不推断坐标系、坐标顺序或数字区域格式。错误坐标保留为无几何记录，至少需要一个有效点。属性表的“源行号”对应 CSV 逻辑记录或 XLSX 行，内部 ID 独立保存。

CSV 按文本保留，包括前导零、空字符串与文字 NULL。XLSX 主表保存规范化文本/NULL，公式保留原文且不执行；单元格类型、数字格式与完整表头映射保存到同一 GeoPackage 的辅助表中。数字显示格式不应用到主表文本，工作簿版式、富文本、公式缓存和原始 XML 数字写法不作为保真范围。不支持 XLS/XLSM。

每个项目同时执行一个导入、导出或坐标转点任务；只显示真实阶段和可用计数。关闭或切换项目会结束活动任务，中断任务不自动恢复。引擎连接故障后需重新打开项目，未保存的表单内容会暂时保留。

栅格使用“导入栅格”，选择自包含 `.tif` 或 `.tiff`。灰度/RGB 设置按波段原始值选择显示范围；抽样最小/最大值只用于显示建议。点击地图可查询原始像元及 scale/offset 后的值，NoData 和掩膜不当作零。像元行列从 0 开始。图层样式立即保存，导出复制完整原始 GeoTIFF，不输出屏幕预览图。

栅格文件必须包含明确的仿射地理变换；未知 CRS 不自动推断。外部 `.msk`、`.aux.xml`、`.ovr` 或 worldfile 等附属文件依赖会被拒绝，需先在 GIS 软件中整理为自包含 GeoTIFF。暂不支持 GCP/RPC 定位、复数波段或栅格分析。内部金字塔原样保留，当前显示直接读取原像元，避免平均金字塔改变分类值。

GDB 支持范围是普通要素类；报告中标记“未读取”的高级元数据不应视为已保全。空项目的分析 CRS 默认未设置，填写分析 CRS 本身不会启动分析。

## 当前限制

以下是 [Phase 1A](docs/phase-1a.md)、[Phase 1B](docs/phase-1b.md) 与 [Phase 1C](docs/phase-1c.md) 的保护上限，不是大数据性能承诺：

| 项目 | 上限 |
| --- | --- |
| 单次导入 | 100,000 个要素、2,000,000 个顶点、256 个字段 |
| 数据源检查 | 512 个源数据层 |
| 每个项目 | 64 个托管数据集 |
| 元数据 | 每个数据集 256 KiB；工作区响应 6 MiB |
| 属性查询 | 默认每页 200 条，最多 500 条 |
| 地图查询 | 每次最多 2,000 个要素、100,000 个显示顶点 |
| 单次属性或地图查询 | 序列化响应最多 2 MiB |
| 表格 | 100,000 数据行、2,000,000 单元格；主表总字段最多 256 |
| 表格列数 | 最多 254 个来源列；转点最多 252 个来源列，另留内部 ID 和状态字段 |
| 表格源文件/规范化内容 | 各 128 MiB；单元格最多 65,536 字符 |
| XLSX 解压内容 | 256 MiB、10,000 个 ZIP 条目、512 张工作表 |
| 表格预览/表头 | 20 行、1 MiB；表头行 1-1000 |
| GeoTIFF | 文件 512 MiB、16 波段、单边 100,000 像元、估计完整解码 2 GiB、源块 16 MiB |
| 栅格采样/显示 | 每波段最多 256×256 抽样；每次显示最多 1024×1024，RPC 总响应 8 MiB |

超限会返回错误或明确的截断提示。地图上的部分显示不代表数据集已完整显示，显示限制不改变托管快照。地图选中的要素若不在当前属性页，会在表格上方显示该记录。

栅格导出沿用暂存副本和目标目录待发布副本流程，两个位置均需容纳一份完整文件。64 位整数像元原值以字符串返回；int64/uint64 波段中超出 JavaScript 安全整数范围的 NoData 标签拒绝导入，详见 Phase 1C 范围。待后续开发：Phase 1D 的项目另存/来源重新定位与跨格式回归；正式土地叠加和面积统计属于 Phase 2。

## 安装与开发

Windows x64 安装器使用 NSIS，包含 Python/GDAL 引擎和 WebView2 离线安装组件；安装版无需另外安装 Python。安装包尚未签名，独立 Windows 10/11、离线环境和真实规划数据验收的进度见 [当前验证记录](docs/verification-phase-1c.md)。

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

实际测试、原生工作流、安装包和校验和记录见 [Phase 1C 验证记录](docs/verification-phase-1c.md)。历史 [Phase 1B](docs/verification-phase-1b.md)、[Phase 1A](docs/verification-phase-1a.md) 与 [Phase 0](docs/verification.md) 记录保留，不用于替代当前版本验收。

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

输出目录须不存在。`scripts/verify-migration.py --source-project <旧版项目.spa> --output <新目录>` 复制 schema 2/3 项目后检查迁移和备份，不直接升级提供的原项目。

桌面可执行文件支持 `--smoke-test <输出目录>`，用于验证宿主调用、项目保存重开、GIS 诊断、矢量导入、表格转点和真实栅格导入/渲染响应/查询/导出；此模式生成 native-smoke.json 后退出。它不操作 React/OpenLayers 界面。

原生界面验收使用 `scripts/verify-rasters-ui.mjs`，通过开发版 WebView2 的本机 CDP 端口运行 Playwright，检查桌面/窄视口截图、实际 Canvas 像元、透明度、缩放和混合图层。只替换文件选择器，GIS 调用保留真实原生链路。该脚本及历史项目副本迁移脚本目前在本机执行；CI 包含单元测试、源码/冻结 RPC 工作流和宿主冒烟，不包含完整原生 UI 自动化。实际结果见当前验证记录。

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

项目入口为 `project.spa`（SQLite 元数据），托管矢量和表格使用真正的 GeoPackage，栅格使用原生 GeoTIFF，缓存与原始数据分开管理。项目 schema 为 v4；打开 v1/v2/v3 项目时先备份再迁移。不要将真实项目、缓存和诊断产物提交到代码仓库。

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
- [Phase 1C 验证记录](docs/verification-phase-1c.md)
- [Phase 1B 验证记录](docs/verification-phase-1b.md)
- [Phase 1A 验证记录](docs/verification-phase-1a.md)
- [Phase 0 历史验证记录](docs/verification.md)
- [阶段待办](TODO.md)

正式发布前仍需在无开发环境的 Windows 10/11 上验收安装、离线依赖和升级行为，并使用真实规划样本核查互操作性。
