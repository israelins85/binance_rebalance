import time
import binance
import math
import sys
from datetime import datetime
import configparser
from pathlib import Path


# Set up the portfolio and rebalance parameters
portfolio = {"BTC": 0.50, "ICX": 0.30, "BNB": 0.2}
rebalance_threshold = 0.06  # Rebalance when the difference between target and current allocation exceeds 10%

api_key = None
api_secret = None

def load_settings():
    global api_key
    global api_secret

    home = str(Path.home())
    config_file_name = home + '/.binance_rebalance_settings.ini'

    config = configparser.ConfigParser()
    config.sections()
    config.read(config_file_name)
    config_dirty = False

    try:
        api_key = config.get('API', 'key')
    except Exception as e:  # pylint: disable=broad-except
        pass

    try:
        api_secret = config.get('API', 'secret')
    except Exception as e:  # pylint: disable=broad-except
        pass

    if api_key is None:
        api_key = "YOUR_API_KEY_HERE"
        config_dirty = True

    if api_secret is None:
        api_secret = "YOUR_API_SECRET_HERE"
        config_dirty = True

    if config_dirty:
        if not 'API' in config.sections():
            config.add_section('API')
        config.set('API', 'key', api_key)
        config.set('API', 'secret', api_secret)
        with open(config_file_name, 'w') as configfile:
            config.write(configfile)

    if api_key == "YOUR_API_KEY_HERE" or api_secret == "YOUR_API_SECRET_HERE":
        print(f"Please setup your configs on " + config_file_name)
        return False

    return True

if not load_settings():
    sys.exit()

# Set up the Binance API client
client = binance.Client(api_key=api_key,
                        api_secret=api_secret)

total_value = 0
in_wallet_symbols_value = {}
in_wallet_symbols_ammount = {}

DEBUG_MODE = False

def truncate(number, digits) -> float:
    # Improve accuracy with floating point operations, to avoid truncate(16.4, 2) = 16.39 or truncate(-1.13, 2) = -1.12
    nbDecimals = len(str(number).split(".")[1])
    if nbDecimals <= digits:
        return number
    stepper = 10.0 ** digits
    return math.trunc(stepper * number) / stepper

def wait_orders_filled(symbols_pending):
    while len(symbols_pending) > 0:
        print("waiting orders finishing")
        for symbol in symbols_pending:
            ticker = symbol + "USDT"
            orders = client.get_open_orders(symbol=ticker)
            if len(orders) == 0:
                del symbols_pending[symbol]
                break
        time.sleep(5)

symbols_ticker_cache = {}
def clear_symbols_ticker_cache():
    global symbols_ticker_cache
    symbols_ticker_cache = {}

def get_symbol_ticker(symbol):
    global symbols_ticker_cache

    if symbol in symbols_ticker_cache:
        return symbols_ticker_cache[symbol]

    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        symbols_ticker_cache[symbol] = ticker
        return ticker
    except binance.exceptions.BinanceAPIException as e:
        print(f"BinanceAPIException: {e}")
    except Exception as e:  # pylint: disable=broad-except
        print(f"Unexpected Error: {e}")

    return None


def get_symbol_price(symbol):
    ticker = get_symbol_ticker(symbol)
    if ticker is None:
        return None

    return float(ticker["price"])


symbols_info_cache = {}
def get_symbol_info(symbol):
    global symbols_info_cache

    if symbol in symbols_info_cache:
        return symbols_info_cache[symbol]

    try:
        info = client.get_symbol_info(symbol=symbol)
        symbols_info_cache[symbol] = info
        return info
    except binance.exceptions.BinanceAPIException as e:
        print(f"BinanceAPIException: {e}")
    except Exception as e:  # pylint: disable=broad-except
        print(f"Unexpected Error: {e}")

    return None

def get_symbol_info_filter(symbol, filter_name):
    info = get_symbol_info(symbol)
    if info is None:
        return None

    filters = info["filters"]
    for filt in filters:
        if (filt["filterType"] == filter_name):
            return filt
    return None

