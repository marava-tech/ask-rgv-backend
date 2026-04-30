import logging
import re

logger = logging.getLogger(__name__)

# Bug #26: original list had ~14 phrases and no romanized variants; expanded with common
# English romanizations and alternate spellings for better safety coverage
CRISIS_KEYWORDS = [
    # English
    "want to die", "kill myself", "end my life", "end it all", "suicide",
    "don't want to live", "no reason to live", "want to end it", "self harm",
    "hurt myself", "better off dead", "take my own life", "can't go on",
    "wanna die", "want to kill myself", "thinking of suicide", "suicidal",
    "going to kill myself", "planning to end", "end my existence",
    "not worth living", "life is not worth", "want to disappear forever",
    # Romanized Telugu
    "atma hatya", "atmahatya", "chanipovadam", "chaniki povadam",
    "naaku chavadam istam", "jeevitam chalichu", "jeevitam modalettu",
    # Romanized Hindi
    "atma hatya karna", "khud ko marna", "suicide karna", "jaan dena",
    "mar jaana chahta", "zindagi khatam karna", "jina nahi chahta",
    "khud ko khatam", "apni jaan lena",
    # Telugu script
    "మిమ్మల్ని చంపుకోవాలనుకుంటున్నాను", "ఆత్మహత్య", "జీవితాన్ని ముగించాలి",
    "చనిపోవాలని", "బతకాలని లేదు",
    # Hindi script
    "खुद को मारना", "आत्महत्या", "जिंदगी खत्म करना",
    "मरना चाहता", "जीना नहीं चाहता",
]

SAFETY_RESPONSE = {
    "en": (
        "I hear you. What you're feeling matters. "
        "Please reach out to iCall right now: 9152987821 (India, free, confidential). "
        "You don't have to face this alone."
    ),
    "te": (
        "నేను మీరు చెప్పింది విన్నాను. మీ భావాలు ముఖ్యమైనవి. "
        "దయచేసి iCall కు ఇప్పుడే కాల్ చేయండి: 9152987821 (India, ఉచిత, రహస్యం). "
        "మీరు ఒంటరిగా ఎదుర్కోవాల్సిన అవసరం లేదు."
    ),
    "hi": (
        "मैंने आपकी बात सुनी। आपकी भावनाएं महत्वपूर्ण हैं। "
        "कृपया अभी iCall से संपर्क करें: 9152987821 (India, मुफ्त, गोपनीय)। "
        "आपको अकेले इसका सामना नहीं करना है।"
    ),
}


def detect_crisis(text: str) -> tuple[bool, str]:
    lower = text.lower()
    for phrase in CRISIS_KEYWORDS:
        # Word-boundary check: phrase must not be embedded inside a longer word.
        # Prevents "chanipovadam" from matching on "chani" substrings and
        # "suicide" from matching only as part of compound romanized tokens.
        pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
        if re.search(pattern, lower):
            return True, phrase
    return False, ""


def get_safety_response(language: str) -> str:
    response = SAFETY_RESPONSE.get(language)
    if response is None:
        # Bug #43: unsupported languages silently fell back with no telemetry — now logged
        logger.warning("[crisis] unsupported language '%s', falling back to English", language)
        return SAFETY_RESPONSE["en"]
    return response
