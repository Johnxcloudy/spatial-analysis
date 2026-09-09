# Phase 0 架构

React 调用受限的 Tauri engine_request 命令。Rust 通过一个持久 Python 子进程的 UTF-8 JSON Lines 管道发送 JSON-RPC 2.0 请求，Python 完成项目存储和真实 GIS 诊断。接口见 shared/protocol.md，前端类型见 shared/contracts.ts。

## 进程职责

- React：项目编辑、原生文件选择、未保存保护、运行状态和诊断预览。纯浏览器明确显示原生桥接不可用。
- Tauri：原生命令白名单、子进程启动、请求序列化、响应 ID 验证、超时、大小上限和桌面日志。
- Python：参数验证、项目锁、SQLite 事务、GIS 库调用、诊断产物和引擎日志。
- Windows Job Object：宿主退出后终止引擎及其后代，避免孤立进程继续持有项目。

Phase 0 的小样诊断在引擎进程内串行执行，桌面等待是异步的。正式耗时分析的 worker 调度、取消和 Task 历史属于后续实现；不能将当前 90 秒 RPC 超时当成通用分析任务系统。

## 通信和恢复

请求上限 1 MiB，响应上限 8 MiB；超过限制、管道中断、错误的 RPC envelope 或超时会返回明确错误。协议/进程异常后停止引擎，后续调用可重新启动，但原项目需要重新打开。领域校验失败不应终止正常引擎。

引擎 stdout 只承载协议；stderr 被宿主接入桌面日志，Python 另写轮转日志。分析结果落文件，接口返回引用。诊断预览只有两个小样面，不构成大数据传输方案。

## 开发与发布

开发时使用 gis-engine/.venv，允许 SPATIAL_ENGINE_PYTHON 覆盖解释器；发布时从 resource_dir/engine 启动 PyInstaller onedir 引擎，携带所需原生库和资源。清除继承的 Python/GDAL/PROJ 路径配置，关闭 PROJ 的在线格网下载。

Windows 安装器由 NSIS 生成，按当前用户安装并包含 WebView2 离线安装方案。代码仓库只保存构建输入和依赖锁，不保存引擎二进制和安装器。

## 地图边界

OpenLayers 仅渲染引擎生成的合成验证结果。正式数据导入、任意项目投影显示、按视窗读取、样式系统和属性表不属于 Phase 0。投影往返小样只能验证数值路径，不能证明真实基准转换或测绘精度。
