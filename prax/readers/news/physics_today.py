from playwright.sync_api import sync_playwright

from prax.convo_states import convo_states


def fetch_articles_front_page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        page.goto('https://pubs.aip.org/physicstoday')
        articles = []

        elements = page.query_selector_all(".widget-dynamic-entry")

        for element in elements:
            article = {}
            title_element = element.query_selector('.widget-dynamic-post-title a')
            if title_element:
                article['title'] = title_element.inner_text()

            authors_element = element.query_selector('.widget-dynamic-post-authors')
            if authors_element:
                article['authors'] = authors_element.inner_text()

            abstract_element = element.query_selector('.widget-dynamic-post-abstract')
            if abstract_element:
                article['abstract'] = abstract_element.inner_text()

            link_element = title_element
            if link_element:
                article['link'] = link_element.get_attribute('href')

            # You would need to navigate to the article['link'] and fetch 'comments' and 'subjects' if they exist on the individual article page
            articles.append(article)

        browser.close()

    return articles

def physics_today_fetch_articles(call_sid=None):
    pages = [
        'https://pubs.aip.org/physicstoday/search-results?SearchSourceType=1%22*%22&exPrm_qqq=%7b!payload_score+f%3dTags+func%3dmax%7d*&q=*&exPrm_fq=(ContentType%3a%22Online%22+AND+-ContentType%3aImage+AND+-Flags%3aObituaries)&hideSearchTerm=true',
        'https://pubs.aip.org/physicstoday/search-results?q=*&SearchSourceType=1%22*%22&fl_SiteID=1000045&exPrm_qqq=%7b!payload_score+f%3dTags+func%3dmax%7d*&exPrm_fq=(ContentType%3a%22Online%22+AND+-ContentType%3aImage+AND+-Flags%3aObituaries)&page=2',
        ]
    articles = []

    for i in pages:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.goto(i)

            elements = page.query_selector_all(".gbosGuy")

            for element in elements:
                article = {}

                # article = {
                #  'title': title,
                #  'abstract': abstract,
                #  'authors': authors,
                #  'comments': comments,
                #  'subjects': subjects,
                #  'link': link_to_full_article
                # }

                title_element = element.query_selector('.sri-title a')
                if title_element:
                    article['title'] = title_element.inner_text()

                authors_element = element.query_selector('.sri-authors a')
                if authors_element:
                    article['authors'] = authors_element.inner_text()

                abstract_element = element.query_selector('.abstract-response-placeholder')
                if abstract_element:
                    article['abstract'] = abstract_element.inner_text()

                link_element = title_element
                if link_element:
                    article['link'] = link_element.get_attribute('href')
                    article['comments'] = None
                    article['subjects'] = None

                # You would need to navigate to the article['link'] and fetch 'comments' and 'subjects' if they exist on the individual article page
                articles.append(article)

            browser.close()

    return articles

# articles = fetch_articles()
# print(articles)


def physics_today_fetch_article_text(reader_data, call_sid):
    url = reader_data['link']
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(60000)

        # Navigate to the page
        page.goto(url)

        # Extract text from each paragraph, excluding links
        # paragraph_texts = page.eval_on_selector_all("div.body-text > p", '''paragraphs => {
        #     return paragraphs.map(p => {
        #         return Array.from(p.childNodes).filter(n => n.nodeType == Node.TEXT_NODE).map(n => n.textContent).join("");
        #     });
        # }''')
        paragraph_texts = page.eval_on_selector_all("div.body-text > p", '''paragraphs => {
            return paragraphs.map(p => {
                const paragraphText = Array.from(p.childNodes)
                    .filter(n => n.nodeType === Node.TEXT_NODE || (n.nodeType === Node.ELEMENT_NODE && n.tagName === 'A'))
                    .map(n => n.nodeType === Node.TEXT_NODE ? n.textContent : n.innerText)
                    .join('');
                return paragraphText.trim();
            });
        }''')

        if call_sid:
            convo_states[call_sid]['article_content'] = paragraph_texts
            convo_states[call_sid]['old_content'] = paragraph_texts

        # Close browser
        browser.close()
    # if call_sid:
    #convo_states[call_sid]['article_content'] = paragraph_texts
    convo_states[call_sid]['read_buffer'][0] = paragraph_texts
    convo_states[call_sid]['read_buffer'][0].append('#FINISHED#')
    convo_states[call_sid]['buffer_redirect'] = "/reader"
    convo_states[call_sid]['current_buffer_id'] = 0
    convo_states[call_sid]['article_text'] = convo_states[call_sid]['read_buffer'][0][0]
    return paragraph_texts

#url = "https://pubs.aip.org/physicstoday/online/41777/Five-years-of-superconductivity-in-magic-angle?searchresult=1"

#print(physics_today_fetch_articles(call_sid=None))
#print(physics_today_fetch_article_text({'link': url}, call_sid=None))
