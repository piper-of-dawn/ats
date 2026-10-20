//! Trading 212 v0 read-only client. Payloads preserve API JSON and decimal precision.
use reqwest::{Client, StatusCode, Url, header::HeaderMap};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::{HashMap, HashSet},
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{
    sync::Mutex,
    time::{Instant, sleep},
};

const MAX_WAIT: Duration = Duration::from_secs(120);
const ATTEMPTS: usize = 3;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Environment {
    Demo,
    Live,
}
impl Environment {
    fn origin(self) -> &'static str {
        match self {
            Self::Demo => "https://demo.trading212.com",
            Self::Live => "https://live.trading212.com",
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum ApiError {
    #[error("set non-empty TRADING212_API_KEY and TRADING212_API_SECRET environment variables")]
    Credentials,
    #[error("unable to initialize HTTP client")]
    Client,
    #[error("request failed after {attempts} attempt(s) (connection, TLS, or timeout error)")]
    Transport { attempts: usize },
    #[error("HTTP {status}: {reason}")]
    Http { status: u16, reason: &'static str },
    #[error("API response is not valid JSON or does not match the expected page structure")]
    Decode,
    #[error(
        "request requires a wait of {seconds} seconds; retry later (maximum automatic wait is 120 seconds)"
    )]
    WaitTooLong { seconds: u64 },
    #[error("history page limit must be between 1 and 50")]
    Limit,
    #[error("this history endpoint does not support that filter")]
    Filter,
    #[error("invalid pagination destination: expected the same origin and history endpoint")]
    Pagination,
    #[error("API repeated a pagination path; stopped to avoid a loop")]
    PaginationLoop,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RateLimit {
    pub limit: Option<u64>,
    pub period: Option<u64>,
    pub remaining: Option<u64>,
    pub reset: Option<u64>,
    pub used: Option<u64>,
}
impl RateLimit {
    fn from_headers(headers: &HeaderMap) -> Self {
        let get = |key| headers.get(key).and_then(|v| v.to_str().ok()?.parse().ok());
        Self {
            limit: get("x-ratelimit-limit"),
            period: get("x-ratelimit-period"),
            remaining: get("x-ratelimit-remaining"),
            reset: get("x-ratelimit-reset"),
            used: get("x-ratelimit-used"),
        }
    }
}

#[derive(Debug)]
pub struct ApiResponse<T> {
    pub data: T,
    pub rate_limit: RateLimit,
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Page<T> {
    pub items: Vec<T>,
    pub next_page_path: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HistoryKind {
    Orders,
    Dividends,
    Transactions,
}
impl HistoryKind {
    fn path(self) -> &'static str {
        match self {
            Self::Orders => "/api/v0/equity/history/orders",
            Self::Dividends => "/api/v0/equity/history/dividends",
            Self::Transactions => "/api/v0/equity/history/transactions",
        }
    }
}

#[derive(Clone, Debug)]
pub struct HistoryOptions {
    pub limit: u8,
    pub ticker: Option<String>,
    /// API ISO 8601 starting time filter, supported only for transactions.
    pub time: Option<String>,
}
impl Default for HistoryOptions {
    fn default() -> Self {
        Self {
            limit: 50,
            ticker: None,
            time: None,
        }
    }
}

// Credentials deliberately have no Debug implementation and never enter URLs/errors.
struct Inner {
    http: Client,
    origin: Url,
    key: String,
    secret: String,
    next: Mutex<HashMap<String, Instant>>,
    #[cfg(test)]
    fast_tests: bool,
}

#[derive(Clone)]
pub struct Trading212Client {
    inner: Arc<Inner>,
}
impl Trading212Client {
    pub fn new(environment: Environment, key: String, secret: String) -> Result<Self, ApiError> {
        if key.trim().is_empty() || secret.trim().is_empty() || key.contains(':') {
            return Err(ApiError::Credentials);
        }
        let http = Client::builder()
            .connect_timeout(Duration::from_secs(10))
            .timeout(Duration::from_secs(30))
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .user_agent(concat!("ats-rust/", env!("CARGO_PKG_VERSION")))
            .build()
            .map_err(|_| ApiError::Client)?;
        Ok(Self {
            inner: Arc::new(Inner {
                http,
                origin: Url::parse(environment.origin()).map_err(|_| ApiError::Client)?,
                key,
                secret,
                next: Mutex::new(HashMap::new()),
                #[cfg(test)]
                fast_tests: false,
            }),
        })
    }

    pub fn from_env(environment: Environment) -> Result<Self, ApiError> {
        Self::new(
            environment,
            std::env::var("TRADING212_API_KEY").map_err(|_| ApiError::Credentials)?,
            std::env::var("TRADING212_API_SECRET").map_err(|_| ApiError::Credentials)?,
        )
    }

    pub async fn summary(&self) -> Result<ApiResponse<Value>, ApiError> {
        self.get(self.url("/api/v0/equity/account/summary")?).await
    }
    pub async fn positions(&self, ticker: Option<&str>) -> Result<ApiResponse<Value>, ApiError> {
        let mut url = self.url("/api/v0/equity/positions")?;
        if let Some(ticker) = ticker {
            url.query_pairs_mut().append_pair("ticker", ticker);
        }
        self.get(url).await
    }
    pub async fn instruments(&self) -> Result<ApiResponse<Value>, ApiError> {
        self.get(self.url("/api/v0/equity/metadata/instruments")?)
            .await
    }
    pub async fn orders(
        &self,
        options: HistoryOptions,
    ) -> Result<ApiResponse<Page<Value>>, ApiError> {
        self.history_page(HistoryKind::Orders, options).await
    }
    pub async fn dividends(
        &self,
        options: HistoryOptions,
    ) -> Result<ApiResponse<Page<Value>>, ApiError> {
        self.history_page(HistoryKind::Dividends, options).await
    }
    pub async fn transactions(
        &self,
        options: HistoryOptions,
    ) -> Result<ApiResponse<Page<Value>>, ApiError> {
        self.history_page(HistoryKind::Transactions, options).await
    }
    async fn history_page(
        &self,
        kind: HistoryKind,
        options: HistoryOptions,
    ) -> Result<ApiResponse<Page<Value>>, ApiError> {
        self.get(self.history_url(kind, &options)?).await
    }
    /// Stateful pagination, with same-endpoint validation and loop detection.
    pub fn history(
        &self,
        kind: HistoryKind,
        options: HistoryOptions,
    ) -> Result<HistoryPager, ApiError> {
        let url = self.history_url(kind, &options)?;
        Ok(HistoryPager {
            client: self.clone(),
            kind,
            next: Some(url),
            seen: HashSet::new(),
        })
    }
    fn url(&self, path: &str) -> Result<Url, ApiError> {
        self.inner
            .origin
            .join(path)
            .map_err(|_| ApiError::Pagination)
    }
    fn history_url(&self, kind: HistoryKind, options: &HistoryOptions) -> Result<Url, ApiError> {
        if !(1..=50).contains(&options.limit) {
            return Err(ApiError::Limit);
        }
        if (kind == HistoryKind::Transactions && options.ticker.is_some())
            || (kind != HistoryKind::Transactions && options.time.is_some())
        {
            return Err(ApiError::Filter);
        }
        let mut url = self.url(kind.path())?;
        url.query_pairs_mut()
            .append_pair("limit", &options.limit.to_string());
        if let Some(ticker) = &options.ticker {
            url.query_pairs_mut().append_pair("ticker", ticker);
        }
        if let Some(time) = &options.time {
            url.query_pairs_mut().append_pair("time", time);
        }
        Ok(url)
    }
    fn pagination_url(&self, kind: HistoryKind, path: &str) -> Result<Url, ApiError> {
        let url = self.url(path)?;
        if url.origin() != self.inner.origin.origin()
            || url.path() != kind.path()
            || !url.username().is_empty()
            || url.password().is_some()
            || url.fragment().is_some()
        {
            return Err(ApiError::Pagination);
        }
        Ok(url)
    }
    fn interval(&self, path: &str) -> Duration {
        #[cfg(test)]
        if self.inner.fast_tests {
            return Duration::ZERO;
        }
        Duration::from_secs(match path {
            "/api/v0/equity/account/summary" => 5,
            "/api/v0/equity/positions" => 1,
            "/api/v0/equity/metadata/instruments" => 50,
            _ => 10,
        })
    }
    async fn get<T: serde::de::DeserializeOwned>(
        &self,
        url: Url,
    ) -> Result<ApiResponse<T>, ApiError> {
        // Holding this lock also serializes clones, including their retries.
        let mut schedule = self.inner.next.lock().await;
        let path = url.path().to_owned();
        for attempt in 1..=ATTEMPTS {
            if let Some(next) = schedule.get(&path) {
                let delay = next.saturating_duration_since(Instant::now());
                check_wait(delay)?;
                sleep(delay).await;
            }
            schedule.insert(path.clone(), Instant::now() + self.interval(&path));
            let response = self
                .inner
                .http
                .get(url.clone())
                .basic_auth(&self.inner.key, Some(&self.inner.secret))
                .send()
                .await;
            let response = match response {
                Ok(response) => response,
                Err(_) => {
                    if attempt == ATTEMPTS {
                        return Err(ApiError::Transport { attempts: attempt });
                    }
                    extend_schedule(&mut schedule, &path, backoff(attempt));
                    continue;
                }
            };
            let status = response.status();
            let rate_limit = RateLimit::from_headers(response.headers());
            let header_delay = retry_delay(response.headers());
            if let (Some(period), Some(limit)) = (rate_limit.period, rate_limit.limit)
                && limit > 0
            {
                extend_schedule(
                    &mut schedule,
                    &path,
                    Duration::from_secs(period.div_ceil(limit)),
                );
            }
            if rate_limit.remaining == Some(0) {
                if let Some(reset) = rate_limit.reset {
                    extend_schedule(&mut schedule, &path, until_epoch(reset));
                } else if let Some(period) = rate_limit.period {
                    extend_schedule(&mut schedule, &path, Duration::from_secs(period));
                }
            }
            if status.is_success() {
                let body = match response.bytes().await {
                    Ok(body) => body,
                    Err(_) if attempt < ATTEMPTS => {
                        extend_schedule(&mut schedule, &path, backoff(attempt));
                        continue;
                    }
                    Err(_) => return Err(ApiError::Transport { attempts: attempt }),
                };
                return serde_json::from_slice(&body)
                    .map(|data| ApiResponse { data, rate_limit })
                    .map_err(|_| ApiError::Decode);
            }
            let retryable = status == StatusCode::REQUEST_TIMEOUT
                || status == StatusCode::TOO_MANY_REQUESTS
                || status.is_server_error();
            if retryable {
                extend_schedule(
                    &mut schedule,
                    &path,
                    header_delay.unwrap_or_default().max(backoff(attempt)),
                );
                // On a 429, a reset timestamp applies even if 'remaining' was absent.
                if status == StatusCode::TOO_MANY_REQUESTS
                    && let Some(reset) = rate_limit.reset
                {
                    extend_schedule(&mut schedule, &path, until_epoch(reset));
                }
                if attempt < ATTEMPTS {
                    continue;
                }
            }
            return Err(ApiError::Http {
                status: status.as_u16(),
                reason: match status.as_u16() {
                    401 => "authentication failed; check key/secret and selected environment",
                    403 => "access denied; check key permissions and IP restrictions",
                    429 => "rate limit exhausted after retries; retry later",
                    300..=399 => "unexpected redirect (redirects are disabled)",
                    _ => "API request failed",
                },
            });
        }
        unreachable!("each final attempt returns")
    }
}

pub struct HistoryPager {
    client: Trading212Client,
    kind: HistoryKind,
    next: Option<Url>,
    seen: HashSet<String>,
}
impl HistoryPager {
    /// Returns one page at a time. None indicates the end of the history.
    pub async fn next_page(&mut self) -> Result<Option<ApiResponse<Page<Value>>>, ApiError> {
        let Some(url) = self.next.clone() else {
            return Ok(None);
        };
        if self.seen.contains(url.as_str()) {
            return Err(ApiError::PaginationLoop);
        }
        let response: ApiResponse<Page<Value>> = self.client.get(url.clone()).await?;
        let next = response
            .data
            .next_page_path
            .as_deref()
            .map(|path| self.client.pagination_url(self.kind, path))
            .transpose()?;
        if next
            .as_ref()
            .is_some_and(|next| next == &url || self.seen.contains(next.as_str()))
        {
            return Err(ApiError::PaginationLoop);
        }
        self.seen.insert(url.to_string());
        self.next = next;
        Ok(Some(response))
    }
}

fn check_wait(delay: Duration) -> Result<(), ApiError> {
    if delay > MAX_WAIT {
        return Err(ApiError::WaitTooLong {
            seconds: delay.as_secs().saturating_add(1),
        });
    }
    Ok(())
}
fn extend_schedule(schedule: &mut HashMap<String, Instant>, path: &str, delay: Duration) {
    // Preserve the server's deadline even when it exceeds the automatic wait cap.
    // An unrepresentable duration still blocks requests rather than overflowing.
    let now = Instant::now();
    let next = now
        .checked_add(delay)
        .unwrap_or_else(|| now + Duration::from_secs(u32::MAX.into()));
    schedule
        .entry(path.to_owned())
        .and_modify(|old| *old = (*old).max(next))
        .or_insert(next);
}
fn until_epoch(epoch: u64) -> Duration {
    Duration::from_secs(epoch).saturating_sub(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default(),
    )
}
fn retry_delay(headers: &HeaderMap) -> Option<Duration> {
    let value = headers.get("retry-after")?.to_str().ok()?;
    value
        .parse::<u64>()
        .ok()
        .map(Duration::from_secs)
        .or_else(|| {
            httpdate::parse_http_date(value)
                .ok()
                .map(|time| time.duration_since(SystemTime::now()).unwrap_or_default())
        })
}
fn backoff(attempt: usize) -> Duration {
    Duration::from_secs(1 << (attempt - 1))
}

#[cfg(test)]
mod tests {
    use super::*;
    use wiremock::{
        Mock, MockServer, ResponseTemplate,
        matchers::{header, method, path, query_param},
    };

    fn client(server: &MockServer) -> Trading212Client {
        let mut client =
            Trading212Client::new(Environment::Demo, "key".into(), "secret".into()).unwrap();
        let inner = Arc::get_mut(&mut client.inner).unwrap();
        inner.origin = Url::parse(&server.uri()).unwrap();
        inner.fast_tests = true;
        client
    }

    #[tokio::test]
    async fn basic_auth_json_precision_and_rate_metadata() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/api/v0/equity/account/summary"))
            .and(header("authorization", "Basic a2V5OnNlY3JldA=="))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("x-ratelimit-limit", "1")
                    .insert_header("x-ratelimit-period", "5")
                    .insert_header("x-ratelimit-remaining", "0")
                    .insert_header("x-ratelimit-used", "1")
                    .set_body_raw(
                        r#"{"totalValue":1234567890.12345678901234567890,"extra":true}"#,
                        "application/json",
                    ),
            )
            .expect(1)
            .mount(&server)
            .await;
        let result = client(&server).summary().await.unwrap();
        assert_eq!(
            result.data["totalValue"].to_string(),
            "1234567890.12345678901234567890"
        );
        assert_eq!(result.rate_limit.limit, Some(1));
        assert_eq!(result.rate_limit.used, Some(1));
    }

    #[tokio::test]
    async fn positions_encodes_ticker_and_instruments_uses_metadata() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path("/api/v0/equity/positions"))
            .and(query_param("ticker", "A&B_US_EQ"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!([])))
            .expect(1)
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(path("/api/v0/equity/metadata/instruments"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!([])))
            .expect(1)
            .mount(&server)
            .await;
        let client = client(&server);
        assert_eq!(
            client.positions(Some("A&B_US_EQ")).await.unwrap().data,
            serde_json::json!([])
        );
        client.instruments().await.unwrap();
    }

    #[tokio::test]
    async fn history_follows_root_relative_path_and_terminates() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(path(HistoryKind::Orders.path()))
            .and(query_param("limit", "2"))
            .and(query_param("ticker", "AAPL_US_EQ"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "items": [{"id": 1}], "nextPagePath": "/api/v0/equity/history/orders?cursor=next"
            })))
            .expect(1)
            .mount(&server)
            .await;
        Mock::given(method("GET"))
            .and(path(HistoryKind::Orders.path()))
            .and(query_param("cursor", "next"))
            .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                "items": [], "nextPagePath": null
            })))
            .expect(1)
            .mount(&server)
            .await;
        let mut pager = client(&server)
            .history(
                HistoryKind::Orders,
                HistoryOptions {
                    limit: 2,
                    ticker: Some("AAPL_US_EQ".into()),
                    time: None,
                },
            )
            .unwrap();
        assert_eq!(
            pager.next_page().await.unwrap().unwrap().data.items.len(),
            1
        );
        assert!(
            pager
                .next_page()
                .await
                .unwrap()
                .unwrap()
                .data
                .items
                .is_empty()
        );
        assert!(pager.next_page().await.unwrap().is_none());
    }

    #[tokio::test]
    async fn history_filters_and_named_methods() {
        let server = MockServer::start().await;
        for kind in [
            HistoryKind::Orders,
            HistoryKind::Dividends,
            HistoryKind::Transactions,
        ] {
            let matcher = Mock::given(method("GET"))
                .and(path(kind.path()))
                .and(query_param("limit", "50"));
            let matcher = if kind == HistoryKind::Transactions {
                matcher.and(query_param("time", "2026-01-01T00:00:00+01:00"))
            } else {
                matcher.and(query_param("ticker", "AAPL_US_EQ"))
            };
            matcher
                .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                    "items": [], "nextPagePath": null
                })))
                .expect(1)
                .mount(&server)
                .await;
        }
        let client = client(&server);
        let options = HistoryOptions {
            ticker: Some("AAPL_US_EQ".into()),
            ..Default::default()
        };
        client.orders(options.clone()).await.unwrap();
        client.dividends(options).await.unwrap();
        client
            .transactions(HistoryOptions {
                time: Some("2026-01-01T00:00:00+01:00".into()),
                ..Default::default()
            })
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn rejects_foreign_pagination_and_loops_without_another_request() {
        for next in [
            "https://evil.example/api/v0/equity/history/orders",
            "/api/v0/equity/history/orders?limit=50",
        ] {
            let server = MockServer::start().await;
            Mock::given(method("GET"))
                .and(path(HistoryKind::Orders.path()))
                .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
                    "items": [], "nextPagePath": next
                })))
                .expect(1)
                .mount(&server)
                .await;
            let mut pager = client(&server)
                .history(HistoryKind::Orders, HistoryOptions::default())
                .unwrap();
            let error = pager.next_page().await.unwrap_err();
            if next.starts_with("https") {
                assert!(matches!(error, ApiError::Pagination));
            } else {
                assert!(matches!(error, ApiError::PaginationLoop));
            }
        }
    }

    #[tokio::test]
    async fn permissions_and_non_json_errors_are_redacted_and_not_retried() {
        for status in [400, 401, 403] {
            let server = MockServer::start().await;
            Mock::given(method("GET"))
                .respond_with(
                    ResponseTemplate::new(status)
                        .set_body_string("key secret Basic a2V5OnNlY3JldA=="),
                )
                .expect(1)
                .mount(&server)
                .await;
            let error = client(&server).summary().await.unwrap_err();
            assert!(matches!(error, ApiError::Http { status: s, .. } if s == status));
            let text = format!("{error:?} {error}");
            assert!(!text.contains("a2V5OnNlY3JldA=="));
            assert!(!text.contains("key secret"));
        }
    }

    #[tokio::test]
    async fn invalid_success_json_does_not_echo_payload() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .respond_with(ResponseTemplate::new(200).set_body_string("secret-value"))
            .expect(1)
            .mount(&server)
            .await;
        assert!(matches!(
            client(&server).summary().await,
            Err(ApiError::Decode)
        ));
    }

    #[tokio::test]
    async fn redirects_are_not_followed() {
        let server = MockServer::start().await;
        let target = MockServer::start().await;
        Mock::given(method("GET"))
            .respond_with(ResponseTemplate::new(302).insert_header("location", target.uri()))
            .expect(1)
            .mount(&server)
            .await;
        assert!(matches!(
            client(&server).summary().await,
            Err(ApiError::Http { status: 302, .. })
        ));
        assert!(target.received_requests().await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn retryable_statuses_stop_at_three_attempts() {
        for status in [408, 429, 503] {
            let server = MockServer::start().await;
            Mock::given(method("GET"))
                .respond_with(ResponseTemplate::new(status).insert_header("retry-after", "0"))
                .expect(3)
                .mount(&server)
                .await;
            assert!(
                matches!(client(&server).summary().await, Err(ApiError::Http { status: s, .. }) if s == status)
            );
        }
    }

    #[tokio::test]
    async fn retry_can_recover() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let server = MockServer::start().await;
        let calls = AtomicUsize::new(0);
        Mock::given(method("GET"))
            .respond_with(move |_: &wiremock::Request| {
                if calls.fetch_add(1, Ordering::SeqCst) == 0 {
                    ResponseTemplate::new(503)
                } else {
                    ResponseTemplate::new(200).set_body_json(serde_json::json!({"ok": true}))
                }
            })
            .expect(2)
            .mount(&server)
            .await;
        assert_eq!(client(&server).summary().await.unwrap().data["ok"], true);
    }

    #[tokio::test]
    async fn long_reset_or_retry_after_fails_without_sleeping_or_retrying() {
        for header in ["retry-after", "x-ratelimit-reset"] {
            let server = MockServer::start().await;
            let value = if header == "retry-after" {
                300
            } else {
                SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap()
                    .as_secs()
                    + 300
            };
            Mock::given(method("GET"))
                .respond_with(ResponseTemplate::new(429).insert_header(header, value.to_string()))
                .expect(1)
                .mount(&server)
                .await;
            assert!(
                matches!(client(&server).summary().await, Err(ApiError::WaitTooLong { seconds }) if seconds >= 299)
            );
        }
    }

    #[tokio::test]
    async fn clones_share_pacing_and_server_limits_extend_it() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .respond_with(
                ResponseTemplate::new(200)
                    .insert_header("x-ratelimit-remaining", "0")
                    .insert_header("x-ratelimit-period", "300")
                    .set_body_json(serde_json::json!({})),
            )
            .expect(1)
            .mount(&server)
            .await;
        let client = client(&server);
        client.summary().await.unwrap();
        assert!(matches!(
            client.clone().summary().await,
            Err(ApiError::WaitTooLong { .. })
        ));
    }

    #[tokio::test]
    async fn validates_configuration_and_endpoint_boundaries() {
        let server = MockServer::start().await;
        let client = client(&server);
        assert!(matches!(
            Trading212Client::new(Environment::Demo, "".into(), "s".into()),
            Err(ApiError::Credentials)
        ));
        assert!(matches!(
            client
                .orders(HistoryOptions {
                    limit: 0,
                    ..Default::default()
                })
                .await,
            Err(ApiError::Limit)
        ));
        assert!(matches!(
            client
                .transactions(HistoryOptions {
                    ticker: Some("x".into()),
                    ..Default::default()
                })
                .await,
            Err(ApiError::Filter)
        ));
        for path in [
            "//evil.example/api/v0/equity/history/orders",
            "/api/v0/equity/history/dividends",
            "/api/v0/equity/history/orders#fragment",
            "http://key:secret@localhost/api/v0/equity/history/orders",
        ] {
            assert!(matches!(
                client.pagination_url(HistoryKind::Orders, path),
                Err(ApiError::Pagination)
            ));
        }
        assert!(server.received_requests().await.unwrap().is_empty());
        let production = Trading212Client::new(Environment::Demo, "k".into(), "s".into()).unwrap();
        assert_eq!(
            production.interval("/api/v0/equity/account/summary"),
            Duration::from_secs(5)
        );
        assert_eq!(
            production.interval("/api/v0/equity/positions"),
            Duration::from_secs(1)
        );
        assert_eq!(
            production.interval("/api/v0/equity/metadata/instruments"),
            Duration::from_secs(50)
        );
        assert_eq!(
            production.interval(HistoryKind::Orders.path()),
            Duration::from_secs(10)
        );
    }

    #[test]
    fn parses_retry_date_and_ignores_malformed_rate_headers() {
        let mut headers = HeaderMap::new();
        headers.insert(
            "retry-after",
            httpdate::fmt_http_date(SystemTime::now() + Duration::from_secs(60))
                .parse()
                .unwrap(),
        );
        assert!(retry_delay(&headers).unwrap() > Duration::from_secs(58));
        headers.insert("x-ratelimit-limit", "unknown".parse().unwrap());
        assert_eq!(RateLimit::from_headers(&headers).limit, None);
        headers.insert("retry-after", "unknown".parse().unwrap());
        assert!(retry_delay(&headers).is_none());
    }
    #[tokio::test]
    async fn transport_timeouts_are_bounded() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .respond_with(
                ResponseTemplate::new(200)
                    .set_delay(Duration::from_millis(100))
                    .set_body_json(serde_json::json!({})),
            )
            .expect(3)
            .mount(&server)
            .await;
        let mut client = client(&server);
        Arc::get_mut(&mut client.inner).unwrap().http = Client::builder()
            .timeout(Duration::from_millis(30))
            .retry(reqwest::retry::never())
            .build()
            .unwrap();
        assert!(matches!(
            client.summary().await,
            Err(ApiError::Transport { attempts: 3 })
        ));
    }

    #[tokio::test]
    async fn concurrent_clones_observe_documented_pacing() {
        let server = MockServer::start().await;
        let times = Arc::new(std::sync::Mutex::new(Vec::new()));
        let record = times.clone();
        Mock::given(method("GET"))
            .respond_with(move |_: &wiremock::Request| {
                record.lock().unwrap().push(Instant::now());
                ResponseTemplate::new(200).set_body_json(serde_json::json!([]))
            })
            .expect(2)
            .mount(&server)
            .await;
        let mut client = client(&server);
        Arc::get_mut(&mut client.inner).unwrap().fast_tests = false;
        let clone = client.clone();
        let (first, second) = tokio::join!(client.positions(None), clone.positions(None));
        first.unwrap();
        second.unwrap();
        let times = times.lock().unwrap();
        assert!(times[1].duration_since(times[0]) >= Duration::from_millis(950));
    }
}
