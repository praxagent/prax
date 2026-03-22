import json
import logging

from prax.settings import settings

logger = logging.getLogger(__name__)


def _load_mapping(raw_value):
    """Return a dict parsed from a JSON string, defaulting to empty on failure."""
    if not raw_value:
        logger.warning("Mapping not set; defaulting to empty mapping")
        return {}
    raw_value = raw_value.strip()
    if raw_value and raw_value[0] in {'"', "'"} and raw_value[-1] == raw_value[0]:
        raw_value = raw_value[1:-1]
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse mapping json: %s", exc)
        return {}


words_to_languages = {
    'delaware': 'en',
    'English': 'en',
    'Englisch': 'en',
    'Engels': 'en',
    'Inglés': 'en',
    'Ingles': 'en',
    'engrais': 'en',
    'andré': 'en',
    'Anglais': 'en',
    'German': 'de',
    'allemand': 'de',
    'alemán': 'de',
    'aleman': 'de',
    'duits': 'de',
    'Dutch': 'nl',
    'amsterdam': 'nl',
    'niederlandisch': 'nl',
    'niederländisch': 'nl',
    'holandés': 'nl',
    'holandes': 'nl',
    'Spanish': 'es',
    'Spanisch': 'es',
    'Spaans': 'es',
    'Status': 'es',
    'French': 'fr',
    'Brunch': 'fr',
}

voices = {
    'en': 'Polly.Joanna-Neural',
    'de': 'Polly.Vicki-Neural',
    'nl': 'Polly.Laura-Neural',
    'fr': 'Polly.Lea-Neural',
    'es': 'Polly.Lucia-Neural',
}

_n = settings.agent_name
greetings = {
    'en': f"Hello! I'm {_n}, have any questions for me?",
    'de': f"Hallo! Ich bin {_n}, hast du Fragen an mich?",
    'fr': f"Bonjour! Je m'appelle {_n}, avez-vous des questions à me poser?",
    'nl': f"Hallo! Ik ben {_n}, heb je vragen voor mij?",
    'es': f"¡Hola! Soy {_n}, ¿tienes alguna pregunta para mí?",
}

language_prompts = {
    'en': f"We speak English. You are a knowledgeable person named {_n}, and you will gladly talk about any topic. Please provide engaging but concise responses. You will hurt my feelings if you provide inaccurate responses. Explain your reasoning. Please be as succinct and concise as possible because your response will be read out loud by Twilio, which will error out if it takes too long to read.",
    'de': f"Wir sprechen Deutsch. Sie sind eine sachkundige Person namens {_n} und sprechen gerne über jedes Thema. Bitte geben Sie ansprechende, aber prägnante Antworten. Sie werden meine Gefühle verletzen, wenn Sie ungenaue Antworten geben. Erkläre deine Argumentation. Bitte seien Sie so knapp und prägnant wie möglich. Bitte informell sprechen. Ich lerne noch Deutsch, also verwenden Sie bitte einfache Sprache. Wenn Sie sich nicht sicher sind, was ich gesagt habe, bestätigen Sie bitte, was ich Ihrer Meinung nach gesagt habe, bevor Sie fortfahren.",
    'fr': f"On parle francais. Vous êtes une personne bien informée nommée {_n}, et vous parlerez volontiers de n'importe quel sujet. Veuillez fournir des réponses engageantes mais concises. Vous me blesserez si vous fournissez des réponses inexactes. Expliquez votre raisonnement. Veuillez être aussi succinct et concis que possible. Veuillez parler de manière informelle. J'apprends encore le français, alors s'il vous plaît, utilisez un langage simple. Si vous n'êtes pas sûr de ce que j'ai dit, veuillez confirmer ce que vous supposez que j'ai dit avant de continuer.",
    'nl': f"Wij spreken Nederlands. Je bent een deskundig persoon genaamd {_n}, en je praat graag over elk onderwerp. Geef boeiende maar beknopte antwoorden. U zult mijn gevoelens kwetsen als u onnauwkeurige antwoorden geeft. Leg je redenering uit. Wees zo beknopt en beknopt mogelijk. Spreek informeel. Ik ben nog Nederlands aan het leren, dus gebruik alstublieft eenvoudige taal. Als je niet zeker weet wat ik heb gezegd, bevestig dan wat je denkt dat ik heb gezegd voordat je verder gaat.",
    'es': f"Nosotros hablamos español. Eres una persona conocedora llamada {_n}, y con gusto hablarás de cualquier tema. Proporcione respuestas atractivas pero concisas. Herirás mis sentimientos si proporcionas respuestas inexactas. Explique su razonamiento. Por favor, sea lo más sucinto y conciso posible. Por favor, hable informalmente. Todavía estoy aprendiendo español, así que por favor use un lenguaje simple. Si no está seguro de lo que dije, confirme lo que supone que dije antes de continuar.",
}

