# Spatial Analysis Desktop

面向土地利用、用地叠加与面积统计的 Windows 本地 GIS 工作站。当前版本为 **0.2.0 / Phase 1A：普通矢量数据与工作区**，交付范围和验收状态见 [Phase 1A 验证记录](docs/verification-phase-1a.md)。

## 当前范围

- 导入 GeoPackage、Shapefile、GeoJSON 和 File Geodatabase 的普通二维要素，选择源数据层与字符编码。
- 保留原始输入，在项目内生成独立 GeoPackage 快照，记录来源、字段、完整 CRS、版本与导入检查报告。
- 地图缩放、图层显示与顺序、透明度、单一符号和分类颜色；属性分页、排序、筛选与地图选择联动。
- 导出新的 GeoPackage；项目创建、保存、关闭、重开及旧版项目迁移备份。
- 导入/导出任务进度与取消，日志、运行环境信息和独立 GIS 诊断。

**尚不提供正式叠加、裁剪、分类面积或占比计算。** 诊断中的已知面积矩形、相交和投影往返仅用于核对引擎，不是项目分析结果，也不证明真实数据的测绘精度。

## 使用

1. 在桌面窗口中“新建项目”，选择父目录并输入项目名称；程序创建新的项目目录，不覆盖已有项目。打开其中的 `project.spa` 可恢复工作区。
2. 点击“导入数据”。普通文件选择 `.gpkg`、`.shp` 或 `.geojson`；GDB 选择整个 `.gdb` 目录。检查并选择源数据层；Shapefile 的组成文件应放在同一目录，中文编码可指定 GBK 等选项。
3. 核对来源 CRS。缺失 CRS 时可明确声明；不声明则仅查看属性，不能显示到地图。已有 CRS 不能在导入时覆盖。显示固定使用 EPSG:3857，来源 CRS 和项目分析 CRS 分开保存。
4. 在图层侧栏调整显示与样式，在属性表排序、筛选或选择记录。“数据与检查报告”显示已完成检查、警告和未检查项。无效或空几何会保留并受限，不自动修复；不能无损保留的 Z/M 或曲线几何会被拒绝。
5. 图层名称、顺序、显示和样式立即保存；项目名称、描述、分析 CRS 和地图视图需点击“保存项目”。刷新工作区不会替换这些未保存的编辑。移除图层仍保留托管数据快照。
6. “导出当前数据”写出新的 GeoPackage，保留快照属性、几何和 CRS，目标文件必须不存在。重开项目读取托管快照，原始来源被移动后仍可查看已导入数据。

每个项目同时执行一个导入或导出任务；只显示真实阶段和可用计数。关闭或切换项目会结束活动任务，中断任务不自动恢复。引擎连接故障后需重新打开项目，未保存的表单内容会暂时保留。

GDB 支持范围是普通要素类；报告中标记“未读取”的高级元数据不应视为已保全。空项目的分析 CRS 默认未设置，填写分析 CRS 本身不会启动分析。

## 当前限制

以下是 [Phase 1A](docs/phase-1a.md) 的保护上限，不是大数据性能承诺：

| 项目 | 上限 |
| --- | --- |
| 单次导入 | 100,000 个要素、2,000,000 个顶点、256 个字段 |
| 数据源检查 | 512 个源数据层 |
| 每个项目 | 64 个托管数据集 |
| 元数据 | 每个数据集 256 KiB；工作区响应 6 MiB |
| 属性查询 | 默认每页 200 条，最多 500 条 |
| 地图查询 | 每次最多 2,000 个要素、100,000 个显示顶点 |
| 单次属性或地图查询 | 序列化响应最多 2 MiB |

超限会返回错误或明确的截断提示。地图上的部分显示不代表数据集已完整显示，显示限制不改变托管快照。地图选中的要素若不在当前属性页，会在表格上方显示该记录。

待后续独立验收：Phase 1B 的 CSV/XLSX 坐标表、Phase 1C 的 GeoTIFF 基础显示、Phase 1D 的项目另存/来源重新定位与跨格式回归；正式土地叠加和面积统计属于 Phase 2。

## 安装与开发

Windows x64 安装器使用 NSIS，包含 Python/GDAL 引擎和 WebView2 离线安装组件；安装版无需另外安装 Python。安装包尚未签名，独立 Windows 10/11、离线环境和真实规划数据验收的进度见 [当前验证记录](docs/verification-phase-1a.md)。

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

实际测试、原生工作流、安装包和校验和记录见 [Phase 1A 验证记录](docs/verification-phase-1a.md)。历史 [Phase 0 验证记录](docs/verification.md) 保留，不用于替代当前版本验收。

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

桌面可执行文件支持 `--smoke-test <输出目录>`，用于验证宿主调用、项目保存重开和 GIS 诊断；此模式生成 native-smoke.json 后退出。

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

项目入口为 `project.spa`（SQLite 元数据），托管矢量数据使用真正的 GeoPackage，缓存与原始数据分开管理。项目 schema 为 v2；打开 v1 项目时先备份再迁移。不要将真实项目、缓存和诊断产物提交到代码仓库。

## 文档

- [优化方案](docs/plan-v2.md)
- [GIS 数据可靠性规范](docs/gis-data-standard.md)
- [当前架构](docs/architecture.md)
- [项目数据模型](docs/data-model.md)
- [Phase 1A 实施范围](docs/phase-1a.md)
- [Phase 1A 验证记录](docs/verification-phase-1a.md)
- [Phase 0 历史验证记录](docs/verification.md)
- [阶段待办](TODO.md)

正式发布前仍需在无开发环境的 Windows 10/11 上验收安装、离线依赖和升级行为，并使用真实规划样本核查互操作性。
