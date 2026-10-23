import ReactMarkdown, { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Element, ElementContent, Root, RootContent } from "hast";
import { ratingTone } from "./shared";

// How an agent's markdown is shown. The text is the model's own, so nothing here
// changes what it says: tables render (GitHub tables need remark-gfm), the debate
// speaker prefixes upstream glues to each turn become turn headers, the typed fields
// ("**Rating**: Hold") become tiles or labelled blocks, and headings and the
// interpretation column of a table get an emoji as a reading aid.

const SPEAKERS: Record<string, string> = { Bull: "🐂", Bear: "🐻", Aggressive: "🔥", Risky: "🔥", Conservative: "🛡️", Safe: "🛡️", Neutral: "⚖️" };
const SPEAKER_LINE = /^(Bull|Bear|Aggressive|Risky|Conservative|Safe|Neutral) Analyst:[ \t]*/gm;
// "To the Aggressive Analyst: …" opens a reply to one speaker; shown as a labelled block.
const ADDRESS_LINE = /^[ \t]*To the (Bull|Bear|Aggressive|Risky|Conservative|Safe|Neutral) Analyst:[ \t]*/gm;
const PROPOSAL_LINE = /^[ \t]*FINAL TRANSACTION PROPOSAL:[ \t]*\**[ \t]*(BUY|SELL|HOLD)\b\**[ \t]*$/gim;
const FIELD_LINE = /^[ \t]*\*\*([A-Z][A-Za-z /&-]{1,40}?):?\*\*:?[ \t]*(.*)$/;

// Short fields shown as tiles above the text, in the order the agent wrote them.
const FACTS = new Set(["rating", "recommendation", "action", "entry price", "stop loss", "price target", "time horizon", "confidence"]);
const RATED = new Set(["rating", "recommendation", "action"]);

const FIELD_EMOJI: Record<string, string> = {
  rating: "⭐", recommendation: "🧭", action: "⚡", "entry price": "🚪", "stop loss": "🛑", "price target": "🎯",
  "time horizon": "⏳", confidence: "📶", "executive summary": "📝", "investment thesis": "💡", rationale: "🧠",
  reasoning: "🧠", "strategic actions": "🗺️", "position sizing": "⚖️", "key risks": "⚠️", risks: "⚠️",
  catalysts: "📰", "missing evidence": "❓", summary: "📝", outlook: "🔮",
};

export const RATING_EMOJI: Record<string, string> = { buy: "🟢", overweight: "🟢", hold: "🟡", underweight: "🟠", sell: "🔴" };

// First match wins, so the more specific topics come first.
const HEADING_EMOJI: [RegExp, string][] = [
  [/summary table|at a glance|key (figures|metrics|levels|numbers)|scorecard/, "📋"],
  [/bottom line|conclusion|verdict|final (call|view|word|thoughts)|\boverall\b/, "🏁"],
  [/data (integrity|quality|gaps?)|caveats?|limitations?|\bflags?\b/, "🔍"],
  [/missing evidence|open questions?|unknowns?|what would change/, "❓"],
  [/anticipat|rebuttal|\bcounter|responding|response to|objections?|addressing/, "🥊"],
  [/\bbull/, "🐂"],
  [/\bbear/, "🐻"],
  [/scenarios?|outlook|forecast/, "🔮"],
  [/momentum|\brsi\b|\bmacd\b/, "⚡"],
  [/volatility|\batr\b|bollinger/, "🌪️"],
  [/\bvolume|\bflows?\b/, "📊"],
  [/\btrend|moving averages?|price action/, "📈"],
  [/technical|indicators?|\bchart/, "📐"],
  [/insiders?|directors?|management|ownership/, "👥"],
  [/profit|margins?|earnings|\bincome|cash flow/, "💰"],
  [/balance sheet|liquidity|\bdebt|leverage|\bcash\b|funding|capital/, "🏦"],
  [/valuation|multiples?|price target/, "🏷️"],
  [/growth|revenue|\bsales\b|demand/, "🌱"],
  [/fundamental|financials?/, "🧾"],
  [/support|resistance|\blevels?\b/, "🧱"],
  [/macro|econom|\brates?\b|\bfed\b|yields?|inflation|geopolit/, "🌍"],
  [/\bnews|headlines?|announcements?|\bevents?\b|catalysts?/, "📰"],
  [/sentiment|social|reddit|crowd/, "💬"],
  [/\brisks?\b|downside|threats?|concerns?/, "⚠️"],
  [/sizing|\bpositions?\b|allocation|portfolio|\bbook\b/, "⚖️"],
  [/recommend|\bactions?\b|\bplan\b|strategy|execution|\btrade\b|decision/, "🧭"],
  [/competit|industry|sector|peers?/, "🏭"],
  [/lessons?|reflection|\bpast\b/, "🎓"],
  [/setup|overview|context|background|introduction|snapshot/, "🧩"],
];

