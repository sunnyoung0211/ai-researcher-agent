"""汇总用的确定性统计（只用标准库）：样本标准差、t 分布 95% 区间、Welch t 检验、MAD 异常值。

详细设计 3 第 8.2 节：std 用 ddof=1；ci95 用 t 分布（n<2 时为 null）；outliers 只标注不剔除。
"""

from __future__ import annotations

import math
import statistics


def _betacf(a: float, b: float, x: float) -> float:
    max_iter, eps, fpmin = 200, 3e-14, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > fpmin else fpmin)
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > fpmin else fpmin)
        c = 1.0 + aa / c
        c = c if abs(c) > fpmin else fpmin
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > fpmin else fpmin)
        c = 1.0 + aa / c
        c = c if abs(c) > fpmin else fpmin
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 函数 I_x(a, b)。"""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(lbeta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lbeta) * _betacf(b, a, 1 - x) / b


def t_sf2(t: float, df: float) -> float:
    """双侧 p 值 P(|T| > |t|)。"""
    return betainc(df / 2, 0.5, df / (df + t * t))


def t_ppf975(df: float) -> float:
    """t 分布的 0.975 分位数（二分法求解）。"""
    lo, hi = 0.0, 1000.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_sf2(mid, df) > 0.05:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def describe(values: list[float]) -> dict:
    n = len(values)
    mean = statistics.fmean(values)
    std = statistics.stdev(values) if n >= 2 else None
    sem = std / math.sqrt(n) if std is not None else None
    ci95 = None
    if sem is not None:
        half = t_ppf975(n - 1) * sem
        ci95 = [round(mean - half, 6), round(mean + half, 6)]
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values]) if n >= 3 else 0.0
    outliers = [v for v in values if mad > 0 and abs(v - med) > 3 * mad]
    return {
        "n": n, "mean": round(mean, 6), "std": None if std is None else round(std, 6),
        "sem": None if sem is None else round(sem, 6), "ci95": ci95,
        "min": min(values), "max": max(values), "outliers": outliers,
    }


def welch(a: list[float], b: list[float]) -> tuple[float, float | None, float | None]:
    """返回 (均值差 a-b, Welch t, 双侧 p 值)。样本不足时 t、p 为 None。"""
    diff = statistics.fmean(a) - statistics.fmean(b)
    if len(a) < 2 or len(b) < 2:
        return round(diff, 6), None, None
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    se = math.sqrt(va + vb)
    if se == 0:
        return round(diff, 6), None, None
    t = diff / se
    df = (va + vb) ** 2 / ((va**2) / (len(a) - 1) + (vb**2) / (len(b) - 1))
    return round(diff, 6), round(t, 4), round(t_sf2(t, df), 4)
