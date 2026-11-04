//! Ledger service.
use axum::{routing::{get, post}, Router};

const METRICS_PATH: &str = "/metrics";

#[tokio::main]
async fn main() {
    let app = Router::new()
        .route("/v1/entries", get(list_entries).post(create_entry))
        .route(
            "/v1/entries/{id}",
            get(get_entry),
        )
        .route(METRICS_PATH, get(metrics))
        .nest("/admin", admin::routes());
    let listener = tokio::net::TcpListener::bind("0.0.0.0:3000").await.unwrap();
    axum::serve(listener, app).with_graceful_shutdown(shutdown_signal()).await.unwrap();
}

async fn shutdown_signal() {
    tokio::signal::ctrl_c().await.unwrap();
}

#[get("/v1/legacy")]
async fn legacy() -> &'static str { "old" }
