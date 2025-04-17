from functools import lru_cache
import pandas as pd
@lru_cache(maxsize=1)
def get_sp500_company_list():
    table = pd.read_html('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
    df = table[0]    
    return df