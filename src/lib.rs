use cached::proc_macro::cached;
use chrono::{DateTime, Duration, TimeZone, Utc};
use reqwest;
use serde_json::Value;

use reqwest::Client;
use std::{collections::HashMap, sync::Arc};
use tokio::sync::RwLock;

#[derive(Debug)]
pub struct Cache {
    map: RwLock<HashMap<String, Value>>,
}

impl Cache {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            map: RwLock::new(HashMap::new()),
        })
    }

    pub async fn fetch_json(self: Arc<Self>, url: &str) -> Result<Value, reqwest::Error> {
        {
            let cache = self.map.read().await;
            if let Some(cached) = cache.get(url) {
                return Ok(cached.clone());
            }
        }

        let client = Client::new();
        let response = client
            .get(url)
            .header(reqwest::header::USER_AGENT, "reqwest/0.11")
            .send()
            .await?;
        let json: Value = response.json().await?;

        let mut cache = self.map.write().await;
        cache.insert(url.to_string(), json.clone());

        Ok(json)
    }
}

// pub async fn get_historical_spx(
//     ticker: &str,
//     end_date: DateTime<Utc>,
//     offset_days: i64,
// ) -> Result<Vec<f64>, Box<dyn std::error::Error>> {
//     // Calculate start date from end_date and offset
//     let start_date = end_date - Duration::days(offset_days);

//     // Convert dates to timestamps
//     let period1 = start_date.timestamp();
//     let period2 = end_date.timestamp();

//     // Build URL with date parameters
//     let url = format!(
//         "https://query1.finance.yahoo.com/v8/finance/chart/{}?period1={}&period2={}&interval=1d",
//         ticker, period1, period2
//     );
//     let json: Value = fetch_json(&url).await?;

//     // Parse and print timestamp
//     let dt = Utc
//         .timestamp_opt(
//             json["chart"]["result"][0]["meta"]["regularMarketTime"]
//                 .as_i64()
//                 .ok_or("Failed to parse timestamp")?,
//             0,
//         )
//         .unwrap()
//         .format("%Y-%m-%d %H:%M:%S")
//         .to_string();
//     // println!("Data timestamp: {}", dt);

//     // Extract closing price
//     let price = json["chart"]["result"][0]["indicators"]["quote"][0]["close"]
//         .as_array()
//         .ok_or("Failed to parse price vector from JSON response")?;

//     // println!("{:#?}", price);
//     let price = price
//         .iter()
//         .map(|p| p.as_f64().ok_or("Failed to parse price from JSON response"))
//         .collect::<Result<Vec<f64>, _>>();
//     Ok(price?)
// }

// pub async fn get_latest_spx(ticker: &str) -> Result<f64, Box<dyn std::error::Error>> {
//     let url = format!(
//         "https://query1.finance.yahoo.com/v8/finance/chart/{}",
//         ticker
//     );
//     let json: Value = fetch_json(&url).await?;
//     let dt = Utc
//         .timestamp_opt(
//             json["chart"]["result"][0]["meta"]["regularMarketTime"]
//                 .as_i64()
//                 .ok_or("Failed to parse timestamp")?,
//             0,
//         )
//         .unwrap()
//         .format("%Y-%m-%d %H:%M:%S")
//         .to_string();
//     // println!("{:#?}", dt);
//     // Navigate through the JSON structure to find the price
//     // println!("{:#?}", json);
//     let price = json["chart"]["result"][0]["meta"]["regularMarketPrice"]
//         .as_f64()
//         .ok_or("Failed to parse price from JSON response")?;

//     Ok(price)
// }

// pub async fn get_moving_avg(
//     ticker: &str,
//     end_date: DateTime<Utc>,
//     offset_days: i64,
// ) -> Result<f64, Box<dyn std::error::Error>> {
//     let price = get_historical_spx(ticker, end_date, offset_days).await?;
//     let price = &price[price.len().saturating_sub(100)..];
//     let mean = price.iter().sum::<f64>() / price.len() as f64;
//     Ok(mean)
// }
