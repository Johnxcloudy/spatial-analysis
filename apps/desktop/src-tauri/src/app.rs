use crate::{
    engine::{log_message, EngineManager},
    protocol::EngineError,
};
use serde_json::{json, Value};
use std::path::PathBuf;
use tauri::{AppHandle, Manager, State};

async fn wait_for_task(
    app: &AppHandle,
    engine: &EngineManager,
    path: &Value,
    mut task: Value,
) -> Result<Value, EngineError> {
    for _ in 0..600 {
        if task["status"] != "running" {
            return if task["status"] == "completed" {
                Ok(task)
            } else {
                Err(EngineError::local("SMOKE_TASK_FAILED", task.to_string()))
            };
        }
        tokio::time::sleep(std::time::Duration::from_millis(200)).await;
        task = engine
            .request(app, "task.get", json!({"path":path,"taskId":task["id"]}))
            .await?;
    }
    Err(EngineError::local("SMOKE_TASK_TIMEOUT", task.to_string()))
}

#[tauri::command]
async fn engine_request(
    app: AppHandle,
    engine: State<'_, EngineManager>,
    method: String,
    params: Value,
) -> Result<Value, EngineError> {
    engine.request(&app, &method, params).await
}

async fn smoke_test(app: &AppHandle, directory: &std::path::Path) -> Result<Value, EngineError> {
    let engine = app.state::<EngineManager>();
    let runtime = engine.request(app, "runtime.info", json!({})).await?;
    let project_directory = directory.join("项目 smoke test");
    let created = engine
        .request(
            app,
            "project.create",
            json!({"directory":project_directory,"name":"Phase 1A 本地验证"}),
        )
        .await?;
    let path = created["projectPath"].clone();
    let saved = engine.request(app, "project.save", json!({
        "path":path,"name":"Phase 1A 本地验证","description":"Native bridge saved successfully",
        "analysisCrs":"EPSG:4547","displayCrs":created["displayCrs"],"viewState":created["viewState"]
    })).await?;
    engine.request(app, "project.close", json!({})).await?;
    let reopened = engine
        .request(app, "project.open", json!({"path":path}))
        .await?;
    if reopened["id"] != created["id"] || reopened["description"] != saved["description"] {
        return Err(EngineError::local(
            "SMOKE_PROJECT_MISMATCH",
            "Project round trip mismatch",
        ));
    }
    let report = engine
        .request(
            app,
            "diagnostics.run",
            json!({"directory":directory.join("diagnostics")}),
        )
        .await?;
    if report["ok"] != true {
        return Err(EngineError::local(
            "SMOKE_DIAGNOSTICS_FAILED",
            report.to_string(),
        ));
    }
    let source = engine
        .request(
            app,
            "source.inspect",
            json!({"sourcePath":report["geopackagePath"],"encoding":null}),
        )
        .await?;
    let import_task = engine
        .request(
            app,
            "vector.import",
            json!({
                "path":path,"sourcePath":report["geopackagePath"],"sourceLayer":"source",
                "encoding":null,"assignedCrs":null
            }),
        )
        .await?;
    let imported = wait_for_task(app, &engine, &path, import_task).await?;
    let workspace = engine
        .request(app, "workspace.get", json!({"path":path}))
        .await?;
    let dataset = &workspace["datasets"][0];
    if workspace["datasets"].as_array().map(Vec::len) != Some(1)
        || dataset["id"] != imported["datasetId"]
        || dataset["featureCount"] != 1
    {
        return Err(EngineError::local(
            "SMOKE_IMPORT_MISMATCH",
            workspace.to_string(),
        ));
    }
    let attributes = engine
        .request(
            app,
            "vector.page",
            json!({
                "path":path,"datasetId":dataset["id"],"offset":0,"limit":200,
                "sortField":null,"descending":false,"filter":null
            }),
        )
        .await?;
    let viewport = engine
        .request(
            app,
            "vector.viewport",
            json!({
                "path":path,"datasetId":dataset["id"],"bbox":dataset["boundsWgs84"],
                "limit":2000,"propertyFields":[]
            }),
        )
        .await?;
    if attributes["total"] != 1 || viewport["returnedCount"] != 1 {
        return Err(EngineError::local(
            "SMOKE_QUERY_MISMATCH",
            json!({"attributes":attributes,"viewport":viewport}).to_string(),
        ));
    }
    let export_task = engine
        .request(
            app,
            "vector.export",
            json!({
                "path":path,"datasetId":dataset["id"],"destination":directory.join("export.gpkg")
            }),
        )
        .await?;
    let exported = wait_for_task(app, &engine, &path, export_task).await?;
    engine.request(app, "project.close", json!({})).await?;
    engine
        .request(app, "project.open", json!({"path":path}))
        .await?;
    let restored = engine
        .request(app, "workspace.get", json!({"path":path}))
        .await?;
    engine.request(app, "project.close", json!({})).await?;
    if restored["datasets"] != workspace["datasets"] || restored["layers"] != workspace["layers"] {
        return Err(EngineError::local(
            "SMOKE_WORKSPACE_MISMATCH",
            restored.to_string(),
        ));
    }
    Ok(
        json!({"ok":true,"runtime":runtime,"created":created,"reopened":reopened,"diagnostics":report,
            "source":source,"workspace":restored,"attributes":attributes,"viewport":viewport,"exported":exported}),
    )
}

pub fn run() {
    let args: Vec<String> = std::env::args().collect();
    let smoke_directory = args
        .iter()
        .position(|arg| arg == "--smoke-test")
        .and_then(|index| args.get(index + 1))
        .map(PathBuf::from);
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(EngineManager::default())
        .invoke_handler(tauri::generate_handler![engine_request])
        .setup(move |app| {
            log_message(app.handle(), "Desktop started");
            if let Some(directory) = smoke_directory.clone() {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.hide();
                }
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    let result = smoke_test(&handle, &directory).await;
                    let success = result.is_ok();
                    let output = match result {
                        Ok(report) => report,
                        Err(error) => json!({"ok":false,"error":error}),
                    };
                    let recorded = serde_json::to_vec_pretty(&output)
                        .map_err(std::io::Error::other)
                        .and_then(|bytes| {
                            std::fs::create_dir_all(&directory)?;
                            std::fs::write(directory.join("native-smoke.json"), bytes)
                        });
                    if let Err(error) = &recorded {
                        log_message(
                            &handle,
                            &format!("Could not save native smoke report: {error}"),
                        );
                    }
                    log_message(&handle, &format!("Native smoke test success={success}"));
                    handle.exit(if success && recorded.is_ok() { 0 } else { 1 });
                });
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Failed to build Spatial Analysis Desktop");
    app.run(|app, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            app.state::<EngineManager>().shutdown();
        }
    });
}
