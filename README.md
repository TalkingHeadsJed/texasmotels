# TABC Website URL Enricher

A production-ready tool that reads a TABC (Texas Alcoholic Beverage Commission) CSV/XLSX file and outputs a new CSV with each establishment's official website URL.

## Features

- **Google Places API Integration**: Primary method for finding official websites
- **Web Search Fallback**: Uses SerpAPI or Bing Search when Places API doesn't return a website
- **Smart Column Detection**: Auto-detects common TABC column names
- **Deduplication**: Prevents processing duplicate entries
- **Resume Mode**: Can continue from where it left off if interrupted
- **Caching**: SQLite-based caching for API results to minimize redundant calls
- **Rate Limiting**: Built-in rate limiting with exponential backoff
- **Domain Denylist**: Filters out social media, directories, and aggregator sites
- **Match Confidence**: Uses fuzzy matching to ensure accurate results

## Output Columns Added

| Column | Description |
|--------|-------------|
| `normalized_name` | Cleaned/normalized business name |
| `normalized_address` | Cleaned/normalized address |
| `place_id` | Google Place ID (if found) |
| `google_maps_url` | Google Maps URL for the location |
| `website` | Official website URL (blank if none found) |
| `source` | How the website was found: `places_api`, `web_search`, or `none` |
| `match_confidence` | Confidence score (0-1) of the match |
| `match_method` | Matching method: `name_address`, `name_city`, `address_only`, or `fallback` |
| `error` | Error message if any occurred |

## Installation

```bash
pip install -r requirements.txt
```

## Environment Variables

### Required
```bash
export GOOGLE_MAPS_API_KEY="your-google-maps-api-key"
```

### Optional (for web search fallback - HIGHLY RECOMMENDED)
```bash
# Preferred - SerpAPI (significantly improves results when Places API has no website)
export SERPAPI_KEY="your-serpapi-key"

# Alternative - Bing Search
export BING_SEARCH_KEY="your-bing-search-key"
```

**Note:** With both APIs configured, the enricher achieves near 100% website discovery rate.

## Usage

### Basic Usage
```bash
python enrich_tabc_websites.py --input tabc.csv --output tabc_with_websites.csv
```

### Resume from Previous Run
```bash
python enrich_tabc_websites.py --input tabc.csv --output tabc_with_websites.csv --resume
```

### Limit Number of Rows
```bash
python enrich_tabc_websites.py --input tabc.csv --output tabc_with_websites.csv --limit 500
```

### Filter by Permit Type
```bash
python enrich_tabc_websites.py --input tabc.csv --output tabc_with_websites.csv --permit-filter "BG,MB"
```

### Custom Column Mapping
```bash
python enrich_tabc_websites.py \
  --input tabc.csv \
  --output tabc_with_websites.csv \
  --name-col "Business Name" \
  --addr-col "Street Address" \
  --city-col "City" \
  --state-col "State" \
  --zip-col "Postal Code"
```

### Adjust Confidence Threshold
```bash
python enrich_tabc_websites.py \
  --input tabc.csv \
  --output tabc_with_websites.csv \
  --confidence 0.80
```

## Command Line Options

| Option | Description | Default |
|--------|-------------|---------|
| `--input`, `-i` | Input CSV/XLSX file path | Required |
| `--output`, `-o` | Output CSV file path | Required |
| `--resume` | Skip already processed rows | False |
| `--limit` | Maximum rows to process | None (all) |
| `--confidence` | Match confidence threshold | 0.75 |
| `--rate-limit` | Max API requests per second | 5 |
| `--name-col` | Override name column detection | Auto |
| `--addr-col` | Override address column detection | Auto |
| `--city-col` | Override city column detection | Auto |
| `--state-col` | Override state column detection | Auto |
| `--zip-col` | Override ZIP column detection | Auto |
| `--permit-col` | Override permit column detection | Auto |
| `--permit-filter` | Comma-separated permit types to include | None (all) |

## Domain Denylist

The following domains are automatically rejected as official websites:

- **Social Media**: facebook.com, instagram.com, tiktok.com, twitter.com, linkedin.com, youtube.com, pinterest.com, snapchat.com
- **Review Sites**: yelp.com, tripadvisor.com, untappd.com, foursquare.com, opentable.com, zomato.com
- **Directories**: yellowpages.com, bbb.org, allmenus.com
- **Maps**: google.com/maps, maps.apple.com, mapquest.com, maps.app.goo.gl
- **Delivery**: doordash.com, grubhub.com, ubereats.com, postmates.com, seamless.com
- **Link Aggregators**: linktr.ee

## Auto-Detected Column Names

### Name Columns
- Trade Name, DBA, Business Name, Licensee Name, Name, Establishment Name

### Address Columns
- Premise Street, Location Address, Address, Street Address, Premise Address

### City Columns
- City, Premise City, Location City

### State Columns
- State, Premise State, Location State

### ZIP Columns
- ZIP, Zip Code, Postal Code, Zipcode, Premise ZIP

### Permit Columns
- Permit Type, License Type, Permit, License, Type

## Caching & Resume

The tool creates an `enrichment_cache.db` SQLite database to store:
- Google Places API responses
- Web search results
- Previously processed rows

This enables:
1. **Resume functionality**: If the script is interrupted, rerun with `--resume` to continue
2. **Cost savings**: Cached API responses aren't re-fetched
3. **Speed**: Previously processed rows are skipped instantly

## Logging

Logs are written to both stdout and `enrichment.log` file with timestamps and log levels.

## Error Handling

- Individual row errors don't crash the entire run
- Errors are recorded in the `error` column of the output
- Rate limit errors trigger exponential backoff
- Incremental saves occur every 50 rows to prevent data loss

## API Costs

- **Google Places Text Search**: ~$32 per 1,000 requests
- **Google Place Details**: ~$17 per 1,000 requests
- **SerpAPI**: ~$50 per 5,000 requests (with caching, actual costs lower)

The caching system significantly reduces costs on subsequent runs.

## License

MIT License
