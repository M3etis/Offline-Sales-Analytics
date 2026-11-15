"""Выбор типа графика: константные колонки и отрицательные значения."""
import unittest

from app.domain.ai.response_builder import _analyze_columns, detect_chart_type

# Ответ на «найди аномалии»: SQL вернул константу-описание рядом с регионом
ANOMALY_ROWS = [
    {"anomaly_type": "Спад в регионе", "region": "Шымкент", "change_pct": -47.4},
    {"anomaly_type": "Спад в регионе", "region": "Астана", "change_pct": -52.3},
    {"anomaly_type": "Спад в регионе", "region": "Атырау", "change_pct": -56.0},
    {"anomaly_type": "Спад в регионе", "region": "Алматы", "change_pct": -62.9},
    {"anomaly_type": "Спад в регионе", "region": "Актау", "change_pct": -66.6},
]


class ChartTypeTest(unittest.TestCase):
    def test_constant_text_column_is_not_category(self):
        self.assertEqual(_analyze_columns(ANOMALY_ROWS)["cat_cols"], ["region"])

    def test_single_row_keeps_text_column(self):
        self.assertEqual(_analyze_columns(ANOMALY_ROWS[:1])["cat_cols"], ["anomaly_type", "region"])

    def test_negative_values_never_pie(self):
        self.assertEqual(detect_chart_type("drop", "найди аномалии", ANOMALY_ROWS), "bar")
        # даже при явной просьбе о доле
        self.assertEqual(detect_chart_type("sql", "доля падения по регионам", ANOMALY_ROWS), "bar")

    def test_positive_shares_stay_pie(self):
        rows = [{"region": "Алматы", "revenue": 300.0}, {"region": "Астана", "revenue": 200.0}]
        self.assertEqual(detect_chart_type("sql", "доля выручки по регионам", rows), "pie")
        self.assertEqual(detect_chart_type("summary", "выручка по регионам", rows), "pie")


if __name__ == "__main__":
    unittest.main()