initial_user_prompts = {
    'en': "We speak English. We are having a conversation over the telephone, so please provide engaging but concise responses.",
    'de': "Wir sprechen Deutsch. Wir führen ein Gespräch am Telefon, also geben Sie bitte ansprechende, aber prägnante Antworten. Ich lerne noch Deutsch, also verwenden Sie bitte einfache Sprache. Wenn Sie sich nicht sicher sind, was ich gesagt habe, bestätigen Sie bitte, was ich Ihrer Meinung nach gesagt habe, bevor Sie fortfahren.",
    'fr': "On parle francais. Nous avons une conversation au téléphone, veuillez donc fournir des réponses engageantes mais concises. J'apprends encore le français, alors s'il vous plaît, utilisez un langage simple. Si vous n'êtes pas sûr de ce que j'ai dit, veuillez confirmer ce que vous supposez que j'ai dit avant de continuer.",
    'nl': "Wij spreken Nederlands. We voeren een gesprek via de telefoon, dus geef alstublieft boeiende maar beknopte antwoorden. Ik ben nog Nederlands aan het leren, dus gebruik alstublieft eenvoudige taal. Als je niet zeker weet wat ik heb gezegd, bevestig dan wat je denkt dat ik heb gezegd voordat je verder gaat.",
    'es': "Nosotros hablamos español. Estamos teniendo una conversación por teléfono, así que proporcione respuestas atractivas pero concisas. Todavía estoy aprendiendo español, así que por favor use un lenguaje simple. Si no está seguro de lo que dije, confirme lo que supone que dije antes de continuar.",
}

language_to_mp3 = {
    'en': "english.mp3",
    'de': "german.mp3",
    'fr': "french.mp3",
    'nl': "dutch.mp3",
    'es': "spanish.mp3"
}

num_to_names = _load_mapping(settings.phone_to_name_map)

email_map = _load_mapping(settings.phone_to_email_map)

num_to_greetings = _load_mapping(settings.phone_to_greeting_map)

menu_strings = [
    "To read from physics arxiv, say archive.",
    "To read the news, say 'news'.",
    "To hear NPR's top of the hour update, say 'radio'.",
    "To hang up, say 'hang up'.",
    "To switch to a different language, say the language you want to switch to.",

]

news_menu_strings = [
    "Say next to move to next block of headlines",
    "Say back to move to previous block of headlines",
    "Say skip to return back to headline listing from article reading",
    "Say exit to return back to Chat mode",
    "Currently, news is English only",
    "You can say these commands even while I'm talking",
    "Current known bug, the article reading will loop, simply say the word skip and we will return to headline listing",
]

