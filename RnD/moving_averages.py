import numpy as np

def calculate_ema(prices, period):
    """Calculate Exponential Moving Average (EMA)"""
    if period < 1 or len(prices) < period:
        return np.full_like(prices, np.nan)
    
    alpha = 2.0 / (period + 1.0)
    ema = np.full(len(prices), np.nan)
    start_idx = period - 1
    
    # Initial SMA valueS
    ema[start_idx] = np.mean(prices[:period])
    
    # Calculate subsequent EMA values
    for i in range(start_idx + 1, len(prices)):
        ema[i] = alpha * prices[i] + (1 - alpha) * ema[i - 1]
    
    return ema

def exponential_hull_ma(close, period, lambda_factor=0.2):
    """
    Calculate Exponential Hull Moving Average (EHMA) with Price Correction
    
    Parameters:
    close (np.array): Array of closing prices
    period (int): Lookback period
    lambda_factor (float): Price correction factor (0-1)
    
    Returns:
    np.array: EHMA values
    """
    period_half = max(1, int(period / 2))
    smoothing_period = max(1, int(np.sqrt(period)))
    
    # Calculate base EMAs
    ema_half = calculate_ema(close, period_half)
    ema_full = calculate_ema(close, period)
    
    # Calculate raw EHMA
    raw_ehma = 2 * ema_half - ema_full
    
    # Smooth with EMA
    smoothed_ehma = calculate_ema(raw_ehma, smoothing_period)
    
    # Initialize corrected EHMA
    corrected_ehma = np.full_like(close, np.nan)
    valid_mask = ~np.isnan(smoothed_ehma)
    
    if not np.any(valid_mask):
        return corrected_ehma
    
    # Find first valid index
    first_valid = np.where(valid_mask)[0][0]
    corrected_ehma[first_valid] = smoothed_ehma[first_valid]
    
    # Apply price correction
    for i in range(first_valid + 1, len(close)):
        if np.isnan(smoothed_ehma[i]):
            continue
        price_deviation = close[i] - corrected_ehma[i - 1]
        corrected_ehma[i] = smoothed_ehma[i] + (lambda_factor * price_deviation)
    
    return corrected_ehma