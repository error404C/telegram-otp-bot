import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
from utils import extract_otp_from_text, clean_phone_number, clean_service_name


class IVASMSScraper:
    """Fixed scraper for ivasms.com"""

    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.session = requests.Session()
        # ✅ FIXED: correct base URL — no www
        self.base_url = "https://ivasms.com"
        self.is_logged_in = False

        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        })

    def login(self) -> bool:
        """Login to IVASMS account."""
        try:
            print(f"Logging in to IVASMS as: {self.email}")

            # Step 1: Get login page and extract CSRF token
            r = self.session.get(f"{self.base_url}/login", timeout=15)
            soup = BeautifulSoup(r.content, 'html.parser')

            token_tag = soup.find('input', {'name': '_token'})
            csrf_token = token_tag['value'] if token_tag else ''

            if not csrf_token:
                print("Warning: No CSRF token found on login page")

            # Step 2: Submit login form
            login_resp = self.session.post(
                f"{self.base_url}/login",
                data={
                    'email': self.email,
                    'password': self.password,
                    '_token': csrf_token,
                },
                timeout=15,
                allow_redirects=True
            )

            # Step 3: Verify login success
            final_url = login_resp.url.lower()
            page_text = login_resp.text.lower()

            if (
                'login' not in final_url or
                'logout' in page_text or
                'dashboard' in page_text or
                'sms' in final_url
            ):
                self.is_logged_in = True
                print("✅ IVASMS login successful")
                return True

            print("❌ Login failed — check your IVASMS_EMAIL and IVASMS_PASSWORD in .env")
            return False

        except Exception as e:
            print(f"Login error: {e}")
            return False

    def fetch_messages(self) -> list:
        """
        Fetch today's SMS messages from IVASMS.
        Uses the correct endpoint: /sms?date=DD/MM/YYYY
        """
        if not self.is_logged_in:
            if not self.login():
                print("Cannot fetch messages — not logged in")
                return []

        try:
            today = datetime.now().strftime('%d/%m/%Y')
            url = f"{self.base_url}/sms?date={today}"
            print(f"Fetching SMS from: {url}")

            resp = self.session.get(url, timeout=15)

            # Detect session expiry (redirected back to login)
            if 'login' in resp.url.lower():
                print("Session expired — re-logging in...")
                self.is_logged_in = False
                if not self.login():
                    return []
                resp = self.session.get(url, timeout=15)

            soup = BeautifulSoup(resp.content, 'html.parser')
            return self._parse_sms_table(soup)

        except Exception as e:
            print(f"Error fetching messages: {e}")
            return []

    def _parse_sms_table(self, soup: BeautifulSoup) -> list:
        """
        Parse the SMS table from the IVASMS /sms page.
        Table structure: Number | Time | Message | (optional: Country/Service)
        """
        messages = []

        try:
            # Find the main data table
            table = soup.find('table')
            if not table:
                print("No table found on SMS page — page structure may have changed")
                return []

            rows = table.find_all('tr')
            if not rows:
                return []

            # Skip header row
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) < 3:
                    continue

                try:
                    # Column layout on ivasms.com/sms:
                    # [0] Phone number
                    # [1] Timestamp
                    # [2] SMS message text
                    # [3] Country/service (optional)

                    phone_raw = cols[0].get_text(strip=True)
                    timestamp = cols[1].get_text(strip=True)
                    message_text = cols[2].get_text(strip=True)
                    country = cols[3].get_text(strip=True) if len(cols) > 3 else 'Unknown'

                    # Clean up fields
                    phone = clean_phone_number(phone_raw)
                    otp = extract_otp_from_text(message_text)
                    service = clean_service_name(
                        self._detect_service(message_text) or country
                    )

                    if not phone and not message_text:
                        continue

                    messages.append({
                        'otp': otp or 'N/A',
                        'phone': phone or phone_raw,
                        'service': service or 'Unknown',
                        'timestamp': timestamp,
                        'raw_message': message_text,
                        'country': country,
                    })

                except Exception as e:
                    print(f"Row parse error: {e}")
                    continue

            print(f"Parsed {len(messages)} messages from IVASMS")
            return messages

        except Exception as e:
            print(f"Table parse error: {e}")
            return []

    def _detect_service(self, message: str) -> str:
        """Detect which service sent the SMS based on message content."""
        msg = message.lower()
        services = {
            'Facebook': ['facebook', 'fb'],
            'WhatsApp': ['whatsapp'],
            'Google': ['google'],
            'Twitter': ['twitter', 'x.com'],
            'Instagram': ['instagram', 'ig'],
            'Telegram': ['telegram'],
            'TikTok': ['tiktok'],
            'Snapchat': ['snapchat'],
            'Discord': ['discord'],
            'Amazon': ['amazon'],
            'Microsoft': ['microsoft'],
            'Apple': ['apple', 'icloud'],
            'PayPal': ['paypal'],
            'Uber': ['uber'],
            'Netflix': ['netflix'],
            'LinkedIn': ['linkedin'],
            'Viber': ['viber'],
            'Line': ['line app', 'line:'],
        }
        for service, keywords in services.items():
            if any(k in msg for k in keywords):
                return service
        return ''

    def test_connection(self) -> bool:
        """Test basic connectivity to IVASMS."""
        try:
            r = self.session.get(self.base_url, timeout=10)
            return r.status_code == 200
        except Exception:
            return False


def create_scraper(email: str, password: str):
    """Factory function — creates, tests, and logs in the scraper."""
    if not email or not password:
        print("❌ IVASMS_EMAIL or IVASMS_PASSWORD not set in .env")
        return None

    scraper = IVASMSScraper(email, password)

    if not scraper.test_connection():
        print("❌ Cannot connect to ivasms.com — check internet/server")
        return None

    if not scraper.login():
        print("❌ IVASMS login failed — check credentials")
        return None

    return scraper
        
