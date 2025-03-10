mod ticker;
// use ats::{get_latest_spx, get_moving_avg};
use chrono::Utc;
use discord_notify::DiscordBot;
use rand::Rng;
use ticker::Ticker;
use tokio::time::{Duration, sleep};

async fn temp_price_tracker(ticker: &str, threshold: f64, bot: DiscordBot) -> () {
    let ticker = Ticker::new(&ticker);
    // Fetch the latest SPX price
    let latest_price: f64 = match ticker.get_latest_price().await {
        Ok(latest_price) => latest_price,
        Err(e) => panic!("Error fetching SPX value: {}", e),
    };
    if latest_price < threshold {
        if let Err(e) = bot
            .send_notification(&format!(
                "Latest Price {:.2} for {} below threshold",
                latest_price, ticker.symbol
            ))
            .await
        {
            eprintln!("Failed to send notification: {}", e);
        };
    }
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().collect();
    
    let ticker = args.get(1)
        .ok_or("Please provide a ticker symbol (e.g. AAPL:NASDAQ)")?;

    let price = get_real_time_price(ticker)?;
    println!("Current {} price: ${:.2}", ticker, price);
    Ok(())
}

// #[tokio::main]
// async fn main() -> Result<(), Box<dyn std::error::Error>> {
//     let channel_id = "1334619980037099600";
//     let identifier = "My Discord Bot";
//     let bot = discord_notify::DiscordBot::new(identifier, channel_id);
//     loop {
//         let mut rng = rand::rng();
//         let sleep_duration = rng.random_range(10..=30);

//         temp_price_tracker("PLTR", 82.0, bot.clone()).await;
//         temp_price_tracker("UAL", 93.0, bot.clone()).await;
//         temp_price_tracker("NVDA", 122.0, bot.clone()).await;
//         temp_price_tracker("WMT", 96.0, bot.clone()).await;
//         temp_price_tracker("EQT", 47.0, bot.clone()).await;
//         temp_price_tracker("RL", 269.0, bot.clone()).await;
//         println!("EVENT COMPLETED!");
//         // Generate a random duration between 30 and 60 seconds
//         // let mut rng = rand::rng();
//         // let sleep_duration = rng.random_range(30..=60);
//         // let ticker = Ticker::new("%5ESPX");
//         // // Fetch the latest SPX price
//         // let latest_price = match ticker.get_latest_price().await {
//         //     Ok(latest_price) => latest_price,
//         //     Err(e) => panic!("Error fetching SPX value: {}", e),
//         // };

//         // let end_date = Utc::now(); // or any specific DateTime<Utc>
//         // let offset_days = 120;
//         // let mean = ticker.get_moving_avg(end_date, offset_days).await?;

//         // // Send notifications
//         // if let Err(e) = bot
//         //     .send_notification(&format!("100 Day Moving Average {:.2}", mean))
//         //     .await
//         // {
//         //     eprintln!("Failed to send notification: {}", e);
//         // };
//         // if let Err(e) = bot
//         //     .send_notification(&format!("Latest Price {:.2}", latest_price))
//         //     .await
//         // {
//         //     eprintln!("Failed to send notification: {}", e);
//         // };
//         println!("Sleeping for {} seconds", sleep_duration);
//         sleep(Duration::from_secs(sleep_duration)).await;
//         // // Sleep for the random duration
//     }
// }
