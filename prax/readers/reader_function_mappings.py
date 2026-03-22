from prax.readers.arxiv.arxiv import get_arxiv_data
from prax.readers.latex.latext_gpt_tools import latex_to_english
from prax.readers.news.new_york_times import get_nyt_article_text, get_nyt_headlines
from prax.readers.news.physics_today import physics_today_fetch_article_text, physics_today_fetch_articles

reader_function_mappings = {
    "get_nyt_article_text": get_nyt_article_text,
    "latex_to_english": latex_to_english,
    "get_nyt_headlines": get_nyt_headlines,
    "get_arxiv_data": get_arxiv_data,
    "physics_today_fetch_articles": physics_today_fetch_articles,
    "physics_today_fetch_article_text": physics_today_fetch_article_text,
    # add more functions here
}
