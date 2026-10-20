//! The original FRED experiment, retained separately from the Trading 212 CLI.
use chrono::{DateTime, Utc};
use reqwest::Client;
use serde_json::Value;

pub struct Observations {
    pub identifier: String,
    pub time: DateTime<Utc>,
    pub value: f64,
}

pub async fn get_data_from_fred(
    client: &Client,
    series: &str,
    api_key: &str,
) -> Result<Value, reqwest::Error> {
    client
        .get("https://api.stlouisfed.org/fred/series/observations")
        .query(&[
            ("series_id", series),
            ("api_key", api_key),
            ("file_type", "json"),
            ("observation_start", "1976-01-01"),
        ])
        .send()
        .await
        .map_err(reqwest::Error::without_url)?
        .error_for_status()
        .map_err(reqwest::Error::without_url)?
        .json()
        .await
        .map_err(reqwest::Error::without_url)
}