// Table columns whose cells say what a figure means; those cells get a tone marker.
const VERDICT_COLUMN = /interpret|signal|implication|\bbias\b|assessment|takeaway|reading|\bview\b|verdict|impact|meaning|direction|so what/i;
const DOWN = /\b(bearish|negative|downtrend|down|sell|underweight|weak(ening|er)?|deteriorat\w*|falling|declin\w*|contracting|headwinds?)\b/;
const UP = /\b(bullish|positive|uptrend|buy|overweight|strong(er)?|improv\w*|rising|expanding|tailwinds?|supportive)\b/;
const CAUTION = /\b(caution|unknown|uncertain|mixed|neutral|possibl[ey]|check|watch|distorts?|understates?|unclear|unverified)\b/;

type Fact = { key: string; label: string; value: string };
type Turn = { speaker: string | null; body: string };

function textOf(node: ElementContent | RootContent): string {
  if (node.type === "text") return node.value;
  if (node.type === "element") return node.children.map(textOf).join("");
  return "";
}

function emojiSpan(value: string, className: string): Element {
  return { type: "element", tagName: "span", properties: { className: [className], ariaHidden: "true" }, children: [{ type: "text", value }] };
}

function startsWithEmoji(text: string) {
  return /^\s*\p{Extended_Pictographic}/u.test(text);
}

export function headingEmoji(text: string) {
  const lower = text.toLowerCase();
  return HEADING_EMOJI.find(([pattern]) => pattern.test(lower))?.[1] ?? null;
}

export function cellTone(text: string) {
  const lower = text.toLowerCase(), down = DOWN.test(lower), up = UP.test(lower);
  if (down && up) return "🟡";
  if (down) return "🔴";
  if (up) return "🟢";
  return CAUTION.test(lower) ? "🟡" : null;
}

function decorate() {
  return (tree: Root) => {
    const visit = (node: Root | Element) => {
      for (const child of node.children) {
        if (child.type !== "element") continue;
        if (/^h[1-4]$/.test(child.tagName)) {
          const text = textOf(child), emoji = startsWithEmoji(text) ? null : headingEmoji(text);
          if (emoji) child.children.unshift(emojiSpan(emoji, "h-emoji"));
        } else if (child.tagName === "table") {
          markVerdictColumns(child);
        } else visit(child);
      }
    };
    visit(tree);
    groupFields(tree);
  };
}

/** The label of a paragraph that opens with "**Label**:", and the paragraph without it. */
function fieldOf(node: RootContent): { name: string; rest: ElementContent[] } | null {
  if (node.type !== "element" || node.tagName !== "p") return null;
  const [first, second, ...others] = node.children;
  if (!first || first.type !== "element" || first.tagName !== "strong") return null;
  const label = textOf(first).trim(), colonAfter = second?.type === "text" && second.value.startsWith(":");
  if (!(label.endsWith(":") || colonAfter) || label.split(/\s+/).length > 5) return null;
  const rest = second ? [colonAfter ? { ...second, value: (second as { value: string }).value.replace(/^:\s*/, "") } : second, ...others] : [];
  return { name: label.replace(/:$/, "").trim(), rest: rest.filter((part) => part.type !== "text" || part.value.trim()) as ElementContent[] };
}

// "**Executive Summary**: text" becomes a labelled block that also holds what follows
// it (a list, more paragraphs) up to the next field, heading or rule. Only a bold run
// at the very start of a paragraph followed by a colon counts, so lead-ins such as
// "**Sizing.** …" stay prose.
function groupFields(tree: Root) {
  const out: RootContent[] = [];
  let group: Element | null = null;
  for (const node of tree.children) {
    const field = fieldOf(node);
    if (field) {
      const emoji = fieldEmoji(field.name);
      const label: Element = { type: "element", tagName: "div", properties: { className: ["report-field-label"] }, children: [...(emoji ? [emojiSpan(emoji, "field-emoji")] : []), { type: "text", value: field.name }] };
      group = { type: "element", tagName: "div", properties: { className: ["report-field"] }, children: [label] };
      if (field.rest.length) group.children.push({ type: "element", tagName: "p", properties: {}, children: field.rest });
      out.push(group);
    } else if (node.type === "element" && /^(h[1-6]|hr)$/.test(node.tagName)) {
      group = null;
      out.push(node);
    } else if (group && (node.type === "element" || node.type === "text")) {
      group.children.push(node);
    } else out.push(node);
  }
  tree.children = out;
}

function rows(table: Element, section: string) {
  const part = table.children.find((c): c is Element => c.type === "element" && c.tagName === section);
  return (part?.children ?? []).filter((c): c is Element => c.type === "element" && c.tagName === "tr");
}

function cells(row: Element) {
  return row.children.filter((c): c is Element => c.type === "element" && (c.tagName === "td" || c.tagName === "th"));
}

function markVerdictColumns(table: Element) {
  const header = rows(table, "thead")[0];
  if (!header) return;
  const columns = cells(header).map((cell, index) => (index > 0 && VERDICT_COLUMN.test(textOf(cell)) ? index : -1)).filter((index) => index > 0);
  for (const row of rows(table, "tbody")) {
    const rowCells = cells(row);
    for (const index of columns) {
      const cell = rowCells[index], tone = cell && !startsWithEmoji(textOf(cell)) ? cellTone(textOf(cell)) : null;
      if (cell && tone) cell.children.unshift(emojiSpan(tone, "tone-dot"));
    }
  }
}

