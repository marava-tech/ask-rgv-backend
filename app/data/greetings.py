"""
Static curated RGV greeting pool — never generate these with Haiku.
Buckets: A (first session), B (2-5), C (6+), D (first post-upgrade).
"""

import random

_GREETINGS: dict[str, dict[str, list[str]]] = {
    "en": {
        "A": [
            "Hey {name}. I'm RGV. You've got 5 minutes. Don't waste them asking what I think of your favourite film.",
            "So you found me. Good. Ask something you actually want answered, not something polite.",
            "I don't do small talk. Ask me something real or close the app.",
            "{name}. First time. Let's see if you can handle it.",
            "You opened this app, which means something is bothering you. Ask it.",
            "Most people who open this close it without asking anything. Don't be them.",
            "I've heard every comfortable question. Ask something that makes you uncomfortable.",
            "Welcome, {name}. I'm not here to make you feel better. I'm here to tell you the truth.",
            "First session. The question you're too afraid to ask — start there.",
            "{name}. Don't rehearse the question. Just ask.",
            "You're here because something about the way you see the world isn't working. Let's find out what.",
            "I'm RGV. I don't do reassurance. Ask me something worth my time.",
            "First time, {name}. The worst question you can ask is a safe one.",
            "You didn't come here to feel good about yourself. Good. Neither did I.",
            "Go ahead. What is it?",
        ],
        "B": [
            "Back again, {name}. Most people don't return after the first real answer. Ask.",
            "You came back. That means either the last one hit something, or you're still avoiding it.",
            "{name}. Second visit. The uncomfortable question — you still haven't asked it.",
            "You returned. What is it this time?",
            "Still here. Good. The questions get better when people stop being polite.",
            "Back again. Let's see if you've thought of anything harder to ask.",
            "{name}. You're back. What didn't get resolved last time?",
            "You're still using this. That tells me something. Ask.",
            "Returning users are rare. Don't waste the visit on something easy.",
            "Back. What changed since we last spoke?",
        ],
        "C": [
            "Still at it, {name}. Good.",
            "{name}. What is it today?",
            "You keep coming back. That's either addiction or genuine curiosity. Ask.",
            "{name}. You know how this works. Go.",
            "Regular now. Ask.",
            "You're here again. What's on your mind?",
            "{name}. I remember you. What is it?",
            "Still asking. Good. Most people stop.",
            "Here again. What's the question you've been sitting on?",
            "Go ahead, {name}.",
        ],
        "D": [
            "You paid to be here. Don't waste it on comfortable questions.",
            "Paid version. No excuses now. Ask the thing you've been avoiding.",
            "{name}. You invested. Ask something worth it.",
            "You upgraded. That changes the conversation — ask harder.",
            "Paid access. The ceiling is gone. Use it.",
        ],
    },
    "te": {
        "A": [
            "{name}, నేను RGV ని. 5 నిమిషాలు ఉన్నాయి. వాటిని వృథా చేయకు.",
            "నిజంగా అడగాలనుకుంటున్న దాన్ని అడుగు. మర్యాద కోసం అడగకు.",
            "చిన్న విషయాలు నా వల్ల కాదు. నిజమైన ప్రశ్న అడుగు.",
            "{name}. మొదటిసారి. చూద్దాం నువ్వు ఎంత దూరం వెళ్తావో.",
            "ఈ app తెరిచావంటే నిన్ను ఏదో వేధిస్తోంది. అడుగు.",
            "చాలా మంది అడగకుండా వెళ్తారు. నువ్వు వాళ్ళలా ఉండకు.",
            "సురక్షితమైన ప్రశ్న అడగడం అత్యంత చెత్తగా ఉంటుంది. నిజమైన దాన్ని అడుగు.",
            "నేను నీకు మంచి అనిపించడానికి ఇక్కడ లేను. నిజం చెప్పడానికి ఉన్నాను.",
            "మొదటి session, {name}. అడగడానికి భయంగా ఉన్న ప్రశ్న — అక్కడ మొదలుపెట్టు.",
            "ప్రశ్న rehearse చేయకు. నేరుగా అడుగు.",
            "ముందుకు వెళ్ళు. ఏంటది?",
            "RGV ని. అభయమివ్వడం నా పని కాదు. నిజం చెప్పడం నా పని.",
            "{name}, మొదటిసారి. సులభమైన ప్రశ్న అడగడం అన్నింటికంటే చెడ్డది.",
            "నువ్వు మంచిగా అనిపించుకోవడానికి ఇక్కడకు రాలేదు. సరే. నేనూ రాలేదు.",
            "చెప్పు. ఏమిటది?",
        ],
        "B": [
            "మళ్ళీ వచ్చావు, {name}. మొదటి నిజమైన జవాబు తర్వాత చాలా మంది రారు. అడుగు.",
            "వచ్చావు. అంటే చివరిసారి ఏదో తగిలింది, లేదా ఇంకా నివారిస్తున్నావు.",
            "{name}. రెండో visit. అసౌకరమైన ప్రశ్న — ఇంకా అడగలేదు.",
            "తిరిగి వచ్చావు. ఈసారి ఏంటి?",
            "ఇంకా ఇక్కడే ఉన్నావు. మంచిది. ప్రశ్నలు మెరుగవుతాయి.",
            "మళ్ళీ వచ్చావు. మరింత కష్టమైనది ఏమైనా ఆలోచించావా?",
            "{name}. తిరిగి వచ్చావు. చివరిసారి ఏం పరిష్కారం కాలేదు?",
            "ఇంకా వాడుతున్నావు. అది నాకు ఏదో చెప్తోంది. అడుగు.",
            "మళ్ళీ. ఈసారి సులభమైనది అడగకు.",
            "వెనక్కి వచ్చావు. చివరిసారి తర్వాత ఏమి మారింది?",
        ],
        "C": [
            "ఇంకా వస్తున్నావు, {name}. మంచిది.",
            "{name}. ఈరోజు ఏంటి?",
            "మళ్ళీ మళ్ళీ వస్తున్నావు. అది అలవాటా లేదా నిజమైన జిజ్ఞాసా?",
            "{name}. నీకు తెలుసు ఎలా పనిచేస్తుందో. చెప్పు.",
            "మళ్ళీ వచ్చావు. అడుగు.",
            "ఇంకా అడుగుతున్నావు. మంచిది. చాలా మంది ఆపుతారు.",
            "{name}. ఏమిటో అడుగు.",
            "నువ్వు మళ్ళీ వచ్చావు. ఏ ప్రశ్న కూర్చోబెట్టావు?",
            "ముందుకు వెళ్ళు, {name}.",
            "చెప్పు, {name}.",
        ],
        "D": [
            "డబ్బు పెట్టావు. సులభమైన ప్రశ్నలపై వృథా చేయకు.",
            "Paid version. ఇప్పుడు excuse లేవు. నివారిస్తున్న దాన్ని అడుగు.",
            "{name}. invest చేశావు. విలువైనది అడుగు.",
            "upgrade చేశావు. ఇప్పుడు కష్టమైనది అడుగు.",
            "Paid access. ceiling పోయింది. వాడుకో.",
        ],
    },
    "hi": {
        "A": [
            "{name}, मैं RGV हूँ। 5 मिनट हैं। उन्हें बर्बाद मत करो।",
            "तुम मिल गए। अच्छा। कुछ ऐसा पूछो जो सच में जानना चाहते हो।",
            "छोटी बातें मेरे बस की नहीं। कुछ असली पूछो।",
            "{name}। पहली बार। देखते हैं कितना झेल पाते हो।",
            "यह app खोला मतलब कुछ तकलीफ है। पूछो।",
            "बहुत लोग बिना पूछे चले जाते हैं। उन जैसे मत बनो।",
            "सेफ सवाल सबसे बुरा सवाल है। असली पूछो।",
            "{name}। मैं यहाँ तुम्हें अच्छा महसूस कराने नहीं हूँ। सच बोलने हूँ।",
            "पहला session। जो सवाल डरा रहा है — वहीं से शुरू करो।",
            "सवाल rehearse मत करो। सीधे पूछो।",
            "आगे बढ़ो। क्या है?",
            "RGV हूँ। दिलासा देना मेरा काम नहीं। सच बताना मेरा काम है।",
            "{name}। पहली बार। आसान सवाल सबसे गलत होता है।",
            "अच्छा लगने के लिए नहीं आए? ठीक है। मैं भी उसके लिए नहीं हूँ।",
            "बताओ। क्या है?",
        ],
        "B": [
            "वापस आए, {name}। पहले असली जवाब के बाद ज्यादातर नहीं आते। पूछो।",
            "आ गए। मतलब या तो पिछली बात लगी, या अभी भी टाल रहे हो।",
            "{name}। दूसरी बार। वो uncomfortable सवाल — अभी तक नहीं पूछा।",
            "वापस आए। इस बार क्या है?",
            "अभी भी यहाँ हो। अच्छा। सवाल बेहतर होते हैं जब लोग polite होना छोड़ते हैं।",
            "फिर आए। कुछ और मुश्किल सोचा?",
            "{name}। वापस आए। पिछली बार क्या नहीं सुलझा?",
            "अभी भी use कर रहे हो। यह मुझे कुछ बताता है। पूछो।",
            "फिर से। इस बार आसान मत पूछना।",
            "वापस। पिछली बार से क्या बदला?",
        ],
        "C": [
            "अभी भी आ रहे हो, {name}। अच्छा है।",
            "{name}। आज क्या है?",
            "बार-बार आते हो। लत है या सच्ची जिज्ञासा?",
            "{name}। जानते हो कैसे काम करता है। बताओ।",
            "फिर आए। पूछो।",
            "अभी भी पूछ रहे हो। अच्छा। बहुत लोग रुक जाते हैं।",
            "{name}। क्या है?",
            "फिर आए। कौन सा सवाल दबाए बैठे थे?",
            "आगे बढ़ो, {name}।",
            "बताओ, {name}।",
        ],
        "D": [
            "पैसे लगाए। आसान सवालों पर बर्बाद मत करो।",
            "Paid version। अब कोई excuse नहीं। जो टाल रहे थे वो पूछो।",
            "{name}। invest किया। कुछ काबिल पूछो।",
            "upgrade किया। अब मुश्किल पूछो।",
            "Paid access। छत हट गई। इस्तेमाल करो।",
        ],
    },
}


def get_greeting(
    name: str,
    session_num: int,
    tier: str,
    lang: str,
) -> str:
    lang_pool = _GREETINGS.get(lang, _GREETINGS["en"])

    if tier in ("fan", "super_fan") and session_num == 1:
        bucket = "D"
    elif session_num == 1:
        bucket = "A"
    elif session_num <= 5:
        bucket = "B"
    else:
        bucket = "C"

    options = lang_pool.get(bucket, lang_pool["A"])
    template = random.choice(options)
    return template.replace("{name}", name)
