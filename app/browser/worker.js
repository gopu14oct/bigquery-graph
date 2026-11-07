// docparse Studio - Web Worker that runs docparse (Python) in Pyodide, off the UI thread.
// Messages in:  {id, cmd: "init" | "parse" | "chunks" | "export", ...}
// Messages out: {id, ok, result} | {id, ok: false, error} | {progress: "…"}
importScripts("pyodide/pyodide.js");

const WHEELS = ["charset_normalizer-3.5.2-py3-none-any.whl", "pdfminer_six-20260107-py3-none-any.whl",
  "pdfplumber-0.11.10-py3-none-any.whl", "pypdf-7.0.0-py3-none-any.whl"];
let py = null, booting = null;

const progress = (msg) => postMessage({ progress: msg });

// The artifact service won't serve binary archives, so .zip/.whl files ship base64-encoded as .txt.
async function fetchB64(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`Could not load ${path} (${r.status})`);
  const bin = atob((await r.text()).trim());
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

async function boot() {
  progress("Downloading Python…");
  const stdlib = URL.createObjectURL(new Blob([await fetchB64("pyodide/python_stdlib.b64.txt")], { type: "application/zip" }));
  progress("Starting Python…");
  py = await loadPyodide({ indexURL: new URL("pyodide/", self.location.href).href, stdLibURL: stdlib, stdout: () => {}, stderr: () => {} });
  URL.revokeObjectURL(stdlib);
  const site = py.runPython("import site; site.getsitepackages()[0]");
  progress("Loading the parser…");
  const files = await Promise.all([...WHEELS.map((w) => "wheels/" + w + ".b64.txt"), "pylib.b64.txt"].map(async (f) => [f, await fetchB64(f)]));
  for (const [f, buf] of files) py.unpackArchive(buf, f.includes(".whl") ? "wheel" : "zip", { extractDir: site });
  py.runPython("import studio");
  progress("");
}

self.onmessage = async (ev) => {
  const { id, cmd } = ev.data;
  try {
    booting ||= boot();
    await booting;
    const studio = py.pyimport("studio");
    let result;
    if (cmd === "init") result = true;
    else if (cmd === "parse") {
      progress("Parsing…");
      const data = py.toPy(new Uint8Array(ev.data.bytes));
      try { result = studio.parse(ev.data.filename, data, JSON.stringify(ev.data.options || {})); }
      finally { data.destroy(); }
      progress("");
    } else if (cmd === "chunks") result = studio.make_chunks(ev.data.elements, JSON.stringify(ev.data.params));
    else if (cmd === "suggest") result = studio.suggest(ev.data.elements);
    else if (cmd === "extract") result = studio.extract(ev.data.elements, JSON.stringify(ev.data.fields));
    else if (cmd === "export") result = studio.export(ev.data.elements, ev.data.fmt, ev.data.reviews || "{}", ev.data.filename || "");
    else throw new Error("unknown command " + cmd);
    studio.destroy();
    postMessage({ id, ok: true, result });
  } catch (e) {
    progress("");
    const msg = String(e && e.message || e);
    // Python errors arrive as full tracebacks: keep the last line, which is the actual error
    const last = msg.trim().split("\n").filter(Boolean).pop() || msg;
    postMessage({ id, ok: false, error: last.replace(/^\w+(Error|Exception): /, "") });
  }
};
