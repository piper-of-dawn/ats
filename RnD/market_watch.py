import subprocess

def get_price_from_binary(ticker: str) -> float:
    try:
        result = subprocess.run(
            ['./ats', ticker, 'latest'],
            cwd='/home/karma/ats/target/release',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True
        )
        output = result.stdout.strip()
        return float(output)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Execution failed: {e.stderr.strip()}") from e
    except ValueError:
        raise ValueError(f"Failed to parse float from output: '{output}'")



from datetime import datetime, timedelta
class Ticker:
    def __init__ (self, ticker: str, queue_size: int = 100):
        self.ticker = ticker
        self.price_queue = []
        self.queue_size = queue_size        

    def get_latest_price (self) -> float:
        timestamp = datetime.now()
        try:
            price = get_price_from_binary(self.ticker)
            self.price_queue.append((timestamp, price))
            if len(self.price_queue) > self.queue_size:
                self.price_queue.pop(0)
            return price
        except Exception as e:
            print(f"Error getting latest price for {self.ticker}: {e}")
            return None
        
    def get_15_minute_return (self) -> float:
        if len(self.price_queue) < 2:
            return None
        current_time = datetime.now()
        cutoff = current_time - timedelta(minutes=15)
        for i, (ts, _) in enumerate(self.price_queue):
            if ts >= cutoff:
                recent_prices = self.price_queue[i:]
                if len(recent_prices) >= 2:
                    return recent_prices[-1][1] - recent_prices[0][1]
                else:
                    return None
                
from time import sleep
while(True):
    t = Ticker(".INX:INDEXSP")
    price = t.get_latest_price()
    print(t.price_queue)
    sleep(10)
    
