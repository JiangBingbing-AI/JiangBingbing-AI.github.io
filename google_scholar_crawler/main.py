from scholarly import scholarly, ProxyGenerator
import json
from datetime import datetime
import os


def configure_proxy():
    scraper_api_key = os.environ.get('SCRAPER_API_KEY')
    if scraper_api_key:
        proxy = ProxyGenerator()
        proxy.ScraperAPI(scraper_api_key)
        scholarly.use_proxy(proxy)
        print('Using ScraperAPI proxy for Google Scholar requests.')
        return

    if os.environ.get('SCHOLARLY_USE_FREE_PROXIES', '').lower() in {'1', 'true', 'yes'}:
        proxy = ProxyGenerator()
        if proxy.FreeProxies(timeout=1, wait_time=30):
            scholarly.use_proxy(proxy)
            print('Using free proxy rotation for Google Scholar requests.')


configure_proxy()

author: dict = scholarly.search_author_id(os.environ.get('GOOGLE_SCHOLAR_ID', 'mJhOACUAAAAJ'))
scholarly.fill(author, sections=['basics', 'indices', 'counts', 'publications'])
author['updated'] = str(datetime.now())
author['publications'] = {v['author_pub_id']:v for v in author['publications']}
print(json.dumps(author, indent=2))
os.makedirs('results', exist_ok=True)
with open(f'results/gs_data.json', 'w') as outfile:
    json.dump(author, outfile, ensure_ascii=False)

shieldio_data = {
  "schemaVersion": 1,
  "label": "citations",
  "message": f"{author['citedby']}",
}
with open(f'results/gs_data_shieldsio.json', 'w') as outfile:
    json.dump(shieldio_data, outfile, ensure_ascii=False)
