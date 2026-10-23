# -*- coding: utf-8 -*-
"""مكتبة التعليقات السياقية للكريبتو: كشف موضوع التغريدة + قوالب spintax عربية وإنجليزية.

كشف الموضوع يتم بالبحث عن كلمات مفتاحية (عربية + إنجليزية) في نص التغريدة،
ثم اختيار قوالب التعليقات الخاصة بالمواضيع المكتشفة - كله محلي بدون إنترنت.
القوالب الإنجليزية (EN_POOLS) أغلبها يحمل رمز العملة بصيغة ${TICKER} ليُخرج
تعليقات مثل «$EPIC is about to take off 🚀» بأسلوب حسابات الإشارات.
"""
import re

# ترتيب الأولوية: المواضيع المحددة أولاً (كلما كان الموضوع أدق كان أفضل)
TOPIC_PRIORITY = [
    "btc", "eth", "alt", "token_pick", "nft_web3", "defi", "exchange",
    "pump_signal", "profit_take", "ta_pattern",
    "buy", "hold", "advice", "analysis", "news", "bull", "bear", "hype",
    "crypto_general",
]

# تم إزالة أي إشارة للـ scam أو النص المتعلق بالاحتيال بناءً على طلبك
KEYWORDS = {
    "bull": [
        "صعود", "ارتفاع", "اختراق", "قمة", "رالي", "قفزة", "قمر", "إلى القمر", "فومو", "لونق",
        "ath", "rally", "pump", "bullish", "all time high", "green", "fomo", "long",
        "rise", "rising", "surge", "soar",
        "meteoric", "moon", "moonshot", "skyrocket", "🚀", "📈",
    ],
    "bear": [
        "هبوط", "انخفاض", "انهيار", "تصحيح", "تراجع", "كسر", "دمب", "شورت", "فاد",
        "dip", "crash", "dump", "bearish", "red", "correction", "fud", "short", "📉",
    ],
    "gem": [
        "💎", "جوهرة", "ماسة", "كنز", "نادرة", "ميم كوين",
        "gem", "diamond", "100x", "10x", "low cap",
    ],
    "buy": [
        "شراء", "فرصة", "تجميع", "دخول", "سعر مناسب", "متوسط", "تعزيز",
        "buy", "accumulate", "entry", "dca", "load up", "bag",
    ],
    "hold": [
        "استثمار", "طويل الأجل", "صبر", "تراكم", "ادخار", "محفظة", "تمسك",
        "hold", "hodl", "long term", "stack", "diamond hands",
    ],
    "analysis": [
        "تحليل", "شارت", "رسم بياني", "دعم", "مقاومة", "مؤشر", "سيناريو", "فوليوم", "سيولة",
        "analysis", "chart", "support", "resistance", "indicator", "ta", "volume", "liquidity",
    ],
    "news": [
        "خبر", "إعلان", "إطلاق", "شراكة", "تحديث", "تطور", "منصة",
        "news", "launch", "partnership", "update", "announcement",
    ],
    "btc": [
        "بيتكوين", "البيتكوين", "bitcoin", "btc", "ساتوشي", "ملك",
    ],
    "eth": [
        "إيثيريوم", "الإيثيريوم", "ethereum", "eth", "إيثر", "ايثريوم",
    ],
    "alt": [
        "عملات بديلة", "الآلتس", "التيرا", "سولانا", "كاردانو", "بينانس كوين",
        "altcoin", "alts", "solana", "cardano", "bnb", "xrp", "ريبل", "sol",
    ],
    "nft_web3": [
        "إن إف تي", "ويب 3", "ميتافيرس", "nft", "web3", "metaverse",
    ],
    "defi": [
        "ديفاي", "سيولة", "عائد", "ستيكينغ", "defi", "yield", "staking", "liquidity", "farming",
    ],
    "exchange": [
        "تبادل", "منصة", "بينانس", "كوين بيس", "إدراج", "قائمة", "كوكوين",
        "exchange", "binance", "coinbase", "okx", "kucoin", "bybit", "bitget", "mexc", "gate", "listing",
    ],
    "pump_signal": [
        "pump", "meteoric", "moon", "skyrocket", "take off", "takeoff",
        "strong move", "big move", "explode", "explosive", "breakout",
        "ضخ", "قنبلة", "صاروخ", "انطلاقة", "انفجار",
    ],
    "profit_take": [
        "profit", "profits", "target", "targets", "gains", "take profit", "tp",
        "secure some profits", "upside", "potential",
        "ربح", "أرباح", "هدف", "جني", "توصية", "أهداف",
    ],
    "token_pick": [
        "coin", "coins", "token", "gem", "ticker", "pick", "call", "signal",
        "كوين", "عملة", "رمز", "توصية", "إشارة", "مشروع",
    ],
    "ta_pattern": [
        "trendline", "trend line", "downtrend", "uptrend", "upward trend", "downward trend",
        "retest", "retested", "rsi", "macd", "ema",
        "breakout", "broken", "stop loss", "stoploss", "entry", "chart pattern",
        "line was broken", "resistance", "support",
        "كسر", "خط الاتجاه", "إعادة اختبار", "وقف الخسارة", "دخول", "نموذج", "ترند",
    ],
    "hype": [
        "get ready", "ready", "let's go", "lets go", "soon", "coming",
        "🚀", "💎", "👀", "💪", "🔥", "حيتان",
        "استعدوا", "يلا", "قريباً", "حماس",
    ],
    "advice": [
        "نصيحة", "تحذير", "انتبه", "احذر", "تنبيه", "مخاطرة",
        "advice", "warning", "risk", "careful",
    ],
    "crypto_general": [
        "كريبتو", "عملات رقمية", "عملة رقمية", "السوق", "سوق العملات", "الرقمية", "بلوكتشين",
        "crypto", "coin", "coins", "market", "token", "الترميز", "blockchain",
    ],
}

