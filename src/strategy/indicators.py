"""統一技術指標計算模組"""

import logging
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)


def calculate_indicators(df: pd.DataFrame, strategy: dict) -> pd.DataFrame:
    """
    在 DataFrame 上計算所有技術指標

    Args:
        df: OHLCV DataFrame
        strategy: 策略參數字典（來自 settings.yaml）

    Returns:
        加上指標欄位的 DataFrame
    """
    df = df.copy()

    sma_fast = strategy.get('sma_fast', 20)
    sma_slow = strategy.get('sma_slow', 50)
    rsi_period = strategy.get('rsi_period', 14)
    bb_period = strategy.get('bb_period', 20)
    bb_std = strategy.get('bb_std', 2)
    macd_fast = strategy.get('macd_fast', 12)
    macd_slow = strategy.get('macd_slow', 26)
    macd_signal = strategy.get('macd_signal', 9)
    atr_period = strategy.get('atr_period', 14)
    adx_period = strategy.get('adx_period', 14)
    vol_ma_period = strategy.get('volume_ma_period', 20)

    # SMA 均線
    df['sma_fast'] = ta.sma(df['close'], length=sma_fast)
    df['sma_slow'] = ta.sma(df['close'], length=sma_slow)

    # RSI
    df['rsi'] = ta.rsi(df['close'], length=rsi_period)

    # MACD
    macd_result = ta.macd(df['close'], fast=macd_fast, slow=macd_slow, signal=macd_signal)
    if macd_result is not None:
        # 動態取得欄位名稱，避免硬編碼
        macd_cols = macd_result.columns.tolist()
        df = df.join(macd_result)
        # 統一別名
        if len(macd_cols) >= 3:
            df = df.rename(columns={
                macd_cols[0]: 'macd_line',
                macd_cols[1]: 'macd_histogram',
                macd_cols[2]: 'macd_signal',
            })

    # 布林通道
    bb_result = ta.bbands(df['close'], length=bb_period, std=bb_std)
    if bb_result is not None:
        bb_cols = bb_result.columns.tolist()
        df = df.join(bb_result)
        # 動態取得欄位名，對應 lower / mid / upper / bandwidth / percent
        for col in bb_cols:
            col_lower = col.lower()
            if col.startswith('BBL'):
                df = df.rename(columns={col: 'bb_lower'})
            elif col.startswith('BBM'):
                df = df.rename(columns={col: 'bb_mid'})
            elif col.startswith('BBU'):
                df = df.rename(columns={col: 'bb_upper'})
            elif col.startswith('BBB'):
                df = df.rename(columns={col: 'bb_bandwidth'})
            elif col.startswith('BBP'):
                df = df.rename(columns={col: 'bb_percent'})

    # ATR（波動度）
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=atr_period)

    # ADX（趨勢強度，用於 regime 偵測）
    adx_result = ta.adx(df['high'], df['low'], df['close'], length=adx_period)
    if adx_result is not None:
        adx_cols = adx_result.columns.tolist()
        df = df.join(adx_result)
        # 取第一個 ADX 欄位做別名
        for col in adx_cols:
            if col.startswith('ADX'):
                df = df.rename(columns={col: 'adx'})
                break

    # 成交量均線（用於量能確認）
    df['volume_ma'] = ta.sma(df['volume'], length=vol_ma_period)
    df['volume_ratio'] = df['volume'] / df['volume_ma']

    # 乖離率 BIAS（價格偏離均線程度）
    if 'sma_fast' in df.columns:
        df['bias_fast'] = (df['close'] - df['sma_fast']) / df['sma_fast'] * 100
    if 'sma_slow' in df.columns:
        df['bias_slow'] = (df['close'] - df['sma_slow']) / df['sma_slow'] * 100

    # ============================================================
    # 以下為進階指標（v2 策略改進）
    # ============================================================

    # RSI 背離偵測（價格創新低但 RSI 沒有 → 底部背離 = 領先買入信號）
    df['rsi_divergence'] = _detect_rsi_divergence(df)

    # 回調深度（價格回調到均線附近 = 好的進場點）
    if 'sma_fast' in df.columns and 'atr' in df.columns:
        # 回調到快線附近（1 ATR 以內）= 好進場點
        distance_to_sma = (df['close'] - df['sma_fast']).abs()
        df['pullback_score'] = 1 - (distance_to_sma / df['atr']).clip(0, 3) / 3
        # pullback_score: 1.0 = 剛好在均線上, 0.0 = 離均線 3ATR 以上

    # 支撐壓力位（近期高低點）
    df['support'] = df['low'].rolling(20).min()
    df['resistance'] = df['high'].rolling(20).max()
    if 'atr' in df.columns:
        # 離支撐多近（越近越適合買）
        df['near_support'] = ((df['close'] - df['support']) / df['atr']).clip(0, 5)
        # 離壓力多近（越近越適合賣）
        df['near_resistance'] = ((df['resistance'] - df['close']) / df['atr']).clip(0, 5)

    # 市場結構（Higher High / Higher Low = 上升趨勢結構）
    df['structure'] = _detect_market_structure(df)

    return df


def _detect_rsi_divergence(df: pd.DataFrame) -> pd.Series:
    """
    RSI 背離偵測

    底部背離（看漲）: 價格創近期新低，但 RSI 沒有創新低 → +1
    頂部背離（看跌）: 價格創近期新高，但 RSI 沒有創新高 → -1
    無背離 → 0

    背離是【領先指標】，比交叉早 2~5 根 K 線發出信號
    """
    lookback = 14
    result = pd.Series(0, index=df.index)

    if 'rsi' not in df.columns:
        return result

    for i in range(lookback, len(df)):
        window = df.iloc[i - lookback:i + 1]

        price_low = window['close'].min()
        price_high = window['close'].max()
        rsi_low = window['rsi'].min()
        rsi_high = window['rsi'].max()

        current_price = df.iloc[i]['close']
        current_rsi = df.iloc[i]['rsi']

        if pd.isna(current_rsi):
            continue

        # 底部背離：價格在近期低點附近，但 RSI 比上次低點時更高
        if current_price <= price_low * 1.01 and current_rsi > rsi_low * 1.05:
            result.iloc[i] = 1

        # 頂部背離：價格在近期高點附近，但 RSI 比上次高點時更低
        if current_price >= price_high * 0.99 and current_rsi < rsi_high * 0.95:
            result.iloc[i] = -1

    return result


def _detect_market_structure(df: pd.DataFrame, window: int = 5) -> pd.Series:
    """
    市場結構偵測

    上升結構 (Higher High + Higher Low) → +1
    下降結構 (Lower High + Lower Low) → -1
    無明確結構 → 0

    用於判斷趨勢的「健康程度」，比均線更即時
    """
    result = pd.Series(0, index=df.index)

    # 找出近期的 swing high / swing low
    for i in range(window * 2, len(df)):
        recent = df.iloc[i - window * 2:i + 1]
        half = len(recent) // 2

        first_half = recent.iloc[:half]
        second_half = recent.iloc[half:]

        fh_high = first_half['high'].max()
        fh_low = first_half['low'].min()
        sh_high = second_half['high'].max()
        sh_low = second_half['low'].min()

        # Higher High + Higher Low = 上升結構
        if sh_high > fh_high and sh_low > fh_low:
            result.iloc[i] = 1
        # Lower High + Lower Low = 下降結構
        elif sh_high < fh_high and sh_low < fh_low:
            result.iloc[i] = -1

    return result