/** Splits a debate history into its turns; text before the first speaker is a turn without one. */
export function splitTurns(markdown: string): Turn[] {
  const marks = [...markdown.matchAll(SPEAKER_LINE)];
  if (!marks.length) return [{ speaker: null, body: markdown }];
  const turns: Turn[] = [];
  const lead = markdown.slice(0, marks[0].index).trim();
  if (lead) turns.push({ speaker: null, body: lead });
  marks.forEach((mark, i) => {
    const body = markdown.slice(mark.index! + mark[0].length, marks[i + 1]?.index ?? markdown.length).trim();
    if (body) turns.push({ speaker: mark[1], body });
  });
  return turns;
}

/** Takes out the short typed fields and the trader's closing proposal, and tidies what is left. */
export function prepare(markdown: string) {
  const facts: Fact[] = [];
  let proposal: string | null = null;
  const lines = markdown.replace(PROPOSAL_LINE, (_line, action: string) => { proposal = action.toUpperCase(); return ""; }).split("\n");
  const kept = lines.filter((line) => {
    const match = FIELD_LINE.exec(line);
    if (!match) return true;
    const label = match[1].trim(), key = label.toLowerCase(), value = match[2].trim();
    if (!FACTS.has(key) || !value || value.length > 80 || /^(\d+\.|[-*+])\s/.test(value) || facts.some((f) => f.key === key)) return true;
    facts.push({ key, label, value: value.replace(/\*\*/g, "") });
    return false;
  });
  // A list written on the label's own line ("**Strategic Actions**: 1. …") would be
  // read as one paragraph, because only a list starting at 1 may interrupt one.
  const body = kept.join("\n").replace(ADDRESS_LINE, (_line, speaker: string) => `**To the ${speaker} analyst**: `).replace(/^([ \t]*\*\*[^*\n]+\*\*:?)[ \t]*(?=(?:\d+\.|[-*+])[ \t])/gm, "$1\n\n").replace(/\n{3,}/g, "\n\n").trim();
  return { facts, proposal: proposal as string | null, body };
}

function fieldEmoji(label: string) {
  const addressed = /^To the (\w+) analyst$/.exec(label);
  return addressed ? SPEAKERS[addressed[1]] ?? null : FIELD_EMOJI[label.toLowerCase()] ?? null;
}

function ratingWord(value: string) {
  return value.trim().split(/[\s,.;:(]/)[0].toLowerCase();
}

function RatingValue({ value }: { value: string }) {
  const word = ratingWord(value), emoji = RATING_EMOJI[word];
  if (!emoji) return <>{value}</>;
  const label = word.charAt(0).toUpperCase() + word.slice(1);
  return <span className={`rating large ${ratingTone(label)}`}><span aria-hidden="true">{emoji}</span> {value}</span>;
}

function Facts({ facts }: { facts: Fact[] }) {
  if (!facts.length) return null;
  return <dl className="report-facts">{facts.map((fact) => {
    const missing = /^(not provided|n\/a|none|—|-)$/i.test(fact.value);
    return <div key={fact.key} className={RATED.has(fact.key) ? "rated" : ""}><dt><span aria-hidden="true">{fieldEmoji(fact.label)}</span>{fact.label}</dt><dd className={missing ? "missing" : ""}>{missing ? "Not provided" : RATED.has(fact.key) ? <RatingValue value={fact.value} /> : fact.value}</dd></div>;
  })}</dl>;
}

function Proposal({ action }: { action: string }) {
  const label = action.charAt(0) + action.slice(1).toLowerCase();
  return <div className={`report-verdict ${ratingTone(label)}`}><span aria-hidden="true">🏁</span><div><small>Final transaction proposal</small><strong>{RATING_EMOJI[action.toLowerCase()]} {action}</strong></div></div>;
}

const components: Components = {
  table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
  a: ({ children, href }) => <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>,
};

function Markdown({ children }: { children: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[decorate]} components={components}>{children}</ReactMarkdown>;
}

export function Report({ markdown }: { markdown: string }) {
  const { facts, proposal, body } = prepare(markdown);
  const turns = splitTurns(body), rounds: Record<string, number> = {};
  // One turn is the section's own speaker, which the section heading already names.
  const labelled = turns.length > 1;
  return <div className="markdown report">
    <Facts facts={facts} />
    {turns.map((turn, index) => {
      const round = turn.speaker ? (rounds[turn.speaker] = (rounds[turn.speaker] ?? 0) + 1) : 0;
      return <section className="report-turn" key={index}>
        {labelled && turn.speaker && <header><span aria-hidden="true">{SPEAKERS[turn.speaker]}</span>{turn.speaker} analyst<small>Round {round}</small></header>}
        <Markdown>{turn.body}</Markdown>
      </section>;
    })}
    {proposal && <Proposal action={proposal} />}
  </div>;
}