# قوالب عربية (spintax) لكل موضوع (بدون أي محتوى تخص الاحتيال أو السكام)
DEFAULT_POOLS = {
    "bull": [
        "ما شاء الله على هذا الصعود {القوي|الجميل|الواضح}",
        "الشارت {يشتغل|يتحرك} حلو هالأيام، صعود {مميز|كبير}",
        "{قمة جديدة|قمة تاريخية}؟ الله يبارك {للمتابعين|للسوق}",
        "هذا الارتفاع {مبشر|جميل} {للمدى الطويل|وللسوق}",
        "صعود واضح، {نتمنى|الله يتمم} {الاستمرارية|الاستمرار}",
        "ماشاء الله، {المؤشرات|الإشارات} كلها {خضراء|صاعدة}",
        "${TICKER} إلى القمر 🚀، {الله يبارك|روعة}",
        "ماشاء الله على ${TICKER}، {اختيار موفق|قرار صائب}",
        "${TICKER} {صار حديث السوق|بيطلع بقوة}، صعود {مميز|واضح}",
        "${TICKER} {مبشر|واعد}، {نراقبه عن قرب|نرجع له}",
        "${TICKER} {شغال|ماشي} {زي الفل|بقوة} هالأيام",
        "الحركة {قوية|مثيرة} جداً، {الزخم|الاتجاه} صاعد وواضح",
        "تحليلك {دقيق|مفيد}، الصعود {متوقع|محتم}",
        "{ممتع|رائع} متابعة {السعر|التطور} بهذه السرعة، {الله يحفظ|أحسنت}",
        "الأسعار الخضراء بكل مكان، {إشارة|علامة} قوية للأعلى",
    ],
    "bear": [
        "التصحيح {طبيعي|متوقع} بعد هالصعود {الكبير|الطويل}",
        "هبوط {مؤقت|محدود} إن شاء الله، السوق بيعيش دورات",
        "التراجع الحالي {فرصة تجميع|فرصة للمتابعين} لمن يخطط {طويلاً|بعيداً}",
        "الأسواق هيك، صعود وهبوط، المهم {الخطة|الاستراتيجية}",
        "نزول {متوقع|مفاجئ} بس {التحليل|الرؤية} واضحة",
        "خليكم هادئين، التصحيحات {جزء من السوق|طبيعية}",
    ],
    "gem": [
        "جوهرة حقيقية {هالعملة|هالرمز}، {متابعة|ملاحظة} موفقة",
        "💎 {رائع|مميز} فعلاً، القليل {يلاحظ|يشوف} {هالقيمة|هالمستوى}",
        "{كنز|تحفة} من العملات، {التحليل|القراءة} {في محله|مضبوط}",
        "ماسة نادرة {بين العملات|بهالسوق}، تسلم على {التنبيه|المعلومة}",
        "{اكتشاف|اختيار} ذكي، هيك العملات {تستاهل|تستحق} الاهتمام",
    ],
    "buy": [
        "فرصة شراء {ممتازة|مناسبة} {بهالمنطقة|بهالأسعار}",
        "{دخلت|دخلنا} من هالمنطقة، {القرار|التوقيت} حلو",
        "الأسعار الحالية {مغرية|مناسبة} للي بيفكر {بالمدى البعيد|بالمدى الطويل}",
        "نقطة دخول {جيدة|ممتازة} لمن يتابع التحليل",
        "التجميع عند هذه المستويات {قرار حكيم|خطة ذكية}",
        "هالمنطقة {سعر عادل|منطقة اهتمام} لكثير من المحللين",
    ],
    "hold": [
        "الاستثمار الطويل {يغلب|يتفوق} على التداول السريع، وجهة نظر محترمة",
        "الصبر {مفتاح|سر} النجاح في عالم الكريبتو",
        "التراكم التدريجي {خطة|استراتيجية} ناجحة، تسلم",
        "اللي بيمسك محفظته بثبات بيلاقي النتيجة، اتفق معك",
        "الاستمرارية والصبر {هم الأساس|الأهم} في هذا السوق",
        "محفظة هادئة ويد ثابتة، هذا {أسلوب|نهج} ناجح",
    ],
    "analysis": [
        "تحليل {واضح|مرتب} ومبني على أرقام، يعطيك العافية",
        "مستويات الدعم والمقاومة {محددة بدقة|واضحة}، شغل محترف",
        "قراءة {ذكية|موضوعية} للشارت، استفدت منها",
        "التحليل {شامل|دقيق} خصوصاً نقاط الدعم، يسلم إيديك",
        "السيناريوهات مطروحة {بوضوح|بشفافية}، يعطيك العافية",
        "رسم وتوضيح {ممتاز|جميل}، صار أسهل واحد يقرأ الشارت",
        "تحليل {تفصيلي|عميق}، {الدعم والمقاومة|الترند} واضح جداً",
        "{مرجع|مصدر} موثوق للتحليل، بحط {عينك|انتباهك} عليك",
        "الشارت {يتحدث|ينطق} بوضوح بفضل تحليلك، {شكراً|أحسنت}",
        "مؤشرات التحليل كلها تشير لـ {نفس الاتجاه|نفس الرأي}",
    ],
    "news": [
        "خبر {مهم|كبير}، الله يوفقكم في تغطية كل جديد",
        "أول واحد يوصلنا {بالأخبار|بالتحديثات}، مشكور",
        "تطورات {مثيرة|مهمة} هالأيام، نتابع معك",
        "شكراً على {التغطية|التحليل} السريع للخبر",
        "أخبار حلوة للسوق، يعطيك العافية على المتابعة",
        "إعلان {مفاجئ|متوقع} والتحليل بعده مباشرة، شغل مميز",
    ],
    "btc": [
        "بيتكوين {الأم|الأصل} ما بتخيب، وجهة نظر محترمة",
        "تحرك البيتكوين {هو المفتاح|يحكم السوق} كله",
        "البيتكوين {يتصدر|يقود} السوق {كالعادة|دائماً}",
        "متابعة البيتكوين {ممتعة|مهمة} دائماً، يعطيك العافية",
        "أداء البيتكوين {واضح|قوي} هالفترة",
    ],
    "eth": [
        "إيثيريوم {منافس قوي|الخيار الثاني الأذكى} في السوق",
        "حركة الإيثيريوم {تشد الانتباه|مميزة} هالأيام",
        "الإيثيريوم ومنظومته {مستقبل|خيار} مهم، تسلم",
        "تحليل الإيثيريوم {دقيق|مفيد}، استفدت منه",
        "متابعة الإيثيريوم {متعة|فائدة} حقيقية، يعطيك العافية",
    ],
    "alt": [
        "العملات البديلة {تعطي فرص|عندها حركة} مميزة هالفترة",
        "تنويع المحفظة بين العملات البديلة {خيار|فكرة} حكيمة",
        "متابعة حركة الآلتس {ممتعة|مهمة} مع تحليلك",
        "بعض الآلتس {تسبق|تتفوق} على السوق، كلامك صحيح",
        "اختيار العملات البديلة المناسبة {مهارة|علم}، تسلم",
    ],
    "nft_web3": [
        "عالم الويب 3 {مليان فرص|واسع} ومثير للاهتمام",
        "الـ NFT والميتافيرس {مستقبل|اتجاه} مهم جداً",
        "الويب 3 {يغير اللعبة|يحمل الكثير}، نتابع معك",
        "الاهتمام بالمشاريع الجديدة {مهم|ضروري}، يعطيك العافية",
        "المشاريع الناشئة في الويب 3 {تستاهل المتابعة|تستحق الاهتمام}",
    ],
    "defi": [
        "منتجات الديفاي {تتطور|بتتوسع} بسرعة، مهم متابعتها",
        "العوائد والستيكينغ {صاروا|أصبحوا} جزء أساسي من السوق",
        "السيولة في الديفاي {العمود الفقري|أساس} للمنظومة",
        "فرص الديفاي كثيرة والتحليل {يساعد|يوجه} كثير",
        "التمويل اللامركزي {مستقبل|خيار} حقيقي للقطاع، تسلم",
    ],
    "exchange": [
        "الإدراج بمنصة كبيرة {خبر ممتاز|نقلة نوعية} لأي مشروع",
        "المنصات الكبرى {تضيف ثقة|تعطي زخم} للسوق",
        "متابعة إدراجات المنصات {مهمة|ضرورية} لكل متداول",
        "قرار الإدراج هذا {يغير اللعبة|مهم جداً}، تسلم عالخبر",
        "التداول عبر منصات موثوقة {خطوة|خيار} صحيح، شكراً على المعلومة",
    ],
    "advice": [
        "نصيحة مهمة، {السلامة أولاً|إدارة المخاطر أولاً} في هذا السوق",
        "كلام {في محله|صائب}، الحذر مطلوب بهالأسواق",
        "إدارة المخاطر {أهم درس|أساس النجاح}، شكراً على التنبيه",
        "فعلاً كثير ناس {يحتاجون|بيحتاجوا} يسمعوا هالكلام",
        "نصائح ذهبية، بوركت",
    ],
    "crypto_general": [
        "تحليل {ممتع|مفيد} كالعادة، يعطيك العافية",
        "السوق {ديناميكي|متغير} والرؤية واضحة عندك، تسلم",
        "متابعتك للسوق {مميزة|مستمرة}، نستفيد دائماً",
        "كلام {في الصميم|مضبوط} عن السوق، شكراً",
        "محتوى كريبتو {رائع|قيم} كالعادة، استمر",
        "واضح أنك {متابع|فاهم} كل تفاصيل السوق، يعطيك العافية",
        "{اتجاهك|رؤيتك} للسوق {منطقي|معقول}، نشاركك الرأي",
        "المتابعة {دقيقة|حثيثة}، واضح الخبرة في {التحليل|القراءة}",
        "كل {تغريدة|منشور} تعطينا {رؤية|معلومة} جديدة، بوركت",
        "السوق {يحتاج|يستحتاج} ناس مثلك {فاهمين|ملمين} {بالتفاصيل|بالتحليل}",
    ],
    "pump_signal": [
        "ضخ قوي متوقع، الشارت يبشر والله يرزق الجميع",
        "ماشاء الله، الإشارة واضحة والتوقع كبير، نتمنى التوفيق للجميع",
        "من هالمستويات الضخ يكون قوي، نترقب الحركة",
        "إشارة ممتازة، نتمنى للجميع الربح من هالتوصية",
        "هالتحرك مبشر، تسلم على التوصية",
        "قفزة متوقعة وكبيرة، المحللين يتفقون معك",
        "الضخ القادم يبيّن قوة العملة، نتابع",
    ],
    "profit_take": [
        "الأرباح ممتازة هالفترة، ماشاء الله",
        "جني الأرباح عند الهدف قرار حكيم، تسلم",
        "الهدف واضح والطريق إليه ممهد",
        "الوصول للأهداف مستحق، يعطيك العافية",
        "نسبة الربح ممتازة، استمر بالتوصيات",
        "الهدف القادم أكبر بإذن الله، نتابع معك",
    ],
    "token_pick": [
        "العملة مميزة وهالتوصية في محلها",
        "تابعنا العملة من بداية التوصية، أداء طيب",
        "اختيار العملات موفق كالعادة، يعطيك العافية",
        "هالعملة حركتها واضحة، تسلم على الإشارة",
        "العملات اللي تختارها غالباً تضرب، استمر",
        "متابعة توصياتك مفيدة دائماً، شكراً",
        "${TICKER} كان {اختيار موفق|توصية في الوقت المناسب}، {الأداء|الحركة} تحت العين",
        "هالعملة {بتستحق|تستاهل} {المتابعة|الاهتمام}، قرار موفق",
        "${TICKER} من أفضل {الخيارات|الخيارات} {الحالية|هالفترة}",
        "التوصية {ناجحة|موفقة}، {الحركة|السعر} يثبت الكلام",
    ],
    "ta_pattern": [
        "كسر الخط وإعادة الاختبار إشارة قوية، تسلم",
        "التحليل الفني مضبوط خصوصاً مستويات الدخول",
        "وقف الخسارة خطوة صحيحة بهذه الأسواق",
        "إعادة اختبار الكسر دليل على قوة الحركة",
        "قراءة الشارت احترافية، يعطيك العافية",
        "مستويات الدخول والخروج محددة بوضوح، شغل محترف",
    ],
    "hype": [
        "استعدوا، شي كبير جاي",
        "جايين نكسب، انطلقوا",
        "الحماس حلو مع التحليل، نترقب",
        "الترقب ممتع، متابعينك",
        "وصلتنا الرسالة، جهزنا المحافظ",
        "الانتظار طويل والحصاد مؤكد، استمر",
    ],
}

