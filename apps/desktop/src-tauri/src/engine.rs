use crate::{
    job::ProcessJob,
    protocol::{
        decode_response, validate_request, EngineError, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,
    },
};
use serde_json::{json, Value};
use std::{
    io,
    path::PathBuf,
    process::Stdio,
    sync::atomic::{AtomicU64, Ordering},
    time::Duration,
};
use tauri::{AppHandle, Manager};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
    sync::Mutex,
};

struct Session {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    _job: ProcessJob,
}

#[derive(Default)]
pub struct EngineManager {
    session: Mutex<Option<Session>>,
    next_id: AtomicU64,
}

pub fn log_message(app: &AppHandle, message: &str) {
    use std::io::Write;
    if let Ok(directory) = app.path().app_log_dir() {
        let _ = std::fs::create_dir_all(&directory);
        let path = directory.join("desktop.log");
        if std::fs::metadata(&path)
            .map(|m| m.len() > 5 * 1024 * 1024)
            .unwrap_or(false)
        {
            let _ = std::fs::rename(&path, directory.join("desktop.previous.log"));
        }
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(path)
        {
            let _ = writeln!(file, "{:?} {message}", std::time::SystemTime::now());
        }
    }
}

impl Session {
    fn start(app: &AppHandle) -> Result<Self, EngineError> {
        let mut command;
        if cfg!(debug_assertions) {
            let engine_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../gis-engine");
            let default_python = engine_root.join(if cfg!(windows) {
                ".venv/Scripts/python.exe"
            } else {
                ".venv/bin/python"
            });
            let python = std::env::var_os("SPATIAL_ENGINE_PYTHON")
                .map(PathBuf::from)
                .unwrap_or(default_python);
            if !python.is_file() {
                return Err(EngineError::local(
                    "ENGINE_NOT_INSTALLED",
                    "找不到开发用 Python 引擎，请先运行 uv sync --project gis-engine。",
                ));
            }
            command = Command::new(python);
            command
                .args(["-u", "-m", "spatial_engine"])
                .current_dir(&engine_root);
            clean_environment(&mut command);
            command.env("PYTHONPATH", engine_root.join("src"));
        } else {
            let resources = app
                .path()
                .resource_dir()
                .map_err(|e| EngineError::local("RESOURCE_DIRECTORY", e.to_string()))?;
            let executable = resources.join("engine").join(if cfg!(windows) {
                "spatial-engine.exe"
            } else {
                "spatial-engine"
            });
            if !executable.is_file() {
                return Err(EngineError::local(
                    "ENGINE_NOT_INSTALLED",
                    "安装目录中缺少 GIS 引擎，请重新安装完整程序。",
                ));
            }
            command = Command::new(executable);
            clean_environment(&mut command);
        }
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        let mut child = command.spawn().map_err(|e| {
            EngineError::local("ENGINE_START_FAILED", format!("无法启动 GIS 引擎：{e}"))
        })?;
        let pid = child
            .id()
            .ok_or_else(|| EngineError::local("ENGINE_START_FAILED", "GIS 引擎提前退出。"))?;
        let job = ProcessJob::new(pid).map_err(|e| {
            EngineError::local(
                "ENGINE_PROCESS_LIFETIME",
                format!("无法管理引擎进程生命周期：{e}"),
            )
        })?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| EngineError::local("ENGINE_PIPE", "无法打开引擎输入管道。"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| EngineError::local("ENGINE_PIPE", "无法打开引擎输出管道。"))?;
        if let Some(stderr) = child.stderr.take() {
            let app = app.clone();
            tauri::async_runtime::spawn(async move {
                let mut reader = BufReader::new(stderr);
                while let Ok(line) = read_frame(&mut reader, 64 * 1024).await {
                    log_message(&app, &String::from_utf8_lossy(&line));
                }
            });
        }
        log_message(app, &format!("GIS engine started, pid={pid}"));
        Ok(Self {
            child,
            stdin,
            stdout: BufReader::new(stdout),
            _job: job,
        })
    }
}

fn clean_environment(command: &mut Command) {
    for key in [
        "PYTHONHOME",
        "PYTHONPATH",
        "GDAL_DATA",
        "PROJ_LIB",
        "PROJ_DATA",
    ] {
        command.env_remove(key);
    }
    command
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PROJ_NETWORK", "OFF");
}

async fn read_frame<R: tokio::io::AsyncBufRead + Unpin>(
    reader: &mut R,
    limit: usize,
) -> io::Result<Vec<u8>> {
    let mut frame = Vec::new();
    loop {
        let buffer = reader.fill_buf().await?;
        if buffer.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "engine stream closed",
            ));
        }
        let newline = buffer.iter().position(|byte| *byte == b'\n');
        let count = newline.map(|position| position + 1).unwrap_or(buffer.len());
        if frame.len() + count > limit {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "engine response exceeds size limit",
            ));
        }
        frame.extend_from_slice(&buffer[..count]);
        reader.consume(count);
        if newline.is_some() {
            return Ok(frame);
        }
    }
}

impl EngineManager {
    pub async fn request(
        &self,
        app: &AppHandle,
        method: &str,
        params: Value,
    ) -> Result<Value, EngineError> {
        validate_request(method, &params)?;
        let id = self.next_id.fetch_add(1, Ordering::Relaxed) + 1;
        let mut message =
            serde_json::to_vec(&json!({"jsonrpc":"2.0","id":id,"method":method,"params":params}))
                .map_err(|e| EngineError::local("REQUEST_ENCODING", e.to_string()))?;
        if message.len() > MAX_REQUEST_BYTES {
            return Err(EngineError::local(
                "REQUEST_TOO_LARGE",
                "请求内容超过大小限制。",
            ));
        }
        message.push(b'\n');
        let mut slot = self.session.lock().await;
        if slot.is_none() {
            *slot = Some(Session::start(app)?);
        }
        let session = slot.as_mut().expect("session initialized");
        let response = tokio::time::timeout(Duration::from_secs(90), async {
            session.stdin.write_all(&message).await?;
            session.stdin.flush().await?;
            read_frame(&mut session.stdout, MAX_RESPONSE_BYTES).await
        })
        .await;
        let parsed = match response {
            Ok(Ok(bytes)) => serde_json::from_slice::<Value>(&bytes).map_err(|e| {
                EngineError::local("ENGINE_PROTOCOL", format!("引擎响应格式错误：{e}"))
            }),
            Ok(Err(error)) => Err(EngineError::local(
                "ENGINE_DISCONNECTED",
                format!("GIS 引擎连接已中断，请重试并重新打开项目。{error}"),
            )),
            Err(_) => Err(EngineError::local(
                "ENGINE_TIMEOUT",
                "GIS 引擎超过 90 秒未响应，已停止该进程。请重试并重新打开项目。",
            )),
        };
        match parsed {
            Ok(payload) => {
                let result = decode_response(payload, id);
                if result
                    .as_ref()
                    .err()
                    .map(|e| e.data["kind"] == "ENGINE_PROTOCOL")
                    .unwrap_or(false)
                {
                    let _ = session.child.kill().await;
                    *slot = None;
                }
                result
            }
            Err(error) => {
                log_message(app, &format!("{}: {}", error.data["kind"], error.message));
                let _ = session.child.kill().await;
                *slot = None;
                Err(error)
            }
        }
    }

    pub fn shutdown(&self) {
        if let Ok(mut slot) = self.session.try_lock() {
            if let Some(mut session) = slot.take() {
                let _ = session.child.start_kill();
            }
        }
    }
}
