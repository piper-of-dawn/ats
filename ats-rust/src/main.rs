use ats_rust::trading212::{Environment, HistoryKind, HistoryOptions, Trading212Client};
use clap::{Args, Parser, Subcommand, ValueEnum};
use std::io::{self, Write};

#[derive(Parser)]
#[command(version, about = "ATS read-only API tools")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    Trading212(Trading212Args),
}
#[derive(Args)]
struct Trading212Args {
    #[arg(long, value_enum, default_value = "demo", global = true)]
    environment: Env,
    #[command(subcommand)]
    command: ApiCommand,
}
#[derive(Clone, Copy, ValueEnum)]
enum Env {
    Demo,
    Live,
}
#[derive(Subcommand)]
enum ApiCommand {
    Summary,
    Positions {
        #[arg(long)]
        ticker: Option<String>,
    },
    Instruments,
    History {
        #[command(subcommand)]
        command: HistoryCommand,
    },
}
#[derive(Subcommand)]
enum HistoryCommand {
    Orders(TickerHistory),
    Dividends(TickerHistory),
    Transactions(TransactionHistory),
}
#[derive(Args)]
struct Paging {
    #[arg(long, default_value_t = 50, value_parser = clap::value_parser!(u8).range(1..=50))]
    limit: u8,
    /// Fetch all pages, emitting one JSON page per line (NDJSON).
    #[arg(long)]
    all: bool,
}
#[derive(Args)]
struct TickerHistory {
    #[command(flatten)]
    paging: Paging,
    #[arg(long)]
    ticker: Option<String>,
}
#[derive(Args)]
struct TransactionHistory {
    #[command(flatten)]
    paging: Paging,
    /// Starting time in RFC 3339 / ISO 8601 format, including timezone.
    #[arg(long, value_parser = parse_time)]
    time: Option<String>,
}
fn parse_time(value: &str) -> Result<String, String> {
    chrono::DateTime::parse_from_rfc3339(value)
        .map(|_| value.to_owned())
        .map_err(|_| "expected RFC 3339 time, e.g. 2026-01-01T00:00:00Z".into())
}
fn emit(value: &impl serde::Serialize) -> Result<(), Box<dyn std::error::Error>> {
    let stdout = io::stdout();
    let mut out = stdout.lock();
    write_json(&mut out, value)
}
fn write_json(
    out: &mut impl Write,
    value: &impl serde::Serialize,
) -> Result<(), Box<dyn std::error::Error>> {
    serde_json::to_writer(&mut *out, value)?;
    writeln!(out)?;
    out.flush()?;
    Ok(())
}
#[tokio::main]
async fn main() -> std::process::ExitCode {
    let cli = Cli::parse();
    match run(cli).await {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error}");
            std::process::ExitCode::FAILURE
        }
    }
}
async fn run(cli: Cli) -> Result<(), Box<dyn std::error::Error>> {
    let Command::Trading212(args) = cli.command;
    let client = Trading212Client::from_env(match args.environment {
        Env::Demo => Environment::Demo,
        Env::Live => Environment::Live,
    })?;
    match args.command {
        ApiCommand::Summary => emit(&client.summary().await?.data)?,
        ApiCommand::Positions { ticker } => emit(&client.positions(ticker.as_deref()).await?.data)?,
        ApiCommand::Instruments => emit(&client.instruments().await?.data)?,
        ApiCommand::History { command } => {
            let (kind, paging, ticker, time) = match command {
                HistoryCommand::Orders(args) => {
                    (HistoryKind::Orders, args.paging, args.ticker, None)
                }
                HistoryCommand::Dividends(args) => {
                    (HistoryKind::Dividends, args.paging, args.ticker, None)
                }
                HistoryCommand::Transactions(args) => {
                    (HistoryKind::Transactions, args.paging, None, args.time)
                }
            };
            let mut pager = client.history(
                kind,
                HistoryOptions {
                    limit: paging.limit,
                    ticker,
                    time,
                },
            )?;
            while let Some(response) = pager.next_page().await? {
                emit(&response.data)?;
                if !paging.all {
                    break;
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn output_is_json_only_and_pages_are_separate_lines() {
        let mut output = Vec::new();
        let page = serde_json::json!({"items": [{"id": 1}], "nextPagePath": null});
        write_json(&mut output, &page).unwrap();
        write_json(&mut output, &page).unwrap();
        let text = String::from_utf8(output).unwrap();
        assert_eq!(text.lines().count(), 2);
        for line in text.lines() {
            assert_eq!(
                serde_json::from_str::<serde_json::Value>(line).unwrap(),
                page
            );
        }
    }
    #[test]
    fn demo_is_default_and_live_requires_explicit_selection() {
        let Command::Trading212(args) = Cli::try_parse_from(["ats-rust", "trading212", "summary"])
            .unwrap()
            .command;
        assert!(matches!(args.environment, Env::Demo));
        let Command::Trading212(args) =
            Cli::try_parse_from(["ats-rust", "trading212", "--environment", "live", "summary"])
                .unwrap()
                .command;
        assert!(matches!(args.environment, Env::Live));
    }
}
