from ats.ticker import EquityTicker


def process_ticker(item):
    ticker, mkt_index = item["ticker"], item["representative_index_ticker"]
    try:
        mkt = EquityTicker(mkt_index).fetch_price_data().make_log_returns().winsorize_log_returns()
        eq = (
            EquityTicker(ticker, mkt)
            .fetch_price_data()
            .make_log_returns()
            .winsorize_log_returns()
            .join_with_market_index()
            .compute_beta()
            .get_idiosyncratic_returns()
            .get_long_term_momentum_signal()
            .get_short_term_momentum_signal()
        )
    except Exception as e:
        return {
            "ticker": ticker,
            "mkt_index": mkt_index,
            "stm": None,
            "ltm": None,
            "beta": None,
            "status": f"{ticker} error={e}",
        }
    return {
        "ticker": ticker,
        "mkt_index": mkt_index,
        "stm": eq.stm,
        "ltm": eq.ltm,
        "beta": eq.beta,
        "status": f"{ticker} ok",
    }
