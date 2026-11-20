// Shared workspace selection: the folder the agent builds in.
// Persisted locally and broadcast so the sidebar chip and chat composer agree.

const KEY = "nw.workspace";
const EVENT = "nw:workspace";

export function getWorkspace(): string {
  return localStorage.getItem(KEY) || "";
}

export function setWorkspace(path: string) {
  if (path) localStorage.setItem(KEY, path);
  else localStorage.removeItem(KEY);
  window.dispatchEvent(new CustomEvent(EVENT, { detail: path }));
}

export function onWorkspace(cb: (path: string) => void): () => void {
  const handler = (e: Event) => cb((e as CustomEvent).detail ?? getWorkspace());
  window.addEventListener(EVENT, handler);
  window.addEventListener("storage", handler);
  return () => {
    window.removeEventListener(EVENT, handler);
    window.removeEventListener("storage", handler);
  };
}

// ~/short form, middle-truncated so chips never wrap.
export function shortPath(path: string, max = 34): string {
  let p = path || "";
  const home = "/home/";
  if (p.startsWith(home) && p.split("/").length > 3) {
    const parts = p.split("/");
    p = "~/" + parts.slice(3).join("/");
  }
  if (p.length > max) {
    const keep = max - 1;
    p = p.slice(0, Math.floor(keep / 2)) + "…" + p.slice(-Math.ceil(keep / 2));
  }
  return p || "choose folder";
}