# قوالب إنجليزية (spintax) لكل موضوع
EN_POOLS = {
    "bull": [
        "${TICKER} is {looking so bullish|pumping hard} right now 🚀",
        "${TICKER} keeps {climbing|breaking higher}, love to see it",
        "${TICKER} to the moon 🚀 {let's go|here we go}",
        "${TICKER} {is on fire|is flying} 🔥",
        "What a {strong|clean} uptrend, {loving it|great to watch}",
        "The chart {looks amazing|is screaming green} today 📈",
        "New highs {incoming|loading} 🚀",
    ],
    "bear": [
        "${TICKER} dipping, might be {a good entry|accumulation zone} soon",
        "${TICKER} {retesting|testing} lower support, watching closely",
        "Healthy {correction|pullback}, part of the game",
        "Red candles {today|for now}, hold tight",
        "This dip {won't last|looks temporary} if the trend holds",
        "Corrections are normal, {stay calm|don't panic}",
    ],
    "gem": [
        "${TICKER} is such a {hidden gem|undervalued gem} 💎",
        "What a {find|discovery}, this one is special 💎",
        "${TICKER} flying under the radar, {nice catch|great pick}",
        "People are sleeping on ${TICKER}, {still early|early}",
        "Diamond in the rough 💎 {noted|adding to watchlist}",
        "Gems like this {don't stay hidden|rarely stay cheap} for long",
    ],
    "buy": [
        "${TICKER} at these levels is {a great entry|a steal}",
        "Bought ${TICKER} {in this zone|at these prices}, feeling good",
        "${TICKER} looks {cheap|attractive} right now",
        "Accumulating ${TICKER} {quietly|patiently} here",
        "Great {buy zone|accumulation zone} here",
        "This is {a solid entry point|the right area} to load up",
    ],
    "hold": [
        "Long term is {the way|the play} with ${TICKER}",
        "Holding ${TICKER} strong {since the beginning|since the call}",
        "${TICKER} is a {hold for me|long term hold}",
        "Patience {pays off|is key} in this market",
        "No panic selling, {we hold|staying strong}",
        "The stack grows {daily|weekly}, staying patient",
    ],
    "analysis": [
        "${TICKER} chart {looks clean|is well structured}",
        "${TICKER} {respecting|holding} the trendline nicely",
        "The chart on ${TICKER} {tells a story|looks promising} 📊",
        "Solid TA, {support and resistance|key levels} well marked",
        "Great {analysis|breakdown}, thanks for sharing",
        "Clear levels, {easy to follow|well explained}",
    ],
    "news": [
        "${TICKER} news {is huge|changes everything}",
        "${TICKER} {partnership|listing} incoming, exciting times",
        "Huge {update|launch} for ${TICKER} 🚀",
        "This {news|development} about ${TICKER} is {bullish|very positive}",
        "Big {announcement|update}, market will react",
        "Thanks for {covering|sharing} this {fast|early}",
    ],
    "btc": [
        "$BTC {leading the market|setting the tone} as always",
        "Love watching $BTC {move|run}",
        "$BTC {breaking out|making moves}, market follows",
        "Bitcoin is {the king|the boss} 👑",
        "Bitcoin {never disappoints|keeps delivering}",
    ],
    "eth": [
        "$ETH looking {strong|healthy} on the charts",
        "$ETH {ready to move|about to run}",
        "Love the $ETH {momentum|trend} right now",
        "Ethereum {ecosystem|network} keeps {growing|building}",
        "Ethereum {is the smart play|has strong fundamentals}",
    ],
    "alt": [
        "${TICKER} {moving nicely|ripping} with the alts",
        "${TICKER} leading the {alt rally|alt season} charge",
        "Small caps {printing|moving} today 🔥",
        "Alts season {is coming|is here} 🚀",
        "Alts {are heating up|have so much upside} right now",
        "Diversifying into alts {is the play|looks smart}",
    ],
    "nft_web3": [
        "${TICKER} riding the {web3|metaverse} narrative 🚀",
        "Web3 {is the future|keeps building} 🔥",
        "NFTs {heating up|back in the spotlight} again",
        "The metaverse {story|narrative} is {strong|big}",
        "Web3 projects {solving real problems|building real value}",
        "NFT volume {climbing|rising}, interesting times",
    ],
    "defi": [
        "${TICKER} {flowing|moving} with the DeFi wave 🌊",
        "DeFi yields {looking good|are back} again",
        "Staking rewards {solid|attractive} these days",
        "DeFi {liquidity|TVL} keeps growing",
        "Yield farming {making a comeback|still strong}",
        "DeFi is {the backbone|core} of this market",
    ],
    "exchange": [
        "${TICKER} just got listed, {big move|volume spike} incoming",
        "${TICKER} on major exchanges {soon|incoming} 👀",
        "Listing ${TICKER} on a major CEX = {new buyers|fresh liquidity}",
        "KUCOIN listing = {instant liquidity|game changer} 🚀",
        "Exchange listing {adds trust|is huge} for the project",
        "CEX listings {always pump|move the market}",
    ],
    "pump_signal": [
        "${TICKER} is {about to take off|ready to explode} 🚀",
        "Get ready for ${TICKER} 🚀 {here we go|let's ride}",
        "${TICKER} {looking primed|set up perfectly} for a move",
        "${TICKER} {pumping soon|about to rip} 🔥",
        "Pump {incoming|loading}, get ready 💪",
        "Strong pump {setup|signal} right here",
        "This {looks explosive|is a rocket setup} 💥",
    ],
    "profit_take": [
        "${TICKER} hitting targets {nicely|as expected} 🤑",
        "${TICKER} delivering {profits|gains} as promised",
        "Targets on ${TICKER} {hit|reached}, great call",
        "Taking profits at {the target|these levels} = smart play ✅",
        "Solid gains {secured|banked}, well played",
        "Profit taking {at the top|at the target} is the way",
    ],
    "token_pick": [
        "${TICKER} is {a solid pick|one to watch} 👀",
        "Nice call on ${TICKER}, {following|watching closely}",
        "${TICKER} {has real potential|looks promising} 💎",
        "${TICKER} is {the pick of the week|my favorite right now}",
        "Added ${TICKER} to the watchlist",
        "Good {pick|call}, looks strong",
        "What a {pick|find} 💎",
    ],
    "ta_pattern": [
        "${TICKER} {broke out|broke the trendline} and retested ✅",
        "${TICKER} {respecting|holding} the breakout level",
        "Clean {chart pattern|setup} on ${TICKER}",
        "Downtrend broken on ${TICKER}, {bullish structure|trend reversal} confirmed",
        "Trendline break + retest = {textbook entry|clean setup}",
        "Stop loss on entry, {smart risk management|well played}",
        "The {breakout|break} looks {legit|confirmed}",
    ],
    "advice": [
        "Solid advice, {risk management|safety} first",
        "Good reminder, {stay careful|be careful} out there",
        "Thanks for {the heads up|the warning} 🙏",
        "Wise words, {protecting capital|capital preservation} matters",
    ],
    "hype": [
        "${TICKER} incoming, {get ready|brace yourselves} 👀",
        "Get ready 💪",
        "Let's gooo 🚀",
        "Something big is coming 👀",
        "Wallets ready, {let's go|time to move} 🔥",
        "{So hyped|Hyped} for this 🔥",
        "Big things loading 👀",
    ],
    "crypto_general": [
        "${TICKER} {part of my portfolio|in my watchlist} too",
        "Love the {signals|updates} on ${TICKER}",
        "Solid {post|thread} on ${TICKER}",
        "Great {analysis|content} as always",
        "The market {is moving|is alive} today 📈",
        "Market {action|momentum} looking good",
    ],
}

