// use ats::fetch_json;
use ats::Cache;
use chrono::{DateTime, Duration, TimeZone, Utc};
use serde_json::Value;
use std::{collections::HashMap, sync::Arc};
use tokio::sync::RwLock;

#[derive(Debug)]
pub struct Ticker {
    pub symbol: String,
    cache: Arc<Cache>,
    // cache: Arc<RwLock<HashMap<String, Value>>>,
}

impl Ticker {
    pub fn new(symbol: &str) -> Self {
        Ticker {
            symbol: symbol.to_string(),
            cache: Cache::new(),
            // cache: Arc::new(RwLock::new(HashMap::new())),
        }
    }
}

impl Ticker {
    pub async fn get_latest_price(&self) -> Result<f64, Box<dyn std::error::Error>> {
        let ticker = &self.symbol;
        let url = format!(
            "https://query1.finance.yahoo.com/v8/finance/chart/{}",
            ticker
        );
        let json: Value = self.cache.clone().fetch_json(&url).await?;
        let dt = Utc
            .timestamp_opt(
                json["chart"]["result"][0]["meta"]["regularMarketTime"]
                    .as_i64()
                    .ok_or("Failed to parse timestamp")?,
                0,
            )
            .unwrap()
            .format("%Y-%m-%d %H:%M:%S")
            .to_string();
        // println!("{:#?}", dt);
        // Navigate through the JSON structure to find the price
        // println!("{:#?}", json);
        let price = json["chart"]["result"][0]["meta"]["regularMarketPrice"]
            .as_f64()
            .ok_or("Failed to parse price from JSON response")?;

        Ok(price)
    }
}

impl Ticker {
    pub async fn get_historical_price(
        &self,
        end_date: DateTime<Utc>,
        offset_days: i64,
    ) -> Result<Vec<f64>, Box<dyn std::error::Error>> {
        let ticker = self.symbol.clone();
        // Calculate start date from end_date and offset
        let start_date = end_date - Duration::days(offset_days);

        // Convert dates to timestamps
        let period1 = start_date.timestamp();
        let period2 = end_date.timestamp();

        // Build URL with date parameters
        let url = format!(
            "https://query1.finance.yahoo.com/v8/finance/chart/{}?period1={}&period2={}&interval=1d",
            ticker, period1, period2
        );
        let json: Value = self.cache.clone().fetch_json(&url).await?;

        // Parse and print timestamp
        let dt = Utc
            .timestamp_opt(
                json["chart"]["result"][0]["meta"]["regularMarketTime"]
                    .as_i64()
                    .ok_or("Failed to parse timestamp")?,
                0,
            )
            .unwrap()
            .format("%Y-%m-%d %H:%M:%S")
            .to_string();
        // println!("Data timestamp: {}", dt);

        // Extract closing price
        let price = json["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            .as_array()
            .ok_or("Failed to parse price vector from JSON response")?;

        // println!("{:#?}", price);
        let price = price
            .iter()
            .map(|p| p.as_f64().ok_or("Failed to parse price from JSON response"))
            .collect::<Result<Vec<f64>, _>>();
        Ok(price?)
    }
}

impl Ticker {
    pub async fn get_moving_avg(
        &self,
        end_date: DateTime<Utc>,
        offset_days: i64,
    ) -> Result<f64, Box<dyn std::error::Error>> {
        let price = self.get_historical_price(end_date, offset_days).await?;
        let price = &price[price.len().saturating_sub(100)..];
        let mean = price.iter().sum::<f64>() / price.len() as f64;
        Ok(mean)
    }
}
