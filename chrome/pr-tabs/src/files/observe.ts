export function observeFiles(doc: Document, update: () => void): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const options = { childList: true, subtree: true, attributes: true };
  const observer = new MutationObserver(schedule);

  function refresh(): void {
    observer.disconnect();
    update();
    observer.observe(doc.documentElement, options);
  }

  function schedule(): void {
    clearTimeout(timer);
    timer = setTimeout(refresh, 50);
  }

  observer.observe(doc.documentElement, options);
  doc.addEventListener("turbo:load", schedule);
  doc.defaultView?.addEventListener("popstate", schedule);

  return () => {
    clearTimeout(timer);
    observer.disconnect();
    doc.removeEventListener("turbo:load", schedule);
    doc.defaultView?.removeEventListener("popstate", schedule);
  };
}
