import io
import os
import tarfile

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

# Download, decompress, and store the tex files
for _title, link in articles:
    # Download the tar.gz file
    response = requests.get(link, stream=True)

    # Check if the download was successful
    if response.status_code == 200:
        # Open the tar.gz file
        file = tarfile.open(fileobj=io.BytesIO(response.content))

        # Get the identifier from the link
        identifier = link.split('/')[-1]

        # Create the directory if it doesn't exist
        dir_name = f'temp_tex/{identifier}'
        os.makedirs(dir_name, exist_ok=True)

        # Extract the largest tex file to the directory
        largest_tex_file = None
        for member in file.getmembers():
            if member.name.endswith('.tex'):
                if largest_tex_file is None or member.size > largest_tex_file.size:
                    largest_tex_file = member
        if largest_tex_file is not None:
            file.extract(largest_tex_file, dir_name)
