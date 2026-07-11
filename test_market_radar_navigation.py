import unittest

from app_core import MARKET_RADAR_HTML, rabbit_v72_polish_html


class MarketRadarNavigationTest(unittest.TestCase):
    def test_radar_uses_only_the_shared_navigation_container(self):
        html = rabbit_v72_polish_html(MARKET_RADAR_HTML)

        self.assertEqual(html.count('data-app-navigation'), 1)
        self.assertNotIn('<button class="nav"', html)
        self.assertIn('rq-radar-navigation-fallback', html)


if __name__ == "__main__":
    unittest.main()
