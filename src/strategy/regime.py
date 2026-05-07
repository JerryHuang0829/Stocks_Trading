"""市場狀態偵測模組"""

import logging
import pandas as pd

logger = logging.getLogger(__name__)


def detect_regime(df: pd.DataFrame) -> str:
    """
    判斷當前市場狀態（趨勢 or 震盪）

    依據：
    - ADX > 25: 趨勢明確
    - ADX < 20: 震盪盤整
    - SMA 排列方向決定趨勢方向

    Returns:
        'trending_up'   - 上升趨勢（ADX 高 + 多頭排列）
        'trending_down'  - 下降趨勢（ADX 高 + 空頭排列）
        'ranging'        - 震盪盤整（ADX 低）
    """
    latest = df.iloc[-1]

    adx = latest.get('adx')
    sma_fast = latest.get('sma_fast')
    sma_slow = latest.get('sma_slow')

    # ADX 不可用時，fallback 到 SMA 判斷
    if pd.isna(adx):
        if pd.notna(sma_fast) and pd.notna(sma_slow):
            return 'trending_up' if sma_fast > sma_slow else 'trending_down'
        return 'ranging'

    # ADX 低 → 震盪
    if adx < 20:
        return 'ranging'

    # ADX 灰區（20~25）：需要 structure 額外確認才算趨勢
    if adx < 25:
        structure = latest.get('structure', 0)
        if pd.notna(sma_fast) and pd.notna(sma_slow):
            if sma_fast > sma_slow and structure == 1:
                return 'trending_up'
            elif sma_fast < sma_slow and structure == -1:
                return 'trending_down'
        # 灰區無結構確認 → 視為震盪
        return 'ranging'

    # ADX >= 25 → 明確趨勢，用 SMA 判斷方向
    if pd.notna(sma_fast) and pd.notna(sma_slow):
        if sma_fast > sma_slow:
            return 'trending_up'
        else:
            return 'trending_down'

    # fallback
    return 'ranging'


def get_regime_weights(regime: str) -> dict:
    """
    根據市場狀態回傳各指標的權重

    趨勢市：重視 SMA/MACD（追蹤趨勢的指標）
    震盪市：重視 RSI/BB（反轉型指標）
    """
    weights = {
        'trending_up': {
            'sma': 0.25,
            'rsi': 0.10,
            'macd': 0.30,
            'bb': 0.05,
            'volume': 0.15,
            'institutional': 0.15,
        },
        'trending_down': {
            'sma': 0.25,
            'rsi': 0.10,
            'macd': 0.30,
            'bb': 0.05,
            'volume': 0.15,
            'institutional': 0.15,
        },
        'ranging': {
            'sma': 0.10,
            'rsi': 0.25,
            'macd': 0.10,
            'bb': 0.25,
            'volume': 0.15,
            'institutional': 0.15,
        },
    }
    return weights.get(regime, weights['ranging'])


def get_regime_weights_v2(regime: str) -> dict:
    """
    v2 權重分配（新增 pullback 和 structure）

    核心思路：
    ┌──────────┬────────────────────────────────────┐
    │ 趨勢市   │ 重視「回調進場」+ 「結構確認」       │
    │          │ 等回調到均線才買，不追高              │
    ├──────────┼────────────────────────────────────┤
    │ 震盪市   │ 重視「RSI 背離」+「BB 超賣超買」     │
    │          │ 抓反轉點，在支撐買壓力賣              │
    └──────────┴────────────────────────────────────┘
    """
    weights = {
        'trending_up': {
            'sma': 0.10,           # 方向確認（降低，因為已知是趨勢）
            'rsi': 0.10,           # 背離偵測
            'macd': 0.10,          # 動能確認
            'bb': 0.05,            # 通道位置
            'volume': 0.10,        # 量能確認
            'pullback': 0.25,      # ★ 回調進場（最重要！）
            'structure': 0.20,     # ★ 結構健康度
            'institutional': 0.10,
        },
        'trending_down': {
            'sma': 0.10,
            'rsi': 0.10,
            'macd': 0.10,
            'bb': 0.05,
            'volume': 0.10,
            'pullback': 0.25,      # ★ 反彈做空時機
            'structure': 0.20,     # ★ 結構惡化程度
            'institutional': 0.10,
        },
        'ranging': {
            'sma': 0.05,           # 震盪市均線意義不大
            'rsi': 0.25,           # ★ RSI 背離（抓反轉最強）
            'macd': 0.05,          # 震盪市 MACD 假信號多
            'bb': 0.20,            # ★ 布林通道（超賣超買）
            'volume': 0.10,
            'pullback': 0.05,      # 震盪市回調意義不大
            'structure': 0.20,     # 結構轉變 = 震盪可能結束
            'institutional': 0.10,
        },
    }
    return weights.get(regime, weights['ranging']).copy()


def get_regime_display(regime: str) -> str:
    """市場狀態的中文顯示"""
    display = {
        'trending_up': '上升趨勢',
        'trending_down': '下降趨勢',
        'ranging': '震盪盤整',
    }
    return display.get(regime, '未知')
