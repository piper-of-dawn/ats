import dash
from dash import dash_table, html
import pandas as pd
import pickle
from yfinance_utils import get_sp500_data, get_ticker_data
import plotly.express as px
from dash import dcc
hist = pd.DataFrame(get_ticker_data("^SPX")[1])

sp500=get_sp500_data().set_index('Symbol')
# Load data from pickle file
with open('tickers.pkl', 'rb') as f:
    tickers_data = pickle.load(f)

# Process Alpha data
alpha_data = []
for ticker, obj in tickers_data.items():
    if (obj.alpha['coef'] > 0) and (obj.alpha['pval'] < 0.1):
        alpha_data.append({
            'Ticker': ticker,
            'Name': sp500.loc[ticker]['Security'],
            'Industry': sp500.loc[ticker]['GICS Sub-Industry'],
            'Alpha': obj.alpha['coef'],
            'Market Beta': obj.beta['coef'],
            'P Value': obj.alpha['pval']
        })

df_alpha = pd.DataFrame(alpha_data)
df_alpha = df_alpha.sort_values('Alpha', ascending=False).reset_index(drop=True)

# Process Momentum Beta data
momentum_data = []
for ticker, obj in tickers_data.items():
        momentum_data.append({
            'Ticker': ticker,
            'Name': sp500.loc[ticker]['Security'],
            'Coefficient': obj.momentum,
            # 'P Value': obj.momentum_beta['pval']
        })

df_momentum = pd.DataFrame(momentum_data)
df_momentum = df_momentum.sort_values('Coefficient', ascending=False).reset_index(drop=True)
# Create the price chart
sp500_chart = px.line(hist, 
                     x='date', 
                     y='close_price',
                     title='S&P 500 Closing Prices (5 Years)',
                     labels={'close_price': 'Price (USD)', 'date': 'Date'},
                     template='plotly_white')
sp500_chart.update_layout(
    hovermode="x unified",
    showlegend=False,
    xaxis_title='',
    yaxis_title='Price',
    margin=dict(l=40, r=40, t=60, b=40)
)
   
# Create Dash app
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("Financial Factor Analysis Dashboard",style={'textAlign': 'center'}),
    
    html.H2("Statistically Significant Positive Alphas (p < 0.1)",style={'textAlign': 'center'}),
    dash_table.DataTable(
        id='alpha-table',
        columns=[{"name": col, "id": col} for col in df_alpha.columns],
        data=df_alpha.to_dict('records'),
        page_size=10,
        style_table={'overflowX': 'auto','width': '50%','margin': '0 auto'},
        style_header={
            'backgroundColor': 'lightgrey',
            'fontWeight': 'bold'
        },
        style_cell={
            'textAlign': 'left',
            'padding': '5px'
        }
    ),
    
    html.H2(
    "Statistically Significant Momentum Betas (p < 0.05)",
    style={'textAlign': 'center'}
    ),

    dash_table.DataTable(
        id='momentum-table',
        columns=[{"name": col, "id": col} for col in df_momentum.columns],
        data=df_momentum.to_dict('records'),
        page_size=10,
        style_table={'overflowX': 'auto','width': '50%','margin': '0 auto'},
        style_header={
            'backgroundColor': 'lightgrey',
            'fontWeight': 'bold'
        },
        style_cell={
            'textAlign': 'left',
            'padding': '5px'
        }
    ),

    html.H2("S&P 500 Historical Prices",style={'textAlign': 'center'}),
    dcc.Graph(figure=sp500_chart, style={'width': '50%', 'margin': '0 auto'})
    ])

if __name__ == '__main__':
    app.run_server(debug=True)