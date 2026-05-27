from ats.ticker import EquityTicker


def process_ticker(item):
    ticker, mkt_index = item["ticker"], item["representative_index_ticker"]
    market_error = item.get("market_error")
    try:
        if market_error:
            raise ValueError(market_error)

        market_price_data = item.get("market_price_data")
        if market_price_data is None:
            mkt = (
                EquityTicker(mkt_index)
                .fetch_price_data()
                .make_log_returns()
                .winsorize_log_returns()
            )
        else:
            mkt = EquityTicker(mkt_index)
            mkt.price_data = market_price_data
            mkt._require_data("market index")

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