# كلمات عربية تُزال تشكيلاتها لتوحيد المطابقة
_AR_DIACRITICS = re.compile(r"[\u064B-\u0652\u0670]")
_AR_WORD = re.compile(r"[\u0600-\u06FFa-zA-Z0-9]+")


def _normalize(text):
    return _AR_DIACRITICS.sub("", text or "").lower()


def _tokens(text):
    """تقطيع النص لكلمات، مع إزالة أل التعريف من بداية الكلمات العربية."""
    out = []
    for w in _AR_WORD.findall(text):
        if w.startswith("ال") and len(w) > 4:
            w = w[2:]
        out.append(w)
    return out


# رمز عملة بنمط $TICKER (مثل $EPIC أو $SOL)
_TICKER_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,9})", re.IGNORECASE)
# عملات كبرى لها موضوعها الخاص ولا تُعامل كعملة بديلة
_TICKER_MAJOR = {"BTC", "ETH", "XBT"}


def extract_ticker(text):
    """استخراج رمز العملة $TICKER من نص التغريدة (أو '' إن لم يوجد)."""
    m = _TICKER_RE.search(text or "")
    return m.group(1).upper() if m else ""


def _render_template(tmpl, ticker):
    """استبدال {TICKER} بالرمز الفعلي؛ إن غاب الرمز والقالب يتطلبه -> None."""
    if "{TICKER}" in tmpl:
        if not ticker:
            return None
        return tmpl.replace("{TICKER}", ticker)
    return tmpl


