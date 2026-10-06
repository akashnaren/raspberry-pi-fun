# Image cards

After a turn finishes, the page may ask `POST /v1/images` with the question, the answer, and the search sources. pi4 forwards that call to pi2 (pi3 only if pi2 is down). The reply field is `pi_images`. A missing field, an empty list, or a failed lookup leaves the text answer as it was. Nothing is added to the chat stream, the prompt, or memory.

Cards are chosen from structure, not from a list of subjects:

1. English Wikipedia links already present in the turn's sources. The link is parsed for a title and is never fetched.
2. List-item heads and bold spans in the answer, after fenced blocks are ignored.
3. One Wikipedia search of the question, only when the first two find nothing.

A card is kept only when the summary is a standard article, the lead photo is a JPEG or WebP on `upload.wikimedia.org` (PNG only when `PI_PAIR_IMAGE_PNG=1`), and the normalized title matches the candidate. There is no subject-word list and no query rewrite. At most four cards are returned.

No embedding model is loaded for this feature.

## `pi_images`

The array is the JSON body of `POST /v1/images`. It is not copied onto chat chunks. The browser loads the image bytes from Wikimedia. The Pis do not proxy them.

| Field | Required | Meaning |
| --- | --- | --- |
| `url` | yes | Image URL on `upload.wikimedia.org` or `thumb.wikimedia.org`. Path ends in `.jpg`, `.jpeg`, or `.webp` (`.png` only behind the knob) and must not contain `.svg`. `http` or `https`, port 80 or 443 only. |
| `alt` | yes | Accessible name. |
| `title` | yes | Card heading. |
| `caption` | no | Short line under the heading. |
| `source` | no | `https` page on `wikipedia.org` that the card links to. |
| `width` | no | Positive pixel width from the source metadata, at most 8000. |
| `height` | no | Positive pixel height from the source metadata, at most 8000. |

Anything else is dropped: private addresses, carrier-grade NAT, `localhost`, other hosts, `javascript:` and `data:` URLs, SVG files, and a card with no title. The page removes a card if the image request fails. When the last card fails, the strip goes away and the text reply stays.

Outbound fetches from pi2 go only to `https://en.wikipedia.org/w/api.php` and `https://en.wikipedia.org/api/rest_v1/page/summary/`, URLs the node builds itself. Redirects are not followed automatically. The body cap is 200 KB.

`/v1/images` rejects a body over 8 KB with 413, and a question, answer, or source list over its cap with 400. Extra calls from one client return an empty list. The route does not take the decode slot.

If a `Content-Security-Policy` is added later, `img-src` must allow `https://upload.wikimedia.org` and `https://thumb.wikimedia.org`.

```json
{
  "pi_images": [
    {
      "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg/320px-Tour_Eiffel_Wikimedia_Commons.jpg",
      "alt": "Eiffel Tower. Wrought-iron lattice tower in Paris",
      "title": "Eiffel Tower",
      "caption": "Wrought-iron lattice tower in Paris",
      "source": "https://en.wikipedia.org/wiki/Eiffel_Tower",
      "width": 320,
      "height": 480
    }
  ]
}
```
