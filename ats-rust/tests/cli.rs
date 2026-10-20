use std::process::{Command, Output};
fn cli(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_ats-rust"))
        .args(args)
        .env_remove("TRADING212_API_KEY")
        .env_remove("TRADING212_API_SECRET")
        .output()
        .unwrap()
}
#[test]
fn help_needs_no_credentials() {
    for args in [
        vec!["--help"],
        vec!["trading212", "--help"],
        vec!["trading212", "history", "orders", "--help"],
    ] {
        let output = cli(&args);
        assert!(output.status.success());
        assert!(output.stderr.is_empty());
        assert!(String::from_utf8_lossy(&output.stdout).contains("Usage:"));
    }
}
#[test]
fn credentials_and_cli_errors_leave_stdout_empty() {
    for args in [
        vec!["trading212", "summary"],
        vec!["trading212", "--environment", "production", "summary"],
        vec!["trading212", "history", "orders", "--limit", "0"],
        vec!["trading212", "history", "orders", "--limit", "51"],
        vec![
            "trading212",
            "history",
            "transactions",
            "--time",
            "yesterday",
        ],
        vec!["trading212", "history", "transactions", "--ticker", "AAPL"],
    ] {
        let output = cli(&args);
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
        assert!(!output.stderr.is_empty());
    }
}