def _kw_match(norm_text, tokens_text, kw):
    """مطابقة كلمة مفتاحية: لاتينية (حدود كلمة + جمع s) أو عربية (تسلسل كلمات)."""
    kw = _normalize(kw)
    if not kw:
        return False
    if re.search(r"[a-z0-9]", kw):
        rx = re.compile(r"(?<![a-z0-9])" + re.escape(kw) + r"s?(?![a-z0-9])")
        return rx.search(norm_text) is not None
    if not re.search(r"[\u0600-\u06FF]", kw):
        return kw in norm_text
    kw_tokens = _tokens(kw)
    if not kw_tokens:
        return False
    for i in range(len(tokens_text) - len(kw_tokens) + 1):
        if tokens_text[i:i + len(kw_tokens)] == kw_tokens:
            return True
    return False


def detect_topics(text):
    """إرجاع قائمة المواضيع المكتشفة في نص التغريدة (حسب الأولوية)."""
    norm = _normalize(text)
    if not norm:
        return []
    tokens = _tokens(norm)
    ticker = extract_ticker(text)
    found = []
    for topic in TOPIC_PRIORITY:
        kws = KEYWORDS.get(topic, [])
        hit = any(_kw_match(norm, tokens, kw) for kw in kws)
        if not hit and topic == "alt" and ticker and ticker not in _TICKER_MAJOR:
            hit = True
        if hit:
            found.append(topic)
            
    if ticker and ticker not in _TICKER_MAJOR and "token_pick" not in found:
        found.append("token_pick")
    return found


def load_crypto_pools_from_json(path):
    """دمج قوالب من ملف JSON قابل للتعديل من المستخدم مع الافتراضيات."""
    import json as _json
    import os as _os

    pools = {k: list(v) for k, v in DEFAULT_POOLS.items()}
    if path and _os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                user_pools = _json.load(f)
            if isinstance(user_pools, dict):
                for k, v in user_pools.items():
                    if isinstance(v, list) and v:
                        pools[k] = [str(x) for x in v if str(x).strip()]
        except Exception:
            pass
    return pools