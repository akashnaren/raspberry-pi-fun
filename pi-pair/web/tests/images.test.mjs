import { cardFrom, cardsFrom, lowSubstance, renderImageCardsHtml } from "../src/images.ts";

const poster = {
  url: "https://upload.wikimedia.org/wikipedia/en/2/2e/Tour_Eiffel.jpg",
  alt: "Eiffel Tower. Lattice tower in Paris",
  title: "Eiffel Tower",
  caption: "Lattice tower in Paris",
  source: "https://en.wikipedia.org/wiki/Eiffel_Tower",
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
    url: "https://evil.example/photo.jpg",
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
if (!html.includes('href="https://en.wikipedia.org/wiki/Eiffel_Tower"')) {
  throw new Error("wikipedia link missing");
}
if (!html.includes("Red panda &lt;script&gt;")) {
  throw new Error("title was not escaped: " + html);
}
if (
  html.includes("<script>") ||
  html.includes("127.0.0.1") ||
  html.includes("javascript:") ||
  html.includes("evil.example")
) {
  throw new Error("unsafe card leaked: " + html);
}
if (!html.includes("&quot;small&quot;") || !html.includes("&amp; friend")) {
  throw new Error("caption was not escaped: " + html);
}
if (!html.includes('width="220"') || !html.includes('height="326"')) {
  throw new Error("size missing");
}
if (!html.includes('referrerpolicy="no-referrer"') || !html.includes('rel="noopener noreferrer"')) {
  throw new Error("image or link policy missing");
}

if (renderImageCardsHtml(null) !== "") throw new Error("null should render nothing");
if (renderImageCardsHtml([]) !== "") throw new Error("empty list should render nothing");
if (renderImageCardsHtml({ url: poster.url }) !== "") throw new Error("object is not a list");

const blocked = [
  "http://10.1.2.3/a.jpg",
  "http://192.168.0.2/a.jpg",
  "http://100.64.1.1/a.jpg",
  "https://localhost/a.jpg",
  "https://files.local/a.jpg",
  "https://upload.wikimedia.org/wikipedia/en/a.svg",
  "https://upload.wikimedia.org/wikipedia/en/a.svg.png",
  "https://user:pass@upload.wikimedia.org/wikipedia/en/a.jpg",
  "https://upload.wikimedia.org:8080/wikipedia/en/a.jpg",
  "data:image/jpeg;base64,aaaa",
  "javascript:alert(1)",
  "https://evil.example/a.jpg",
  "https://upload.wikimedia.org/wikipedia/en/a.gif",
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

const png = cardFrom({
  url: "https://upload.wikimedia.org/wikipedia/en/a.png",
  title: "Raster",
  alt: "Raster",
});
if (!png || !png.url.endsWith(".png")) throw new Error("png should stay in the browser");

const many = cardsFrom(
  Array.from({ length: 9 }, (_item, index) => ({
    url: "https://upload.wikimedia.org/wikipedia/en/" + index + ".jpg",
    title: "Item " + index,
    alt: "Item " + index,
  })),
);
if (many.length !== 4) throw new Error("cap is 4, got " + many.length);

const capped = renderImageCardsHtml(many.concat(poster));
if ((capped.match(/class="image-card"/g) || []).length !== 4) {
  throw new Error("render cap is 4, got " + capped);
}

if (lowSubstance("Hello", "Hello! What can I help you with?") !== true) {
  throw new Error("a greeting looked topical");
}
if (lowSubstance("Tell me about the movie Inception", "It is a story.") !== false) {
  throw new Error("Inception was treated as low substance");
}

console.log("ok");
