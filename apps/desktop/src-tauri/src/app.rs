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
    if runtime["protocolVersion"] != 4 || runtime["engineVersion"] != "0.4.0" {
        return Err(EngineError::local(
            "SMOKE_VERSION_MISMATCH",
            runtime.to_string(),
        ));
    }
    let project_directory = directory.join("项目 smoke test");
    let created = engine
        .request(
            app,
            "project.create",
            json!({"directory":project_directory,"name":"Phase 1C 本地验证"}),
        )
        .await?;
    let path = created["projectPath"].clone();
    let saved = engine.request(app, "project.save", json!({
        "path":path,"name":"Phase 1C 本地验证","description":"Native bridge saved successfully",
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
    let table_source = directory.join("coordinates.csv");
    {
        use std::io::Write;
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&table_source)
            .map_err(|error| EngineError::local("SMOKE_SOURCE_FAILED", error.to_string()))?;
        file.write_all(b"code,x,y\n001,114.0,27.1\n002,bad,27.2\n003,114.2,27.3\n")
            .map_err(|error| EngineError::local("SMOKE_SOURCE_FAILED", error.to_string()))?;
    }
    let table_source_info = engine.request(app, "table.inspect", json!({
        "sourcePath":table_source,"encoding":"utf-8","delimiter":",","sheet":null,"headerRow":1
    })).await?;
    let table_task = engine.request(app, "table.import", json!({
        "path":path,"sourcePath":table_source,"encoding":"utf-8","delimiter":",","sheet":null,"headerRow":1
    })).await?;
    let table_task = wait_for_task(app, &engine, &path, table_task).await?;
    let table_page = engine
        .request(
            app,
            "table.page",
            json!({
                "path":path,"datasetId":table_task["datasetId"],"offset":0,"limit":200,
                "sortField":"code","descending":false,"filter":null
            }),
        )
        .await?;
    let table_workspace = engine
        .request(app, "workspace.get", json!({"path":path}))
        .await?;
    if table_page["total"] != 3
        || table_page["rows"][0]["values"]["code"] != "001"
        || table_workspace["layers"].as_array().map(Vec::len) != Some(1)
    {
        return Err(EngineError::local(
            "SMOKE_TABLE_MISMATCH",
            table_page.to_string(),
        ));
    }
    let point_task = engine.request(app, "table.points", json!({
        "path":path,"datasetId":table_task["datasetId"],"xField":"x","yField":"y","declaredCrs":"EPSG:4326"
    })).await?;
    let point_task = wait_for_task(app, &engine, &path, point_task).await?;
    let point_page = engine
        .request(
            app,
            "vector.page",
            json!({
                "path":path,"datasetId":point_task["datasetId"],"offset":0,"limit":200,
                "sortField":"code","descending":false,"filter":null
            }),
        )
        .await?;
    let point_view = engine
        .request(
            app,
            "vector.viewport",
            json!({
                "path":path,"datasetId":point_task["datasetId"],"bbox":[113.0,26.0,115.0,28.0],
                "limit":2000,"propertyFields":["code"]
            }),
        )
        .await?;
    if point_page["total"] != 3 || point_view["returnedCount"] != 2 {
        return Err(EngineError::local(
            "SMOKE_POINTS_MISMATCH",
            point_page.to_string(),
        ));
    }
    let table_export = engine.request(app, "table.export", json!({
        "path":path,"datasetId":table_task["datasetId"],"destination":directory.join("table-export.gpkg")
    })).await?;
    let table_export = wait_for_task(app, &engine, &path, table_export).await?;
    let raster_source = engine
        .request(
            app,
            "raster.inspect",
            json!({"sourcePath":report["geotiffPath"]}),
        )
        .await?;
    let raster_source_path = report["geotiffPath"]
        .as_str()
        .ok_or_else(|| EngineError::local("SMOKE_RASTER_SOURCE", report.to_string()))?;
    let raster_source_bytes = std::fs::read(raster_source_path)
        .map_err(|error| EngineError::local("SMOKE_RASTER_SOURCE", error.to_string()))?;
    let raster_task = engine
        .request(
            app,
            "raster.import",
            json!({"path":path,"sourcePath":report["geotiffPath"]}),
        )
        .await?;
    let raster_task = wait_for_task(app, &engine, &path, raster_task).await?;
    let raster_bounds = &raster_source["boundsWgs84"];
    let bound = |index: usize| {
        raster_bounds[index]
            .as_f64()
            .ok_or_else(|| EngineError::local("SMOKE_RASTER_BOUNDS", raster_source.to_string()))
    };
    let longitude = bound(0)? + (bound(2)? - bound(0)?) * 0.25;
    let latitude = bound(3)? - (bound(3)? - bound(1)?) * 0.25;
    let raster_sample = engine.request(app, "raster.sample", json!({"path":path,"datasetId":raster_task["datasetId"],"coordinate":[longitude,latitude]})).await?;
    let mercator_x = 6378137.0 * longitude.to_radians();
    let mercator_y = 6378137.0
        * (std::f64::consts::FRAC_PI_4 + latitude.to_radians() / 2.0)
            .tan()
            .ln();
    let raster_image = engine.request(app, "raster.render", json!({
        "path":path,"datasetId":raster_task["datasetId"],"bbox":[mercator_x-200.0,mercator_y-200.0,mercator_x+200.0,mercator_y+200.0],
        "width":128,"height":128,"style":{"mode":"gray","bands":[1],"ranges":[[1,4]],"resampling":"nearest"}
    })).await?;
    if raster_source["raster"]["width"] != 2
        || raster_source["raster"]["height"] != 2
        || raster_sample["inside"] != true
        || raster_sample["pixel"]["row"] != 0
        || raster_sample["pixel"]["column"] != 0
        || raster_sample["bands"][0]["rawValue"] != "1"
        || raster_sample["bands"][0]["value"] != 1.0
        || raster_sample["bands"][0]["valid"] != true
        || raster_image["mimeType"] != "image/png"
        || raster_image["width"] != 128
        || !raster_image["imageBase64"]
            .as_str()
            .is_some_and(|value| value.starts_with("iVBORw0KGgo"))
    {
        return Err(EngineError::local(
            "SMOKE_RASTER_MISMATCH",
            json!({"source":raster_source,"sample":raster_sample}).to_string(),
        ));
    }
    let raster_destination = directory.join("raster-export.tif");
    let raster_export = engine.request(app, "raster.export", json!({"path":path,"datasetId":raster_task["datasetId"],"destination":raster_destination})).await?;
    let raster_export = wait_for_task(app, &engine, &path, raster_export).await?;
    let raster_reread = engine
        .request(
            app,
            "raster.inspect",
            json!({"sourcePath":raster_destination}),
        )
        .await?;
    let raster_export_bytes = std::fs::read(&raster_destination)
        .map_err(|error| EngineError::local("SMOKE_RASTER_EXPORT", error.to_string()))?;
    if raster_reread["raster"] != raster_source["raster"]
        || raster_reread["crsWkt"] != raster_source["crsWkt"]
        || raster_export_bytes != raster_source_bytes
    {
        return Err(EngineError::local(
            "SMOKE_RASTER_EXPORT_MISMATCH",
            raster_reread.to_string(),
        ));
    }
    let workspace = engine
        .request(app, "workspace.get", json!({"path":path}))
        .await?;
    if workspace["datasets"].as_array().map(Vec::len) != Some(4)
        || workspace["layers"].as_array().map(Vec::len) != Some(3)
    {
        return Err(EngineError::local(
            "SMOKE_DATASETS_MISMATCH",
            workspace.to_string(),
        ));
    }
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
            "source":source,"workspace":restored,"attributes":attributes,"viewport":viewport,"exported":exported,
            "tableSource":table_source_info,"tablePage":table_page,"pointPage":point_page,
            "pointViewport":point_view,"tableExport":table_export,"rasterSource":raster_source,
            "rasterSample":raster_sample,"rasterRender":{"width":raster_image["width"],"height":raster_image["height"],"mimeType":raster_image["mimeType"]},
            "rasterExport":raster_export}),
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
