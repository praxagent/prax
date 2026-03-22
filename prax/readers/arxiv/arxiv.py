import io
import os
import tarfile

import requests
from bs4 import BeautifulSoup

from prax.convo_states import convo_states


def extract_largest_tex_file(link):
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
            return f"Extracted {largest_tex_file.name} to {dir_name}"
        else:
            return "No .tex file found in the archive"
    else:
        return "Failed to download the file"

def get_arxiv_data(call_sid):
    url = convo_states[call_sid]["arxiv_url"]
    response = requests.get(url)
    html_content = response.text
    soup = BeautifulSoup(html_content, 'html.parser')

    article_data = []

    for dt, dd in zip(soup.find_all('dt'), soup.find_all('dd'), strict=False):
        title = dd.find('div', class_='list-title').text.strip().replace("Title:", "").strip()
        identifier = dt.span.a.text
        abstract_element = dd.find('p', class_='mathjax')
        abstract = abstract_element.text.strip() if abstract_element else None
        if abstract is None:
            response2 = requests.get(f"https://arxiv.org/abs/{identifier.replace('arXiv:', '')}")
            html_content2 = response2.text
            soup2 = BeautifulSoup(html_content2, 'html.parser')
            abstract_tag = soup2.find('meta', attrs={'name': 'citation_abstract'})
            abstract = abstract_tag['content'] if abstract_tag else "Abstract not found"

        authors = [a.text for a in dd.find('div', class_='list-authors').find_all('a')]
        comments = dd.find('div', class_='list-comments').text.strip().replace("Comments:", "").strip() if dd.find('div', class_='list-comments') else None
        subjects = dd.find('div', class_='list-subjects').text.strip().replace("Subjects:", "").split("; ")
        source_link = f'https://arxiv.org/e-print/{identifier.replace("arXiv:", "")}'
        # try:
        #     link = "https://arxiv.org" + dt.find('a', title="Download PDF")['href']
        # except:
        #     link = "https://arxiv.org" + dt.find('a', title="Abstract")['href']

        article = {
            'identifier': identifier,
            'title': title,
            'abstract': abstract,
            'authors': authors,
            'comments': comments,
            'subjects': subjects,
            'link': source_link
        }

        article_data.append(article)

    return article_data


# articles = get_arxiv_data("https://arxiv.org/list/astro-ph/new?skip=0&show=100")

# # Print the first 5 articles
# for article in articles[:5]:
#     print(article)

#extract_largest_tex_file('https://arxiv.org/e-print/2305.09702')
