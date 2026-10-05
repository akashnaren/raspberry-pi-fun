# Image cards

A generative miss can add `pi_images` to the chat completion when the question suits a picture: a movie, a poster, a place, a painting, or a "picture of" line. The field is omitted when there is nothing to show. A missing field, an empty list, or a failed lookup leaves the text answer as it was.

Pictures are public `http` and `https` images from Wikimedia. The lookup calls the English Wikipedia search and summary APIs. There is no API key and no paid host. The cards are not written into the train queue, and the model prompt does not receive the image URLs.

A cache hit does not look up pictures. pi2 and pi3 still do not search; they forward the brain's reply, including `pi_images` when pi4 sent it.

## `pi_images`

The array is on the JSON completion body. On a stream it is copied onto the search-result event, the answering status, and the final chunk. At most three items.

| Field | Required | Meaning |
| --- | --- | --- |
| `url` | yes | Image URL on `upload.wikimedia.org` or `thumb.wikimedia.org`. Path ends in `.jpg`, `.jpeg`, `.png`, `.webp`, or `.gif`. `http` or `https`, port 80 or 443 only. |
| `alt` | yes | Accessible name. |
| `title` | yes | Card heading. |
| `caption` | no | Short line under the heading, such as "2010 film by Christopher Nolan". |
| `source` | no | `https` page on `wikipedia.org` that the card links to. |
| `width` | no | Positive pixel width from the source metadata, at most 8000. |
| `height` | no | Positive pixel height from the source metadata, at most 8000. |

Anything else is dropped: private addresses, `localhost`, other hosts, `javascript:` and `data:` URLs, SVG files, and a card with no title. The page removes a card if the image request fails. When the last card fails, the strip goes away and the text reply stays.

```json
{
  "pi_images": [
    {
      "url": "https://upload.wikimedia.org/wikipedia/en/2/2e/Inception_%282010%29_theatrical_poster.jpg",
      "alt": "Inception. 2010 film by Christopher Nolan",
      "title": "Inception",
      "caption": "2010 film by Christopher Nolan",
      "source": "https://en.wikipedia.org/wiki/Inception",
      "width": 220,
      "height": 326
    }
  ]
}
```
