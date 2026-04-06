from datamodel import Order, OrderDepth, TradingState
from typing import Dict, List


class Trader:
    LIMITS = {
        "EMERALDS": 80,
        "TOMATOES": 80,
    }

    # How aggressively to skew prices when holding inventory
    SKEW_FACTOR = 0.3
    # Edge required before we aggressively take a price
    TAKE_EDGE = 2
    # How many units to quote passively per side
    QUOTE_SIZE = 15
    # How many units to take aggressively
    TAKE_SIZE = 20

    def run(self, state: TradingState):
        orders_by_product: Dict[str, List[Order]] = {}

        for product, order_depth in state.order_depths.items():
            if product not in self.LIMITS:
                orders_by_product[product] = []
                continue

            position = int(state.position.get(product, 0))
            limit = self.LIMITS[product]

            fair_value = self.estimate_fair_value(product, order_depth, state)
            if fair_value is None:
                orders_by_product[product] = []
                continue

            orders_by_product[product] = self.generate_orders(
                product, order_depth, position, limit, fair_value
            )

        return orders_by_product, 0, ""

    def estimate_fair_value(
        self, product: str, order_depth: OrderDepth, state: TradingState
    ) -> float | None:
        """Mid of best bid/ask, pulled toward recent trade prices."""
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return None

        best_bid = max(order_depth.buy_orders)
        best_ask = min(order_depth.sell_orders)
        mid = (best_bid + best_ask) / 2.0

        # Blend with recent market trade prices for better signal
        recent_trades = state.market_trades.get(product, [])
        if recent_trades:
            avg_trade = sum(t.price for t in recent_trades) / len(recent_trades)
            mid = 0.7 * mid + 0.3 * avg_trade

        return mid

    def generate_orders(
        self,
        product: str,
        order_depth: OrderDepth,
        position: int,
        limit: int,
        fair_value: float,
    ) -> List[Order]:
        orders: List[Order] = []
        buy_capacity = limit - position
        sell_capacity = limit + position

        # --- Aggressive taking: hit mispricings immediately ---
        for ask_price in sorted(order_depth.sell_orders):
            if ask_price < fair_value - self.TAKE_EDGE and buy_capacity > 0:
                vol = min(self.TAKE_SIZE, buy_capacity,
                          -order_depth.sell_orders[ask_price])
                if vol > 0:
                    orders.append(Order(product, ask_price, vol))
                    buy_capacity -= vol

        for bid_price in sorted(order_depth.buy_orders, reverse=True):
            if bid_price > fair_value + self.TAKE_EDGE and sell_capacity > 0:
                vol = min(self.TAKE_SIZE, sell_capacity,
                          order_depth.buy_orders[bid_price])
                if vol > 0:
                    orders.append(Order(product, bid_price, -vol))
                    sell_capacity -= vol

        # --- Passive quoting: skew toward reducing inventory ---
        # Skew: if long, lower both bid and ask to attract sellers
        skew = self.SKEW_FACTOR * position
        best_bid = max(order_depth.buy_orders)
        best_ask = min(order_depth.sell_orders)

        # Stay inside the spread
        passive_bid = int(best_bid + 1 - skew)
        passive_ask = int(best_ask - 1 - skew)

        # Don't cross the spread
        if passive_bid >= passive_ask:
            passive_bid = int(fair_value - 1 - skew)
            passive_ask = int(fair_value + 1 - skew)
        if passive_bid >= passive_ask:
            return orders  # spread too tight, skip passive

        buy_size = min(self.QUOTE_SIZE, buy_capacity)
        sell_size = min(self.QUOTE_SIZE, sell_capacity)

        if buy_size > 0:
            orders.append(Order(product, passive_bid, buy_size))
        if sell_size > 0:
            orders.append(Order(product, passive_ask, -sell_size))

        return orders