import { cardFrom, cardsFrom, renderImageCardsHtml } from "./src/images.ts";

const poster = {
  url: "https://upload.wikimedia.org/wikipedia/en/2/2e/Inception_%282010%29_theatrical_poster.jpg",
  alt: "Inception. 2010 film by Christopher Nolan",
  title: "Inception",
  caption: "2010 film by Christopher Nolan",
  source: "https://en.wikipedia.org/wiki/Inception",
  width: 220,
  height: 326,
};

const html = renderImageCardsHtml([
  poster,
  {
    url: "https://thumb.wikimedia.org/wikipedia/commons/thumb/f/fd/Red_Panda.jpg/330px-Red_Panda.jpg",
    alt: "Red panda",
    title: "Red panda <script>",
    caption: 'A "small" mammal & friend',
    source: "https://en.wikipedia.org/wiki/Red_panda",
    width: 330,
    height: 220,
  },
  {
    url: "http://127.0.0.1/secret.jpg",
    alt: "secret",
    title: "secret",
  },
  {
    url: "javascript:alert(1)",
    alt: "script",
    title: "script",
  },
  {
    url: "https://evil.example/poster.jpg",
    alt: "other host",
    title: "other host",
  },
  poster,
]);

if (!html.startsWith('<div class="image-cards"')) {
  throw new Error("strip missing: " + html.slice(0, 80));
}
if ((html.match(/class="image-card"/g) || []).length !== 2) {
  throw new Error("expected two public cards, got " + html);
}
if (!html.includes(`src="${poster.url}"`)) {
  throw new Error("poster src missing");
}
if (!html.includes('href="https://en.wikipedia.org/wiki/Inception"')) {
  throw new Error("wikipedia link missing");
}
if (!html.includes("Inception &lt;script&gt;") && !html.includes("Red panda &lt;script&gt;")) {
  throw new Error("title was not escaped: " + html);
}
if (html.includes("<script>") || html.includes("127.0.0.1") || html.includes("javascript:") || html.includes("evil.example")) {
  throw new Error("unsafe card leaked: " + html);
}
if (!html.includes("&quot;small&quot;") || !html.includes("&amp; friend")) {
  throw new Error("caption was not escaped: " + html);
}
if (!html.includes('width="220"') || !html.includes('height="326"')) {
  throw new Error("size missing");
}

if (renderImageCardsHtml(null) !== "") throw new Error("null should render nothing");
if (renderImageCardsHtml([]) !== "") throw new Error("empty list should render nothing");
if (renderImageCardsHtml({ url: poster.url }) !== "") throw new Error("object is not a list");

const blocked = [
  "http://10.1.2.3/a.jpg",
  "http://192.168.0.2/a.jpg",
  "https://localhost/a.jpg",
  "https://upload.wikimedia.org/wikipedia/en/a.svg",
  "https://user:pass@upload.wikimedia.org/wikipedia/en/a.jpg",
  "https://upload.wikimedia.org:8080/wikipedia/en/a.jpg",
  "data:image/png;base64,aaaa",
];
for (const url of blocked) {
  if (cardFrom({ url, alt: "x", title: "x" })) {
    throw new Error("kept blocked url " + url);
  }
}

const thumb = cardFrom({
  url: "http://thumb.wikimedia.org/wikipedia/commons/thumb/a/a.jpg/a.jpg",
  title: "Plain",
  alt: "",
});
if (!thumb || !thumb.url.startsWith("http://thumb.wikimedia.org/")) {
  throw new Error("public http thumbnail was dropped");
}
if (thumb.source !== "") throw new Error("missing source should stay empty");

const many = cardsFrom([
  poster,
  { ...poster, url: poster.url + "?x=1" },
  { url: "https://upload.wikimedia.org/wikipedia/en/a.jpg", title: "A", alt: "A" },
  { url: "https://upload.wikimedia.org/wikipedia/en/b.jpg", title: "B", alt: "B" },
  { url: "https://upload.wikimedia.org/wikipedia/en/c.jpg", title: "C", alt: "C" },
]);
if (many.length !== 3) throw new Error("cap is 3, got " + many.length);

console.log("ok");
