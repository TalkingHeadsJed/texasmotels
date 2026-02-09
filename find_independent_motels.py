#!/usr/bin/env python3
"""
Independent Motel Finder using SerpAPI

Finds independent motels (excludes national chains) using SerpAPI Google Maps.
Scrapes websites for contact information including emails and owner/manager names.

Author: Emergent Labs
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('motel_finder_serp.log')
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# NATIONAL BRANDS TO EXCLUDE (case-insensitive matching)
# ============================================================================

NATIONAL_BRANDS = [
    # Marriott
    "marriott", "jw marriott", "ritz-carlton", "ritz carlton", "st. regis", "st regis",
    "w hotels", "w hotel", "sheraton", "westin", "le méridien", "le meridien",
    "renaissance", "autograph collection", "tribute portfolio", "courtyard",
    "fairfield inn", "fairfield", "springhill suites", "springhill", "residence inn",
    "towneplace suites", "towneplace", "element", "ac hotels", "ac hotel", "aloft", "moxy",
    
    # Hilton
    "hilton", "waldorf astoria", "waldorf", "conrad", "lxr hotels", "lxr",
    "doubletree", "double tree", "curio collection", "curio", "canopy",
    "hilton garden inn", "hilton garden", "hampton inn", "hampton",
    "homewood suites", "homewood", "home2 suites", "home2", "tru by hilton", "tru hilton",
    "spark by hilton", "spark hilton",
    
    # Hyatt
    "hyatt", "park hyatt", "grand hyatt", "hyatt regency", "andaz",
    "hyatt place", "hyatt house",
    
    # IHG
    "ihg", "intercontinental", "kimpton", "hotel indigo", "voco",
    "crowne plaza", "holiday inn express", "holiday inn", "avid hotels", "avid",
    "even hotels", "staybridge suites", "staybridge", "candlewood suites", "candlewood",
    
    # Wyndham
    "wyndham grand", "wyndham", "dolce hotels", "dolce", "la quinta",
    "wingate", "ramada", "days inn", "super 8", "super8", "microtel",
    "baymont", "howard johnson", "travelodge",
    
    # Choice Hotels
    "choice hotels", "cambria", "radisson", "comfort inn", "comfort suites",
    "quality inn", "sleep inn", "clarion", "econo lodge", "econolodge",
    "rodeway inn", "rodeway", "mainstay suites", "mainstay", "suburban studios",
    
    # Best Western
    "best western premier", "best western plus", "best western", "surestay",
    
    # G6 Hospitality
    "motel 6", "motel6", "studio 6", "studio6",
    
    # Red Roof
    "red roof plus", "red roof inn", "red roof",
    
    # Extended Stay
    "extended stay america", "extended stay", "woodspring suites", "woodspring",
    "value place",
    
    # Sonesta
    "sonesta es suites", "sonesta simply suites", "sonesta select", "sonesta",
    
    # Others
    "drury inn", "drury hotels", "drury", "omni hotels", "omni",
    "loews hotels", "loews", "graduate hotels", "graduate",
    "my place hotels", "my place", "cobblestone hotels", "cobblestone",
    "country inn & suites", "country inn", "scottish inns", "scottish inn",
    "knights inn", "budget host", "oyo"
]

# Compile patterns for faster matching
BRAND_PATTERNS = [re.compile(rf'\b{re.escape(brand)}\b', re.IGNORECASE) for brand in NATIONAL_BRANDS]

# ============================================================================
# CONFIGURATION
# ============================================================================

DEFAULT_RATE_LIMIT = 3  # requests per second (conservative for SerpAPI)

# Email patterns
EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

# Contact pages to check
CONTACT_PAGES = [
    '/contact', '/contact-us', '/contactus', '/contact.html',
    '/about', '/about-us', '/aboutus',
    '/info', '/reach-us',
]

# Skip these email domains
SKIP_EMAIL_DOMAINS = {
    'example.com', 'sentry.io', 'wixpress.com', 'googleapis.com',
    'w3.org', 'schema.org', 'facebook.com', 'twitter.com', 'instagram.com',
    'sentry-next.wixpress.com'
}

# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class MotelInfo:
    """Information about a motel."""
    name: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    google_maps_url: str = ""
    rating: float = 0.0
    reviews: int = 0
    emails: str = ""
    owner_manager: str = ""
    scrape_status: str = ""
    error: str = ""


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def is_national_brand(name: str) -> bool:
    """Check if the motel name matches a national brand."""
    if not name:
        return False
    
    for pattern in BRAND_PATTERNS:
        if pattern.search(name):
            return True
    return False


# ============================================================================
# RATE LIMITER
# ============================================================================

class RateLimiter:
    def __init__(self, max_per_second: float = 3):
        self.min_interval = 1.0 / max_per_second
        self.last_request = 0
    
    def wait(self):
        now = time.time()
        elapsed = now - self.last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request = time.time()


# ============================================================================
# CACHE DATABASE
# ============================================================================

class CacheDB:
    def __init__(self, db_path: str = "motel_finder_serp_cache.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS search_cache (
                    query_hash TEXT PRIMARY KEY,
                    query TEXT,
                    response TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scraped_sites (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT,
                    emails TEXT,
                    owner_manager TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
    
    def get_search(self, key: str) -> Optional[Dict]:
        query_hash = hashlib.md5(key.encode()).hexdigest()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT response FROM search_cache WHERE query_hash = ?", (query_hash,)
            )
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
        return None
    
    def set_search(self, key: str, response: Dict):
        query_hash = hashlib.md5(key.encode()).hexdigest()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO search_cache (query_hash, query, response) VALUES (?, ?, ?)",
                (query_hash, key, json.dumps(response))
            )
            conn.commit()
    
    def get_scraped(self, url: str) -> Optional[Tuple[str, str]]:
        url_hash = hashlib.md5(url.encode()).hexdigest()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT emails, owner_manager FROM scraped_sites WHERE url_hash = ?", (url_hash,)
            )
            row = cursor.fetchone()
            if row:
                return row[0], row[1]
        return None
    
    def set_scraped(self, url: str, emails: str, owner_manager: str):
        url_hash = hashlib.md5(url.encode()).hexdigest()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scraped_sites (url_hash, url, emails, owner_manager) VALUES (?, ?, ?, ?)",
                (url_hash, url, emails, owner_manager)
            )
            conn.commit()


# ============================================================================
# SERPAPI CLIENT
# ============================================================================

class SerpAPIClient:
    """Client for SerpAPI Google Maps search."""
    
    def __init__(self, api_key: str, cache: CacheDB, rate_limiter: RateLimiter):
        self.api_key = api_key
        self.cache = cache
        self.rate_limiter = rate_limiter
        self.base_url = "https://serpapi.com/search"
        self.searches_used = 0
    
    def search_maps(self, query: str, location: str) -> List[Dict]:
        """Search Google Maps via SerpAPI."""
        cache_key = f"serp:{query}:{location}"
        
        cached = self.cache.get_search(cache_key)
        if cached:
            logger.info(f"Cache hit for: {query}")
            return cached.get('local_results', [])
        
        self.rate_limiter.wait()
        
        try:
            params = {
                'engine': 'google_maps',
                'q': query,
                'll': location,  # @lat,lng,zoom format
                'type': 'search',
                'api_key': self.api_key,
            }
            
            response = requests.get(self.base_url, params=params, timeout=30)
            
            if response.status_code == 429:
                logger.warning("SerpAPI rate limited")
                time.sleep(5)
                return []
            
            response.raise_for_status()
            data = response.json()
            
            self.searches_used += 1
            logger.info(f"SerpAPI search #{self.searches_used}: {query} - found {len(data.get('local_results', []))} results")
            
            self.cache.set_search(cache_key, data)
            
            return data.get('local_results', [])
            
        except Exception as e:
            logger.error(f"SerpAPI error: {e}")
            return []


# ============================================================================
# EMAIL SCRAPER
# ============================================================================

class EmailScraper:
    """Scrapes websites for email addresses and owner/manager names."""
    
    def __init__(self, cache: CacheDB, rate_limiter: RateLimiter):
        self.cache = cache
        self.rate_limiter = rate_limiter
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def scrape_website(self, url: str) -> Tuple[List[str], str]:
        """Scrape a website for emails and owner/manager names."""
        if not url:
            return [], ""
        
        # Check cache
        cached = self.cache.get_scraped(url)
        if cached:
            emails = cached[0].split(',') if cached[0] else []
            return emails, cached[1]
        
        emails = set()
        owner_manager = ""
        
        try:
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            
            pages_to_check = [url]
            for page in CONTACT_PAGES:
                pages_to_check.append(urljoin(base_url, page))
            
            for page_url in pages_to_check[:5]:  # Limit pages to check
                try:
                    self.rate_limiter.wait()
                    response = self.session.get(page_url, timeout=10, allow_redirects=True)
                    
                    if response.status_code == 200:
                        page_emails, page_owner = self._extract_info(response.text, parsed.netloc)
                        emails.update(page_emails)
                        if page_owner and not owner_manager:
                            owner_manager = page_owner
                    
                except requests.RequestException:
                    continue
            
            valid_emails = [e for e in emails if self._is_valid_email(e)]
            self.cache.set_scraped(url, ','.join(valid_emails), owner_manager)
            
            return valid_emails, owner_manager
            
        except Exception as e:
            logger.debug(f"Error scraping {url}: {e}")
            return [], ""
    
    def _extract_info(self, html: str, domain: str) -> Tuple[Set[str], str]:
        """Extract emails and owner/manager from HTML."""
        emails = set()
        owner_manager = ""
        
        soup = BeautifulSoup(html, 'html.parser')
        
        for script in soup(["script", "style"]):
            script.decompose()
        
        text = soup.get_text(separator=' ')
        
        # Find emails in text
        found_emails = EMAIL_PATTERN.findall(text)
        
        # Check mailto links
        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.startswith('mailto:'):
                email = href.replace('mailto:', '').split('?')[0].strip()
                if email:
                    found_emails.append(email)
        
        for email in found_emails:
            email = email.lower().strip()
            email_domain = email.split('@')[-1] if '@' in email else ''
            if email_domain not in SKIP_EMAIL_DOMAINS:
                emails.add(email)
        
        # Look for owner/manager
        owner_patterns = [
            r'(?:owner|manager|proprietor|operated by|managed by)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
            r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)(?:\s*[-,]\s*(?:owner|manager|proprietor))',
        ]
        
        for pattern in owner_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                owner_manager = matches[0].strip()[:100]  # Limit length
                break
        
        return emails, owner_manager
    
    def _is_valid_email(self, email: str) -> bool:
        """Check if email looks valid."""
        if not email or '@' not in email:
            return False
        
        email = email.lower()
        
        invalid_patterns = ['example.com', 'test.com', 'domain.com', 'your@', 'email@',
                          '.png', '.jpg', '.gif', '.css', '.js']
        
        for pattern in invalid_patterns:
            if pattern in email:
                return False
        
        domain = email.split('@')[-1]
        if domain in SKIP_EMAIL_DOMAINS:
            return False
        
        if '.' not in domain or len(domain.split('.')[-1]) < 2:
            return False
        
        return True


# ============================================================================
# MAIN FINDER
# ============================================================================

class IndependentMotelFinder:
    """Finds independent motels using SerpAPI."""
    
    def __init__(self, api_key: str, cache_db_path: str = "motel_finder_serp_cache.db"):
        self.cache = CacheDB(cache_db_path)
        self.rate_limiter = RateLimiter(DEFAULT_RATE_LIMIT)
        self.serp_client = SerpAPIClient(api_key, self.cache, self.rate_limiter)
        self.scraper = EmailScraper(self.cache, self.rate_limiter)
    
    def find_motels(self, city: str, state: str, lat: float, lng: float) -> List[MotelInfo]:
        """Find independent motels in the specified area."""
        logger.info(f"Searching for independent motels in {city}, {state}")
        
        # Multiple search queries to find more results
        queries = [
            f"motel {city} {state}",
            f"motor lodge {city} {state}",
            f"budget motel {city} {state}",
        ]
        
        location = f"@{lat},{lng},12z"  # 12z = city-level zoom
        
        all_results = []
        seen_names = set()
        
        for query in queries:
            results = self.serp_client.search_maps(query, location)
            for r in results:
                name = r.get('title', '').lower()
                if name not in seen_names:
                    seen_names.add(name)
                    all_results.append(r)
        
        results = all_results
        
        logger.info(f"Found {len(results)} total results, filtering...")
        
        motels = []
        skipped_brands = 0
        skipped_no_contact = 0
        
        for i, place in enumerate(results):
            name = place.get('title', '')
            
            # Filter 1: Skip national brands
            if is_national_brand(name):
                logger.debug(f"Skipping national brand: {name}")
                skipped_brands += 1
                continue
            
            logger.info(f"Processing {i+1}/{len(results)}: {name}")
            
            motel = MotelInfo()
            motel.name = name
            motel.address = place.get('address', '')
            motel.phone = place.get('phone', '')
            motel.website = place.get('website', '')
            motel.google_maps_url = place.get('place_id_search', '') or place.get('link', '')
            motel.rating = place.get('rating', 0.0)
            motel.reviews = place.get('reviews', 0)
            
            # Scrape for emails
            if motel.website:
                try:
                    emails, owner = self.scraper.scrape_website(motel.website)
                    motel.emails = ', '.join(emails)
                    motel.owner_manager = owner
                    motel.scrape_status = "success"
                except Exception as e:
                    motel.scrape_status = "failed"
                    motel.error = str(e)
            else:
                motel.scrape_status = "no_website"
            
            # Filter 2: Must have website OR email
            if not motel.website and not motel.emails:
                logger.debug(f"Skipping (no website or email): {name}")
                skipped_no_contact += 1
                continue
            
            motels.append(motel)
        
        logger.info(f"Skipped {skipped_brands} national brands, {skipped_no_contact} without contact info")
        
        return motels
    
    def save_to_csv(self, motels: List[MotelInfo], output_path: str):
        """Save results to CSV."""
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            writer.writerow([
                'Name', 'Address', 'Phone', 'Website', 'Email(s)', 
                'Owner/Manager', 'Rating', 'Reviews', 'Google Maps URL'
            ])
            
            for motel in motels:
                writer.writerow([
                    motel.name,
                    motel.address,
                    motel.phone,
                    motel.website,
                    motel.emails,
                    motel.owner_manager,
                    motel.rating,
                    motel.reviews,
                    motel.google_maps_url
                ])
        
        logger.info(f"Saved {len(motels)} motels to {output_path}")
    
    def get_searches_used(self) -> int:
        return self.serp_client.searches_used


def main():
    parser = argparse.ArgumentParser(description='Independent Motel Finder (SerpAPI)')
    parser.add_argument('--city', default='El Paso', help='City name')
    parser.add_argument('--state', default='TX', help='State')
    parser.add_argument('--lat', type=float, default=31.7619, help='Latitude')
    parser.add_argument('--lng', type=float, default=-106.4850, help='Longitude')
    parser.add_argument('--output', '-o', default='independent_motels.csv', help='Output CSV')
    
    args = parser.parse_args()
    
    api_key = os.environ.get('SERPAPI_KEY')
    if not api_key:
        logger.error("SERPAPI_KEY environment variable required")
        sys.exit(1)
    
    finder = IndependentMotelFinder(api_key)
    motels = finder.find_motels(args.city, args.state, args.lat, args.lng)
    
    finder.save_to_csv(motels, args.output)
    
    with_email = sum(1 for m in motels if m.emails)
    with_website = sum(1 for m in motels if m.website)
    with_owner = sum(1 for m in motels if m.owner_manager)
    
    logger.info(f"""
    ========== INDEPENDENT MOTEL FINDER SUMMARY ==========
    Location: {args.city}, {args.state}
    SerpAPI searches used: {finder.get_searches_used()}
    
    Results:
    - Independent motels found: {len(motels)}
    - With website: {with_website}
    - With email(s): {with_email}
    - With owner/manager: {with_owner}
    
    Output: {args.output}
    ======================================================
    """)


if __name__ == '__main__':
    main()
