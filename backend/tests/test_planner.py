from app.data.markets import MarketMeta
from app.exec.planner import book_walk, plan_orders, slippage_of

META = MarketMeta(amount_prec={"BTCUSDT": "0.001", "ETHUSDT": "0.01"}, min_order_usdt=0.5)
PX = {"BTCUSDT": 100000.0, "ETHUSDT": 3000.0}


def test_sell_before_buy_and_rounding():
    orders, dropped = plan_orders({"ETHUSDT": 1.0}, {"BTCUSDT": 0.0057, "ETHUSDT": 0.0}, PX, META, 2.0)
    assert [o.action for o in orders] == ["close_long", "open_long"]
    assert orders[1].amount == 0.005          # rounded down to 0.001 step
    assert orders[0].full_close


def test_below_minimum_is_dropped():
    orders, dropped = plan_orders({}, {"ETHUSDT": 0.001}, PX, META, 2.0)
    assert orders == [] and dropped


def test_flip_creates_two_orders_in_order():
    orders, _ = plan_orders({"ETHUSDT": 2.0}, {"ETHUSDT": -1.0}, PX, META, 2.0)
    assert [o.action for o in orders] == ["close_long", "open_short"]
    assert orders[1].leg == "margin" and orders[1].side == "sell"


def test_short_close_is_buy_on_margin():
    orders, _ = plan_orders({"ETHUSDT": -1.0}, {}, PX, META, 2.0)
    assert orders[0].action == "close_short" and orders[0].side == "buy" and orders[0].leg == "margin"


def test_book_walk_and_slippage():
    book = {"asks": [["100", "1"], ["101", "10"]], "bids": [["99", "1"], ["98", "10"]]}
    avg, filled = book_walk(book["asks"], 150)
    assert filled == 150 and 100 < avg < 101
    assert slippage_of(book, "buy", 150, 100) > 0
    assert slippage_of(book, "buy", 1e9, 100) is None   # not enough depth