def floats_decimals(stepSize):
    decimal = 0
    is_dec = False
    for c in stepSize:
        if is_dec is True:
            decimal += 1
        if c == "1":
            break
        if c == ".":
            is_dec = True
    return decimal

def update_wallet_info():
    global total_value
    global in_wallet_symbols_value
    global in_wallet_symbols_ammount

    total_value = 0
    in_wallet_symbols_value = {}
    in_wallet_symbols_ammount = {}
    in_wallet_symbols_value["USDT"] = 0
    in_wallet_symbols_ammount["USDT"] = 0

    print("get balance data")
    balances = []
    try:
        balances = client.get_account()["balances"]
    except binance.exceptions.BinanceAPIException as e:
        print(f"BinanceAPIException: {e}")
    except Exception as e:  # pylint: disable=broad-except
        print(f"Unexpected Error: {e}")

    print("sum all wallet assets")
    for balance in balances:
        asset = balance["asset"]
        ammount = float(balance["free"]) + float(balance["locked"])
        if ammount > 0:
            if asset == "USDT":
                price = 1
            else:
                ticker = asset + "USDT"
                price = get_symbol_price(symbol=ticker)

                if price is None:
                    in_wallet_symbols_value = {}
                    in_wallet_symbols_ammount = {}
                    in_wallet_symbols_value["USDT"] = 0
                    in_wallet_symbols_ammount["USDT"] = 0
                    return

            in_wallet_symbols_ammount[asset] = ammount

            value = ammount * price
            if value > 0:
                in_wallet_symbols_value[asset] = value
                total_value += value
    return

current_allocation = {}
def calculate_current_allocation():
    global portfolio
    global current_allocation

    current_allocation = {}
    portfolio_total = 0
    for symbol in portfolio:
        current_allocation[symbol] = 0
        portfolio_total += portfolio[symbol]

    for symbol in portfolio:
        portfolio[symbol] = portfolio[symbol] / portfolio_total

    for symbol in in_wallet_symbols_value:
        if total_value == 0:
            current_allocation[symbol] = 0
        else:
            current_allocation[symbol] = in_wallet_symbols_value[symbol] / total_value

    return current_allocation

def calculate_operations(buy):
    ops = {}
    for symbol in current_allocation:
        if symbol == "USDT":
            continue
        ticker = symbol + "USDT"
        price = get_symbol_price(ticker)
        if price is None:
            return {}
        if not symbol in portfolio:
            portfolio[symbol] = 0
        diff = portfolio[symbol] - current_allocation[symbol]

        if abs(diff) < rebalance_threshold:
            continue
        if buy and diff < 0:
            continue
        if not buy and diff > 0:
            continue

        # Calculate the number of units of the asset to sell or buy
        lotSize = get_symbol_info_filter(ticker, "LOT_SIZE")
        decimals = floats_decimals(lotSize["stepSize"])
        units = abs(truncate((diff * total_value / price), decimals))
        units_price = price * units
        money_avail_to_buy = in_wallet_symbols_value["USDT"]

        print(f"Order for {symbol} units: {units} units_price:{units_price}")
        minQty = 0
        maxQty = sys.float_info.max
#         print(f"Initial minQty:{minQty:.8f}")
#         print(f"Initial maxQty:{maxQty:.8f}")

        if lotSize is not None:
            lotSizeMin = float(lotSize["minQty"])
            if lotSizeMin > minQty:
                minQty = max(minQty, lotSizeMin)
#                 print(f"Adjusted to lotSize.minQty {minQty:.5f}")
            lotSizeMax = float(lotSize["maxQty"])
            if lotSizeMax < maxQty:
                maxQty = min(maxQty, lotSizeMax)
#                 print(f"Adjusted to lotSize.maxQty {maxQty:.8f}")

        # limited by money avail
        if buy:
            affordable = truncate(money_avail_to_buy / price, decimals)
            if affordable < maxQty:
                maxQty = min(maxQty, affordable)
#                 print(f"Adjusted to money_avail_to_buy maxQty:{maxQty:.8f}")

        minNotional = get_symbol_info_filter(ticker, "MIN_NOTIONAL")
        if minNotional is not None:
            minNotional = float(minNotional["minNotional"])
            new_min = round((minNotional / price) + pow(10, -decimals) / 2, decimals)
            if new_min > minQty:
                minQty = max(minQty, new_min)
