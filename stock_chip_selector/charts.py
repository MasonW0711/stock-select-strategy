# ==========================================
# charts.py - Plotly 圖表繪製
# ==========================================

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


def create_price_chart(
    price_df: pd.DataFrame,
    stock_id: str,
    stock_name: str = ''
) -> go.Figure:
    """
    建立股價走勢圖（K線圖 + 成交量柱狀圖）。

    參數:
        price_df: 股價資料 DataFrame
        stock_id: 股票代號
        stock_name: 股票名稱（顯示用）

    回傳:
        Plotly Figure 物件
    """
    stock_prices = price_df[price_df['stock_id'] == stock_id].copy()
    stock_prices = stock_prices.sort_values('date')

    title = f'{stock_id} {stock_name} 股價走勢圖'

    if stock_prices.empty:
        return _empty_chart(title, '此股票無股價資料')

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.70, 0.30],
        subplot_titles=['K線走勢', '成交量（張）']
    )

    # K 線圖（臺灣習慣：紅漲綠跌）
    fig.add_trace(
        go.Candlestick(
            x=stock_prices['date'],
            open=stock_prices['open'],
            high=stock_prices['high'],
            low=stock_prices['low'],
            close=stock_prices['close'],
            name='K線',
            increasing_line_color='#E03030',
            decreasing_line_color='#00AA44',
            increasing_fillcolor='#E03030',
            decreasing_fillcolor='#00AA44',
        ),
        row=1, col=1
    )

    # 成交量顏色依漲跌決定
    bar_colors = [
        '#E03030' if c >= o else '#00AA44'
        for c, o in zip(stock_prices['close'], stock_prices['open'])
    ]

    fig.add_trace(
        go.Bar(
            x=stock_prices['date'],
            y=stock_prices['volume'],
            name='成交量',
            marker_color=bar_colors,
            showlegend=False,
        ),
        row=2, col=1
    )

    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor='center'),
        xaxis_rangeslider_visible=False,
        height=500,
        margin=dict(l=50, r=30, t=60, b=40),
        plot_bgcolor='white',
        paper_bgcolor='white',
    )

    fig.update_xaxes(gridcolor='#EEEEEE')
    fig.update_yaxes(gridcolor='#EEEEEE')

    return fig


def create_broker_volume_chart(
    broker_df: pd.DataFrame,
    stock_id: str,
    branch: str,
    stock_name: str = ''
) -> go.Figure:
    """
    建立指定分點每日買超張數柱狀圖。

    正值（紅色）代表買超，負值（綠色）代表賣超。
    連續買超段落以較深的紅色標示。

    參數:
        broker_df: 券商分點買賣超資料
        stock_id: 股票代號
        branch: 分點名稱
        stock_name: 股票名稱

    回傳:
        Plotly Figure 物件
    """
    stock_broker = broker_df[
        (broker_df['stock_id'] == stock_id) &
        (broker_df['branch'] == branch)
    ].copy()
    stock_broker = stock_broker.sort_values('date')

    title = f'{stock_id} {stock_name} ｜ {branch} 每日買超張數'

    if stock_broker.empty:
        return _empty_chart(title, '此分點無買賣超資料')

    bar_colors = ['#E03030' if v > 0 else '#00AA44' for v in stock_broker['net_buy']]

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=stock_broker['date'],
            y=stock_broker['net_buy'],
            marker_color=bar_colors,
            name='買超張數',
            text=stock_broker['net_buy'].apply(lambda v: f'{int(v):+,}'),
            textposition='outside',
        )
    )

    # 零軸參考線
    fig.add_hline(y=0, line_dash='solid', line_color='gray', line_width=1)

    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor='center'),
        xaxis_title='日期',
        yaxis_title='買超張數（張）',
        height=380,
        margin=dict(l=50, r=30, t=60, b=40),
        plot_bgcolor='white',
        paper_bgcolor='white',
    )

    fig.update_xaxes(gridcolor='#EEEEEE')
    fig.update_yaxes(gridcolor='#EEEEEE')

    return fig


def create_holder_chart(
    holder_df: pd.DataFrame,
    stock_id: str,
    stock_name: str = ''
) -> go.Figure:
    """
    建立集保戶數變化折線圖。

    下降趨勢以橘色標示，上升趨勢以藍色標示。

    參數:
        holder_df: 集保戶數資料
        stock_id: 股票代號
        stock_name: 股票名稱

    回傳:
        Plotly Figure 物件
    """
    stock_holders = holder_df[holder_df['stock_id'] == stock_id].copy()
    stock_holders = stock_holders.sort_values('week_date')

    title = f'{stock_id} {stock_name} 集保戶數變化'

    if stock_holders.empty:
        return _empty_chart(title, '此股票無集保戶數資料')

    # 判斷整體趨勢決定線條顏色
    if len(stock_holders) >= 2:
        first_count = stock_holders['holder_count'].iloc[0]
        last_count = stock_holders['holder_count'].iloc[-1]
        line_color = '#E07000' if last_count < first_count else '#1F77B4'
    else:
        line_color = '#1F77B4'

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=stock_holders['week_date'],
            y=stock_holders['holder_count'],
            mode='lines+markers+text',
            name='集保戶數',
            line=dict(color=line_color, width=2.5),
            marker=dict(size=8, color=line_color),
            text=stock_holders['holder_count'].apply(lambda v: f'{int(v):,}'),
            textposition='top center',
            textfont=dict(size=10),
        )
    )

    # 計算 Y 軸範圍，讓圖表不要太空曠
    y_min = stock_holders['holder_count'].min()
    y_max = stock_holders['holder_count'].max()
    y_padding = max((y_max - y_min) * 0.15, 100)

    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor='center'),
        xaxis_title='週別',
        yaxis_title='集保戶數（戶）',
        yaxis=dict(range=[y_min - y_padding, y_max + y_padding]),
        height=380,
        margin=dict(l=60, r=30, t=60, b=40),
        plot_bgcolor='white',
        paper_bgcolor='white',
    )

    fig.update_xaxes(gridcolor='#EEEEEE')
    fig.update_yaxes(gridcolor='#EEEEEE')

    return fig


def _empty_chart(title: str, message: str) -> go.Figure:
    """產生無資料時的空白圖表"""
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref='paper', yref='paper',
        x=0.5, y=0.5,
        showarrow=False,
        font=dict(size=14, color='gray')
    )
    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor='center'),
        height=350,
        plot_bgcolor='white',
        paper_bgcolor='white',
    )
    return fig
