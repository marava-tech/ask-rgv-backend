CRISIS_KEYWORDS = [
    "want to die", "kill myself", "end my life", "end it all", "suicide",
    "don't want to live", "no reason to live", "want to end it", "self harm",
    "hurt myself", "better off dead", "take my own life", "can't go on",
    "మిమ్మల్ని చంపుకోవాలనుకుంటున్నాను", "ఆత్మహత్య", "జీవితాన్ని ముగించాలి",
    "खुद को मारना", "आत्महत्या", "जिंदगी खत्म करना",
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
        if phrase in lower:
            return True, phrase
    return False, ""


def get_safety_response(language: str) -> str:
    return SAFETY_RESPONSE.get(language, SAFETY_RESPONSE["en"])
