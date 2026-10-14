import unittest
from unittest.mock import Mock
from app.utils import enhanced_get_geolocation

class TestGeolocation(unittest.TestCase):
    def test_cloudflare_headers(self):
        # Mock request object with Cloudflare headers
        mock_request = Mock()
        mock_request.headers = {
            'CF-IPCountry': 'US',
            'CF-IPCity': 'New York',
            'CF-IPLatitude': '40.7128',
            'CF-IPLongitude': '-74.0060'
        }

        # Call function with mock request
        geo_data = enhanced_get_geolocation('1.2.3.4', mock_request)

        # Verify results
        self.assertEqual(geo_data['country_code'], 'US')
        self.assertEqual(geo_data['city'], 'New York')
        self.assertEqual(geo_data['latitude'], 40.7128)
        self.assertEqual(geo_data['longitude'], -74.0060)

    def test_no_request_object(self):
        # Call function without request object (should fallback to other methods, 
        # but here we just check it doesn't crash and returns empty or partial data if IP is invalid/mocked)
        # Since we can't easily mock the internal GeoIP/API calls without more patching, 
        # we'll just ensure it runs.
        try:
            enhanced_get_geolocation('127.0.0.1')
        except Exception as e:
            self.fail(f"enhanced_get_geolocation raised Exception unexpectedly: {e}")

if __name__ == '__main__':
    unittest.main()