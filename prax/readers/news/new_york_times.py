import json
import logging
import os
import sys

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from prax.convo_states import convo_states

logger = logging.getLogger(__name__)

cookies_file = "./prax/readers/news/nyt_cookies.json"

def save_cookies(session, cookies_file):
    with open(cookies_file, 'w') as f:
        json.dump(session.cookies.get_dict(), f)

def load_cookies(session, cookies_file):
    if os.path.exists(cookies_file):
        with open(cookies_file) as f:
            cookies = json.load(f)
        for cookie in cookies:
            session.cookies.set(cookie['name'], cookie['value'])
    else:
        logger.error("Cookies file not found. Please log in and save cookies first.")
        sys.exit(1)


def login_and_save_cookies(username, password, cookies_file):
    session = requests.Session()

    response = session.get("https://myaccount.nytimes.com/auth/login")
    soup = BeautifulSoup(response.text, "html.parser")
    csrf_token = soup.find("input", {"name": "csrf_token"})["value"]

    login_data = {
        "csrf_token": csrf_token,
        "email": username,
        "password": password,
        "remember_me": "Y",
    }
    session.post("https://myaccount.nytimes.com/svc/ios/v2/login", data=login_data)

    save_cookies(session, cookies_file)

    return session

def get_nyt_headlines(call_sid):
    session = requests.Session()
    load_cookies(session, cookies_file)
    response = session.get("https://www.nytimes.com/")
    soup = BeautifulSoup(response.text, "html.parser")

    headlines = soup.find_all("section", {"class": "story-wrapper"})
    headlines_data = []

    for h in headlines:
        title = h.find("h3")
        summary = h.find("p", {"class": "summary-class"})
        link = h.find("a")

        if title and summary and link:
            title_text = title.get_text(strip=True)
            summary_text = summary.get_text(strip=True)

            title_text = title_text.replace('\xa0', ' ')
            summary_text = summary_text.replace('\xa0', ' ')

            url = link["href"]

            headline_data = {
                'title': title_text,
                'abstract': summary_text,
                'authors': None,
                'comments': None,
                'subjects': None,
                'link': url
            }

            if headline_data not in headlines_data:
                headlines_data.append(headline_data)

    return headlines_data


def get_nyt_article_text(reader_data, call_sid=None):
    url = reader_data['link']
    session = requests.Session()
    load_cookies(session, cookies_file)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(60000)
        for cookie in session.cookies:
            if cookie.domain and cookie.path:
                page.context.add_cookies([
                    {"name": cookie.name,
                     "value": cookie.value,
                     "domain": cookie.domain,
                     "path": cookie.path}])

        page.goto(url)
        soup = BeautifulSoup(page.content(), "html.parser")

        article_body = soup.find("section", {"name": "articleBody"})
        if article_body:
            paragraphs = article_body.find_all("p")
            article_text = [paragraph.get_text(strip=True) for paragraph in paragraphs if paragraph.get_text(strip=True) not in ["Advertisement", "Supported by"]]
            convo_states[call_sid]['article_content'] = article_text
        else:
            convo_states[call_sid]['in_article'] = False
            convo_states[call_sid]['buffer_redirect'] = "/reader"
            article_text = ["Article body not found, try another article. I'm a work in progress and New York Times is sloppy."]

        browser.close()
        convo_states[call_sid]['read_buffer'][0] = article_text
        convo_states[call_sid]['read_buffer'][0].append('#FINISHED#')
        convo_states[call_sid]['buffer_redirect'] = "/reader"
        convo_states[call_sid]['current_buffer_id'] = 0
        convo_states[call_sid]['article_text'] = convo_states[call_sid]['read_buffer'][0][0]
    return article_text