#                 print(f"Adjusted to minNotional {minQty:.8f}")

        if minQty >= maxQty:
            print(f"Skiped minQty : {minQty} < maxQty: {maxQty}")
            continue

        if maxQty == 0:
            print(f"Skiped maxQty: {maxQty} == 0")
            continue

        if units > maxQty:
            units = maxQty
            print(f"Skiped units : {units} > maxQty: {maxQty}")
            continue

        if units < minQty:
            print(f"Check if skip when units: {units} < minQty: {minQty}")
            if buy:
                rest_percent = (in_wallet_symbols_value[symbol] + (minQty * price)) / total_value
            else:
                rest_percent = (in_wallet_symbols_value[symbol] - (minQty * price)) / total_value

            print(f"Info rest_percent: {rest_percent}")

            diff = abs(portfolio[symbol] - rest_percent)
            if diff > rebalance_threshold:
                print(f"Skiped diff: {diff} > rebalance_threshold: {rebalance_threshold}")
                continue

            if buy:
                min_units_price = price * minQty
                if min_units_price > in_wallet_symbols_value["USDT"]:
                    print(f"Skiped min_units_price : {min_units_price} < minQty: {minQty}")
                    continue
                units = minQty
            else:
                units = minQty

        # update the money in wallet
        units_price = units * price
        if buy:
            in_wallet_symbols_value["USDT"] -= units_price
        else:
            in_wallet_symbols_value["USDT"] += units_price

        units_formated = "{:0.0{}f}".format(units, decimals)
        ops[symbol] = units_formated

    return ops


pending_orders = {}
def make_orders(operations, buy):
    global pending_orders

    for symbol in operations:
        quantity = operations[symbol]
        ticker = symbol + "USDT"
        price = get_symbol_price(ticker)
        value = float(quantity) * price
        try:
            if (not buy):
                print(f"Selling {quantity} {ticker} at {price} USDT {value}")
                if not DEBUG_MODE:
                    pending_orders[symbol] = client.order_market_sell(symbol=ticker, quantity=quantity)
            else:
                print(f"Buying {quantity} {ticker} at {price} USDT {value}")
                if not DEBUG_MODE:
                    pending_orders[symbol] = client.order_market_buy(symbol=ticker, quantity=quantity)
        except binance.exceptions.BinanceAPIException as e:
            print(f"BinanceAPIException: {e}")
        except Exception as e:  # pylint: disable=broad-except
            print(f"Unexpected Error: {e}")

    if len(pending_orders) > 0:
        print("wait all orders to finish")
        wait_orders_filled(pending_orders)

def do_sells(sell_ops):
    make_orders(sell_ops, False)

def do_buys(buy_ops):
    make_orders(buy_ops, True)

while True:
    clear_symbols_ticker_cache()

    print("")
    print("---------- ########## ---------- ########## ---------- ########## ---------- ########## ---------- ########## ---------- ########## ----------")
    print("")
    print("Initiated at " + datetime.today().strftime('%Y-%m-%d %H:%M:%S'))
    print(f"portfolio: {portfolio}")

    update_wallet_info()
    print(f"in_wallet_symbols_value: {in_wallet_symbols_value}")
    print(f"in_wallet_symbols_ammount: {in_wallet_symbols_ammount}")

    print("calculate allocation for each symbol")
    calculate_current_allocation()
    print(f"portfolio: {portfolio}")
    print(f"current_allocation: {current_allocation}")

    print("calculate sells")
    sell_ops = calculate_operations(False)

    if len(sell_ops) != 0:
        print("do sells to \"make\" money for buy later")
        print(f"sell_ops: {sell_ops}")
        do_sells(sell_ops)

    print("calculate buyes")
    buy_ops = calculate_operations(True)
    if len(buy_ops) != 0:
        print("do buys")
        print(f"buy_ops: {buy_ops}")
        do_buys(buy_ops)

    print("Wait for a while before checking again")
    print("")
    time.sleep(10)

