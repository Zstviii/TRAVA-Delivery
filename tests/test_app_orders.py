import sqlite3
import unittest
from datetime import date
from unittest.mock import Mock, patch

from app import App


class LocalOrderLoadingTests(unittest.TestCase):
    def test_orders_load_from_local_store_for_selected_day(self):
        selected_day = date(2026, 11, 27)
        store = Mock()
        store.list_orders.return_value = [{"id": 1, "order_no": "ORD-000001"}]
        store.list_couriers.return_value = []
        app = App.__new__(App)
        app.order_store = store
        app.selected_date = selected_day
        app.orders = []
        app.couriers = []

        with (
            patch.object(App, "_show_orders_state"),
            patch.object(App, "_set_database_status"),
            patch.object(App, "_render_orders_table") as render,
        ):
            App.load_orders(app)

        store.list_orders.assert_called_once_with(selected_day)
        store.list_couriers.assert_called_once_with()
        self.assertEqual(app.orders, [{"id": 1, "order_no": "ORD-000001"}])
        self.assertEqual(len(render.call_args.args[-1]), 1)

    def test_database_indicator_distinguishes_connected_and_disconnected_states(self):
        app = App.__new__(App)
        app.database_status_dot = Mock()
        app.database_status_label = Mock()

        App._set_database_status(app)
        app.database_status_label.configure.assert_called_with(
            text="База данных подключена",
            text_color="#83D19A",
        )

        App._set_database_status(app, sqlite3.OperationalError("database unavailable"))
        app.database_status_label.configure.assert_called_with(
            text="Нет подключения к базе данных",
            text_color="#F28B82",
        )


if __name__ == "__main__":
    unittest.main()
