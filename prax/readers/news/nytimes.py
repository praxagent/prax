import json
import logging
import os
import sys

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

async def save_cookies(page, cookies_file):
    cookies = await page.context.cookies()
    with open(cookies_file, 'w') as f:
        json.dump(cookies, f)

async def load_cookies(page, cookies_file):
    if os.path.exists(cookies_file):
        with open(cookies_file) as f:
            cookies = json.load(f)
        await page.context.add_cookies(cookies)
    else:
        logger.error("Cookies file not found. Please log in and save cookies first.")
        sys.exit(1)

async def login_and_save_cookies(username, password, cookies_file):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await page.goto("https://www.nytimes.com/")

        await page.click("text=Log In")
        await page.fill("input[name='email']", username)
        await page.fill("input[name='password']", password)
        await page.click("button[type='submit']")

        await page.wait_for_load_state("load")

        await save_cookies(page, cookies_file)

        await browser.close()

async def get_nyt_headlines(username, password, cookies_file):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await load_cookies(page, cookies_file)
        await page.goto("https://www.nytimes.com/")

        headlines = await page.query_selector_all("section.story-wrapper")
        headlines_data = []

        for h in headlines:
            title = await h.query_selector("h3")
            summary = await h.query_selector("p.summary-class")
            link = await h.query_selector("a")

            if title and summary and link:
                title_text = await title.text_content()
                summary_text = await summary.text_content()

                title_text = title_text.replace('\xa0', ' ')
                summary_text = summary_text.replace('\xa0', ' ')

                url = await link.get_attribute("href")

                headline_data = {'title': title_text, 'summary': summary_text, 'url': url}

                if headline_data not in headlines_data:
                    headlines_data.append(headline_data)

        await browser.close()

        return headlines_data

async def get_article_text(url, cookies_file):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await load_cookies(page, cookies_file)
        await page.goto(url)

        article_body = await page.query_selector("section[name='articleBody']")
        if article_body:
            paragraphs = await article_body.query_selector_all("p")
            article_text = [await paragraph.text_content() for paragraph in paragraphs]
        else:
            article_text = ["Article body not found."]

        await browser.close()

        return article_text
