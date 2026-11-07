use anyhow::Result;
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use isla_server::{
    app::{router, Platform},
    paths::Paths,
};
use serde_json::Value;
use std::path::PathBuf;
use tower::ServiceExt;

#[tokio::test]
async fn native_api_preserves_profiles_and_policies_and_rejects_bad_commands() -> Result<()> {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()?;
    let temp = tempfile::tempdir()?;
    let paths = Paths {
        root: root.clone(),
        robots: root.join("robots"),
        runs: temp.path().join("runs"),
        policies: temp.path().join("policies"),
        settings: temp.path().join("settings"),
        cache: temp.path().join("cache"),
        ui: temp.path().join("ui"),
    };
    let platform = Platform::new(paths, Some("luma_1"))?;
    let app = router(platform.clone());
    let response = app
        .clone()
        .oneshot(Request::builder().uri("/api/status").body(Body::empty())?)
        .await?;
    assert_eq!(response.status(), StatusCode::OK);
    let value: Value = serde_json::from_slice(&response.into_body().collect().await?.to_bytes())?;
    assert_eq!(value["runtime"], "rust");
    assert_eq!(value["robot"], "luma_1");
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/sim/policy")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"run":"luma1-walk","use_design":false}"#))?,
        )
        .await?;
    assert_eq!(response.status(), StatusCode::OK);
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/hardware")
                .header("host", "localhost:8848")
                .header("origin", "https://elsewhere.example")
                .body(Body::empty())?,
        )
        .await?;
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let response = app
        .clone()
        .oneshot(Request::builder().uri("/api/missing").body(Body::empty())?)
        .await?;
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/runs")
                .body(Body::from(r#"{"name":"new"}"#))?,
        )
        .await?;
    assert_eq!(response.status(), StatusCode::NOT_IMPLEMENTED);
    let response = app
        .oneshot(
            Request::builder()
                .method("PUT")
                .uri("/api/robots/upload/abcdefgh12/model.exe")
                .body(Body::from("x"))?,
        )
        .await?;
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    platform.close();
    Ok(())
}
