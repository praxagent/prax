reader_source_mappings = {
    'arxiv': {
        0: {
            "title": "Astro P H",
            "url": "https://arxiv.org/list/astro-ph/new?skip=0&show=20",
            "subject": "astro",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        1: {
            "title": "G R and Q C",
            "url": "https://arxiv.org/list/gr-qc/new?skip=0&show=20",
            "subject": "general",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        2: {
            "title": "Space Physics",
            "url": "https://arxiv.org/list/physics.space-ph/new?skip=0&show=25",
            "subject": "space_physics",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        3: {
            "title": "Classical Physics",
            "url": "https://arxiv.org/list/physics.class-ph/new?skip=0&show=25",
            "subject": "classical_physics",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        4: {
            "title": "All Physics",
            "url": "https://arxiv.org/list/physics/new?skip=0&show=25",
            "subject": "physics",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        5: {
            "title": "GeoPhysics",
            "url": "https://arxiv.org/list/physics.geo-ph/new?skip=0&show=25",
            "subject": "geophysics",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        6: {
            "title": "Physics FLuid Dynamics",
            "url": "https://arxiv.org/list/physics.flu-dyn/new?skip=0&show=25",
            "subject": "fluid_dynamics",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        7: {
            "title": "Data Analysis, Statistics and Probability",
            "url": "https://arxiv.org/list/physics.data-an/new?skip=0&show=25",
            "subject": "physics_data_analysis",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        8: {
            "title": "Computational Physics",
            "url": "https://arxiv.org/list/physics.comp-ph/new?skip=0&show=25",
            "subject": "computational_physics",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        9: {
            "title": "Mathematical Physics",
            "url": "https://arxiv.org/list/nlin.cd/new?skip=0&show=25",
            "subject": "math_physics",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        10: {
            "title": "Quantum Physics",
            "url": "https://arxiv.org/list/quant-ph/new?skip=0&show=25",
            "subject": "quant_physics",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        11: {
            "title": "Nonlinear Sciences, Chaotic Dynamics",
            "url": "https://arxiv.org/list/nlin.cd/new?skip=0&show=25",
            "subject": "chaos",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        12: {"title": "Nonlinear Sciences, All",
            "url": "https://arxiv.org/list/nlin/new?skip=0&show=25",
            "subject": "nlin",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
        13: {
            "title": "History and Philosophy of Physics",
            "url": "https://arxiv.org/list/physics.hist-ph/new?skip=0&show=25",
            "subject": "history_philosophy_physics",
            "headline_function": "get_arxiv_data",
            "article_function": "latex_to_english"
            },
    },
    'news': {
        0: {"title": "The New York Times",
            "url": "https://www.nytimes.com",
            "subject": "new_york_times",
            "headline_function": "get_nyt_headlines",
            "article_function": "get_nyt_article_text"
            },
        1: {"title": "Physics Today",
            "url": "",
            "subject": "physics_today",
            "headline_function": "physics_today_fetch_articles",
            "article_function": "physics_today_fetch_article_text"
            },
    }
}

article_option_mapping = {
    0: {
        'title': "Return to headlines",
        'function': None
    },
    1: {
        'title': "Discuss article with Chat GPT",
        'function': 'discuss_article_with_gpt'
    },
    2: {
        'title': "E-mail the article to yourself",
        'function': 'email_article'
    },
    # 3: {
    #     'title': "Receive link as text message",
    #     'function': 'text_article'
    # },
    # 4: {
    #     'title': "Read article again.",
    #     'function': 'read_article_again'
    # }
}

