/** A download card for a ```doc fence. The file is built on the tool node. */

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function settings(source: string): { kind: string; title: string; markdown: string } {
  const lines = String(source ?? "").replace(/\n$/, "").split("\n");
  let kind = "docx";
  let title = "document";
  const body: string[] = [];
  for (const line of lines) {
    const match = /^(kind|title)\s*:\s*(\S.*?)\s*$/i.exec(line.trim());
    if (match && !body.length) {
      if (match[1].toLowerCase() === "kind") kind = match[2].trim().toLowerCase();
      else title = match[2].trim();
      continue;
    }
    body.push(line);
  }
  if (!["md", "txt", "csv", "docx", "xlsx", "pdf"].includes(kind)) kind = "docx";
  return { kind, title, markdown: body.join("\n").trim() };
}

export function docCard(source: string): string {
  const spec = settings(source);
  const safe = spec.title.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-|-$/g, "") || "document";
  const filename = safe.toLowerCase().endsWith(`.${spec.kind}`) ? safe : `${safe}.${spec.kind}`;
  const payload = JSON.stringify(spec).replace(/</g, "\\u003c");
  return `<div class="pi-doc"><a href="#" download="${escapeHtml(filename)}">Download ${escapeHtml(filename)}</a><script type="application/json">${payload}</script></div>`;
}

function downloadBytes(name: string, bytes: Uint8Array, type: string): void {
  const blob = new Blob([bytes.slice().buffer], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export function mountDocs(root: ParentNode | null): void {
  if (!root || typeof document === "undefined") return;
  for (const node of root.querySelectorAll(".pi-doc")) {
    if (node.getAttribute("data-bound") === "1") continue;
    node.setAttribute("data-bound", "1");
    const link = node.querySelector("a");
    const script = node.querySelector("script");
    if (!link || !script) continue;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      let spec: { kind?: string; title?: string; markdown?: string } = {};
      try {
        spec = JSON.parse(script.textContent || "") as { kind?: string; title?: string; markdown?: string };
      } catch {
        return;
      }
      const markdown = spec.markdown || "";
      fetch("/tools/render_doc", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ markdown, kind: spec.kind || "docx", name: spec.title || "document" }),
      })
        .then((response) => (response.ok ? response.json() : null))
        .then((body: { data?: string; name?: string } | null) => {
          if (body?.data) {
            const raw = atob(body.data);
            const bytes = new Uint8Array(raw.length);
            for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
            downloadBytes(body.name || "document", bytes, "application/octet-stream");
            return;
          }
          downloadBytes(`${spec.title || "document"}.md`, new TextEncoder().encode(markdown), "text/markdown");
        })
        .catch(() => {
          downloadBytes(`${spec.title || "document"}.md`, new TextEncoder().encode(markdown), "text/markdown");
        });
    });
  }
}
