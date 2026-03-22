import requests
from bs4 import BeautifulSoup

url = "https://arxiv.org/list/astro-ph/new"

response = requests.get(url)
soup = BeautifulSoup(response.text, 'html.parser')

# Find all dt tags as they contain the identifier for each paper
dt_tags = soup.find_all('dt')

# Find all dd tags as they contain the title and authors for each paper
dd_tags = soup.find_all('dd')

articles = []
for dt, dd in zip(dt_tags, dd_tags, strict=False):
    # Get the identifier
    identifier = dt.span.a.text

    # Try to get the title
    try:
        title = dd.find('div', {'class': 'list-title mathjax'}).text.replace('Title: ', '').strip()
    except AttributeError:
        title = 'Title not found'

    # Construct the link to the source file
    link = f'https://arxiv.org/e-print/{identifier.replace("arXiv:", "")}'

    articles.append((title, link))

# Print the titles and links
for title, link in articles:
    print(f'Title: {title}\nLink: {link}\n')
