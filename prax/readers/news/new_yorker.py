import logging

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

def get_article_info():
    url = "https://www.newyorker.com/"
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')

    articles = []

    article_blocks = soup.find_all('div', class_="River__riverItem___3huWr")

    for block in article_blocks:
        article = {}

        title_element = block.find('h2', class_="River__hed___re6RP")
        if title_element:
            article['title'] = title_element.text

        abstract_element = block.find('p', class_="River__dek___CayIg")
        if abstract_element:
            article['abstract'] = abstract_element.text

        author_element = block.find('span', class_="River__byline___3UPkM")
        if author_element:
            article['authors'] = author_element.text

        link_element = block.find('a')
        if link_element:
            article['link'] = link_element.get('href')

        articles.append(article)

    return articles
