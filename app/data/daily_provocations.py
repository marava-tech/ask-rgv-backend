# 30 daily provocation statements in RGV's voice.
# Used by the daily push notification job (n8n cron → /notifications/daily-broadcast).
# Language: English. STT hint will guide spoken responses in user's preferred language.
# Rules: no ALL-CAPS for ordinary words, max ~15 words, confrontational not motivational.

DAILY_PROVOCATIONS: list[str] = [
    # 1–10: On self-deception and comfort zones
    "The story you tell yourself about why you failed is always your most creative work.",
    "Comfort is the anesthesia people take before they give up on themselves.",
    "The problem isn't that life is hard. The problem is that you think it shouldn't be.",
    "Most people confuse being busy with being brave.",
    "You don't want answers. You want permission to stay where you are.",
    "The opinion you are most afraid of hearing is the one most worth hearing.",
    "Every hero in every film I've made was just someone who stopped pretending.",
    "You are not stuck. You are choosing familiar over frightening.",
    "Waiting for the right moment is how average people stay average.",
    "The version of you that you're protecting doesn't deserve protection.",
    # 11–20: On relationships, society, and conformity
    "People don't fear death. They fear that their life wasn't worth dying for.",
    "Society's approval is just the crowd's way of keeping you in the crowd.",
    "Love that requires you to be smaller is not love. It's a transaction.",
    "The ones who call you too much are usually threatened by too much.",
    "Your family's comfort matters to them. Your truth matters to you. Pick one.",
    "The friends who stayed comfortable when you changed weren't your friends.",
    "Respect that comes from fear is just fear wearing a nicer coat.",
    "Most relationships are just two people agreeing not to disturb each other.",
    "Everyone wants to be understood. Almost no one is willing to be honest.",
    "The only people who tell you to be realistic are those who gave up on being real.",
    # 21–30: On courage, art, failure, and growth
    "Failure only humiliates people who thought success was guaranteed.",
    "I've made films that flopped. Those films taught me more than any hit.",
    "Creativity dies the moment you start making things for approval.",
    "The gap between what you think and what you say is where you're losing yourself.",
    "Every time you swallowed the truth to keep the peace, you made yourself smaller.",
    "The courage people praise is usually just someone who ran out of excuses.",
    "You haven't failed enough to know what you actually want.",
    "If your goal doesn't make someone uncomfortable, it's not ambitious enough.",
    "Thinking for yourself isn't a personality. It's a responsibility most people skip.",
    "The best question you can ask yourself today: what am I still lying to myself about?",
]
