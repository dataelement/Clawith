type SentenceType = "夸奖" | "责怪" | "命令" | "叙事" | "请求" | "未知";

type AckReactionClassifyResult = {
  type: SentenceType;
  emoji: string;
};

type EmojiMap = Record<SentenceType, readonly string[]>;

const CATCHPHRASE = "叽 ";

const KEYWORDS = {
  praise: ["真棒", "太好了", "厉害", "优秀", "聪明", "好样的", "赞", "牛", "完美", "出色", "真行", "干得漂亮", "天才", "棒极了"],
  blame: ["怎么又", "搞砸", "太差了", "烦死了", "讨厌", "笨", "蠢", "马虎", "不负责任", "乱来", "糟糕", "废物", "气死我了", "错了"],
  command: ["必须", "立刻", "马上", "赶紧", "不准", "不要", "别动", "别说", "别做", "快去", "去做", "给我", "听着", "站住", "闭嘴"],
  request: ["能不能", "可以吗", "好吗", "请", "麻烦", "帮个忙", "帮忙", "劳驾", "能否", "想请你", "能帮我", "借我", "方便吗"],
} as const;

const POLITE_EXCLUSIONS = ["别客气", "别介意", "别见怪", "别担心", "别着急"] as const;

const EMOJIS: EmojiMap = {
  "夸奖": ["(๑•̀ㅂ•́)و✧", "(ﾉ≧∀≦)ﾉ", "٩(๑>◡<๑)۶", "(★▽★)", "(⌒▽⌒)☆", "(*≧ω≦)", "(ง •_•)ง", "ヾ(≧▽≦*)o"],
  "责怪": ["(╬ Ò﹏Ó)", "(╯°□°）╯", "(▼皿▼#)", "(｡•́︿•̀｡)", "(╥﹏╥)", "ヽ(｀Д´)ﾉ", "(＃＞＜)", "(；′⌒`)"],
  "命令": ["(¬_¬)", "(｀ε´)", "(＃｀Д´)", "(●｀∀´●)", "┌（┌ *｀д´）┐", "(｀д´)", "(•̀へ •́ ╮ )", "(￣ω￣;)"],
  "叙事": ["(。・ω・。)", "(￣▽￣)", "(´• ω •`)", "(・・?)", "(。_。)", "(￣ω￣)", "(´▽`)", "(=_=)"],
  "请求": ["(っ´∀｀)っ", "(๑•̀ω•́๑)✧", "(づ｡◕‿‿◕｡)づ", "(p≧w≦q)", "(♡˙︶˙♡)", "(⁄ ⁄•⁄ω⁄•⁄ ⁄)", "(´;ω;｀)", "(人•ᴗ•✿)"],
  "未知": ["(•̀_•́)", "(；一_一)", "(???)"],
};

function containsAny(text: string, words: readonly string[]): boolean {
  return words.some(word => text.includes(word));
}

export function classifyAckReactionEmoji(sentence: unknown): AckReactionClassifyResult {
  if (!sentence || typeof sentence !== "string") {
    return { type: "未知", emoji: `${CATCHPHRASE}(•̀_•́)` };
  }

  const s = sentence.trim();
  const isPolitePhrase = POLITE_EXCLUSIONS.some(phrase => s.includes(phrase));
  const isQuestion = /吗|呢|？|\?/.test(s);
  const startsWithPlease = s.startsWith("请");
  const hasExclamation = /[!！]/.test(s);
  const imperativeStart = !isPolitePhrase && /^(快|别|不要|不准|必须|马上|立刻)/.test(s);

  let type: SentenceType;
  if (containsAny(s, KEYWORDS.request) || (isQuestion && (startsWithPlease || /帮|麻烦/.test(s)))) {
    type = "请求";
  } else if (imperativeStart || containsAny(s, KEYWORDS.command)) {
    type = "命令";
  } else if (containsAny(s, KEYWORDS.praise)) {
    type = "夸奖";
  } else if (containsAny(s, KEYWORDS.blame) || (hasExclamation && /烦|讨厌|笨|蠢|差|气死/.test(s))) {
    type = "责怪";
  } else {
    type = "叙事";
  }

  const emojiList = EMOJIS[type];
  const emoji = emojiList[Math.floor(Math.random() * emojiList.length)];
  return { type, emoji: `${CATCHPHRASE}${emoji}` };
}
