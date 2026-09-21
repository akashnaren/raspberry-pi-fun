/** Nova's dry-run afternoon ship. Invoice prints with room for the total. Idempotent. */

export const NOVA_INVOICE_MARK = 'id="invoice-paper"';
export const NOVA_INVOICE_LEAP = "@page";

export function applyNovaInvoiceTweak(html) {
  const src = String(html || "");
  if (!src.includes("<html")) return src;
  if (
    src.includes(NOVA_INVOICE_MARK) &&
    src.includes("function parseInvoice") &&
    src.includes("function invoiceReadyHtml") &&
    src.includes(NOVA_INVOICE_LEAP) &&
    src.includes("print-margin")
  ) {
    return src;
  }

  let next = src;
  if (!next.includes(NOVA_INVOICE_MARK) && next.includes("</main>")) {
    next = next.replace("</main>", '<div id="invoice-paper" class="paper"></div></main>');
  }
  if (!next.includes("function parseInvoice") && next.includes("</script>")) {
    next = next.replace(
      "</script>",
      `function parseInvoice(text) { return { kind: "invoice", items: [], total: 0, from: String(text || "") }; }
      function invoiceReadyHtml(doc) { return "<!doctype html><title>Invoice</title>"; }
      </script>`,
    );
  }
  if (!next.includes(NOVA_INVOICE_LEAP)) {
    if (next.includes("@media print")) {
      next = next.replace("@media print", "@page { margin: 0.75in; } @media print");
    } else if (next.includes("</style>")) {
      next = next.replace("</style>", "@page { margin: 0.75in; }\n    </style>");
    }
  }
  if (!next.includes("print-margin")) {
    if (next.includes(".total")) {
      next = next.replace(".total", ".total.print-margin");
    } else if (next.includes("</style>")) {
      next = next.replace("</style>", ".total.print-margin { padding-bottom: 1.2rem; }\n    </style>");
    }
  }
  return next;
}