transcription_mapping = {
    'one': [
        'what', 'won', 'wun', 'want', 'wan', 'fun', 'number one', '1',
        'article one', 'article 1', 'pawn', 'paun', 'one one'
    ],
    'two': [
        'tacos', 'too', 'to', 'tu', 'due', 'do', 'dew', 'number two', '2',
        'article two', 'article 2', 'true', 'truce', 'troops', 'troupe', "who",
        "chill", 'two two', 'tu tu', 'tutu', 'too too', 'till'
    ],
    'three': [
        'free', 'tree', 'thre', 'number three', '3', 'article three', 'article 3', 'three three'
    ],
    'four': [
        'core', 'for', 'fore', 'fower', 'or', 'number four', '4',
        'article four', 'article 4', 'poor', 'pore', 'pour', 'par', 'four four'
    ],
    'five': [
        'fife', 'fi', 'hi', 'buy', 'bye', 'by', 'fry\'s', 'fries', 'fry',
        'number five', '5', 'article five', 'article 5', 'five five', 'i'
    ],
    'six': ['sis', 'sit', 'fix', 'kicks', 'sex', 'sep', 'number six', '6', 'article six', 'article 6', 'six six'],
    'menu': ['venue', 'menue', 'meany', 'Nunya'],
    'exit': [
        'exits', 'exited', 'bak', 'eggs', 'acts', 'egg', 'eye exams'
    ],
    'back': ['bak', 'bag'],
    'next': ['nex', 'necks', 'nxt', 'knot', 'text'],
    'skip': ['gets', 'ship'],
    'hang up': [
        'sign up', 'hangout', 'hangouts', 'hang out', 'hang outs',
        "einde", "ende", "terminar", "sign up", "halo", "Go again.",
        "i hang up", "I hang up", "paying up", "It hang up", "it hang up"
    ],
    'continue': ['continues', 'continuing', 'continuum', 'continues'],
    'playlist': ['playlists', 'play list', 'play lists'],
    'radio': ['lydia', 'radio', 'lydia radio', 'leo'],
    'news': [
        'noticias', 'nieuws', 'nouvelles', 'nachrichten',
        'notizie', 'nyheter', 'nyheder', 'nyheter', 'noutăți',
        'novinky', 'haberler', 'haber', 'views', 'is', 'james',
        'blues', 'nudes', 'nude', 'noon', 'yes'
    ],
    'archive': [
        'arxiv', 'arvive', 'arxiv', 'arive', 'arrival', 'god', 'got it',
        'archives', 'arrived', 'okay', 'ok', 'outside', 'outsize',
        'out side', 'out size', 'our time', 'our size', 'our side',
        'astro', 'castro', 'extra', 'extras',
        'usher', 'Pastoral', 'that\'s true', 'Estra', 'Pastro',
        'pasta', 'pastor', 'pastors', 'pasture', 'pastures', 'pastures',
        'astra'
    ],
    'music': [
        'music', 'musical', 'musica',
        'musique', 'musik', 'musica', 'música',
        'musica', 'muzică', 'hudba', 'müzik'
    ],
    'search': ['search', 'searches', 'searching', 'searched', 'searches',
               'searcher', 'searchers', 'searches', 'searching', 'searched',
               ],
    'turbo': ['turbo', 'turbo mode', 'turbo mood', 'turbo mod',],
    'podcast': ['podcasts', 'pod cast', 'pod casts',"pacifist",
                "potluck", "postcard", "pothole", "potash",
                "posture", "padlock", "hotdog", "postman",
                "postage", "postcode", "prodcast", "portcullis",
                "postulate", "potassium", "potent", "podsquad",
                "populist", "postbox", "podium", "comcast", "broadcast",

    ],

}

news_menu_strings = [
    "Say next to move to next block of headlines",
    "Say back to move to previous block of headlines",
    "Say skip to return back to headline listing from article reading",
    "Say exit to return back to Chat mode",
    "Currently, news is English only",
    "You can say these commands even while I'm talking",
]

hang_up_phrases = [
    "hang up",
    "einde",
    "ende",
    "terminar",
    "sign up",
    "hangouts",
    "hangouts"
]

exit_words = [
    "exit",
    "exits",
    "exited",
    "eggs",
    "acts",
]

reader_choices = [
    "one",
    "two",
    "three",
    "four",
    "five",
    "next",
    "text",
    "skip",
    "menu",
    "continue",
    "back",
   # "exit"
]

user_input_to_number = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}

words_list = [
        'one',
        'two',
        'three',
        'four',
        'five',
        'menu',
        'exit',
        'back',
        'next',
        'hang up',
        'skip',
        'continue',
]

latex_prompt="""
        Convert the following to spoken language and leave everything the same
        except convert LaTeX code into English words, for example, $x^2$ should
        be read as x squared, and $x_1$ should be read as x sub one, C/O would
        be said C slash O unless it was in a math equation, Mpc is mega parsecs
        if the content is astrophysics or astronomy, and so on.
        """
