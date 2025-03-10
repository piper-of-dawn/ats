use scraper::{Html, Selector};
use std::error::Error;
use cached::proc_macro::cached;
use chrono::{DateTime, Duration, TimeZone, Utc};
use reqwest;
use serde_json::Value;
use reqwest::Client;
use std::{collections::HashMap, sync::Arc};
use tokio::sync::RwLock;
use ats::{get_real_time_price}
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


pub fn get_real_time_price(ticker: &str) -> Result<f64, Box<dyn Error>> {
    // Construct the Google Finance URL
    let url = format!("https://www.google.com/finance/quote/{}", ticker);
    
    // Create a blocking HTTP client with a browser-like user agent
    let client = reqwest::blocking::Client::builder()
        .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
        .build()?;
    
    // Send HTTP GET request and handle potential errors
    let response = client.get(&url).send()?;
    
    // Check for non-successful HTTP status codes
    if !response.status().is_success() {
        return Err(format!("Request failed with status code: {}", response.status()).into());
    }

    // Parse the HTML response
    let body = response.text()?;
    // println!("{}", body);
    let document = Html::parse_document(&body);
    
    // Create selector for the price element (current class as of knowledge cutoff)
    let selector = Selector::parse(".fxKbKc").map_err(|_| "Invalid CSS selector")?;
    // Extract price text from the first matching element
    let price_element = document.select(&selector).next()
    .ok_or("Price element not found in page")?;
    let price_text = price_element.text().collect::<String>();
    // println!("{}", price_text);
    
    // Clean and parse the price value
    let cleaned_price = price_text.replace(',', "").replace('$', "");  // Remove commas, dollar signs, and periods

    let price = cleaned_price.parse::<f64>()?;

    Ok(price)
}



