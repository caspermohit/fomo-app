// Runs before React hydrates.
// Extensions stamp bis_* attrs (hydration) and throw (Urban VPN M_ID, etc.).
const JUNK = /^(bis_|__processed_)/;
const EXT = /(?:chrome|moz|safari)-extension:\/\//;

function isExtensionNoise(event: Event): boolean {
  const filename = "filename" in event ? String((event as ErrorEvent).filename || "") : "";
  const thrown =
    "error" in event
      ? (event as ErrorEvent).error
      : "reason" in event
        ? (event as PromiseRejectionEvent).reason
        : undefined;
  const stack = thrown instanceof Error ? thrown.stack || "" : String(thrown || "");
  return EXT.test(filename) || EXT.test(stack);
}

window.addEventListener(
  "error",
  (event) => {
    if (!isExtensionNoise(event)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  },
  true,
);
window.addEventListener(
  "unhandledrejection",
  (event) => {
    if (!isExtensionNoise(event)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  },
  true,
);

function stripExtensionAttrs(root: ParentNode = document) {
  const nodes = root instanceof Element ? [root, ...root.querySelectorAll("*")] : root.querySelectorAll("*");
  nodes.forEach((el) => {
    for (const attr of [...el.attributes]) {
      if (JUNK.test(attr.name)) el.removeAttribute(attr.name);
    }
  });
}

stripExtensionAttrs();
const mo = new MutationObserver((records) => {
  for (const rec of records) {
    if (rec.type === "attributes" && rec.attributeName && JUNK.test(rec.attributeName)) {
      (rec.target as Element).removeAttribute(rec.attributeName);
    }
    rec.addedNodes.forEach((node) => {
      if (node instanceof Element) stripExtensionAttrs(node);
    });
  }
});
mo.observe(document.documentElement, { subtree: true, childList: true, attributes: true });
setTimeout(() => mo.disconnect(), 4000);
