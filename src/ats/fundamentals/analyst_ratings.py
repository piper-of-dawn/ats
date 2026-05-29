from math import sqrt

import numpy as np


RATINGS = {"strongBuy": 2, "buy": 1, "hold": 0, "sell": -1, "strongSell": -2}


def weights(data, lam=0.8):
    raw_weights = [lam**index for index in range(len(data))]
    return np.array(raw_weights) / np.sum(raw_weights)


def mu_t(period):
    sample_size = np.sum([period[rating] for rating in RATINGS])
    return np.sum([RATINGS[rating] * period[rating] for rating in RATINGS]) / sample_size


def C_t(period):
    sample_size = np.sum([period[rating] for rating in RATINGS])
    mean_rating = mu_t(period)
    variance = np.sum(
        [((RATINGS[rating] - mean_rating) ** 2) * period[rating] for rating in RATINGS]
    ) / sample_size
    return 1 - sqrt(variance) / 2


def direction(data, lam=0.8):
    return np.sum(weights(data, lam) * np.array([mu_t(period) for period in data]))


def agreement(data, lam=0.8):
    return np.sum(weights(data, lam) * np.array([C_t(period) for period in data]))


def stability(data, lam=0.8):
    period_weights = weights(data, lam)
    period_means = np.array([mu_t(period) for period in data])
    weighted_mean = np.sum(period_weights * period_means)
    weighted_variance = np.sum(period_weights * (period_means - weighted_mean) ** 2)
    return 1 - 0.5 * sqrt(weighted_variance)


def sample_confidence(data, lam=0.8, k=10):
    period_weights = weights(data, lam)
    sample_sizes = [np.sum([period[rating] for rating in RATINGS]) for period in data]
    weighted_sample_size = np.sum(period_weights * np.array(sample_sizes))
    return weighted_sample_size / (weighted_sample_size + k)
