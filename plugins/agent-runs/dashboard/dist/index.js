(function () {
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry) {
    console.error("[agent-runs] Hermes plugin SDK unavailable");
    return;
  }

  const React = sdk.React;
  const h = React.createElement;
  const hooks = sdk.hooks;
  const fetchJSON = sdk.fetchJSON;
  const Button = sdk.components.Button;
  const Input = sdk.components.Input;

  const API = "/api/plugins/agent-runs";
  const ASSET_BASE = "/dashboard-plugins/agent-runs/dist";
  const completed = new Set(["completed", "failed", "killed"]);
  let xtermPromise = null;

  function fmtTime(ts) {
    if (!ts) return "";
    try { return new Date(ts * 1000).toLocaleString(); } catch { return ""; }
  }

  function wsUrl(runId) {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const token = window.__HERMES_SESSION_TOKEN__ || "";
    return `${proto}//${window.location.host}${API}/runs/${encodeURIComponent(runId)}/terminal?token=${encodeURIComponent(token)}`;
  }

  function loadStyleOnce(id, href) {
    if (document.getElementById(id)) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const link = document.createElement("link");
      link.id = id;
      link.rel = "stylesheet";
      link.href = href;
      link.onload = resolve;
      link.onerror = () => reject(new Error(`Failed to load ${href}`));
      document.head.appendChild(link);
    });
  }

  function loadScriptOnce(id, src) {
    if (document.getElementById(id)) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.id = id;
      script.src = src;
      script.async = true;
      script.onload = resolve;
      script.onerror = () => reject(new Error(`Failed to load ${src}`));
      document.head.appendChild(script);
    });
  }

  function loadXterm() {
    if (!xtermPromise) {
      xtermPromise = Promise.all([
        loadStyleOnce("agent-runs-xterm-css", `${ASSET_BASE}/xterm.css`),
        loadScriptOnce("agent-runs-xterm-js", `${ASSET_BASE}/xterm.js`),
        loadScriptOnce("agent-runs-fit-js", `${ASSET_BASE}/addon-fit.js`),
      ]).then(() => {
        const TerminalCtor = window.Terminal;
        const FitAddonCtor = window.FitAddon && window.FitAddon.FitAddon;
        if (!TerminalCtor || !FitAddonCtor) {
          throw new Error("xterm.js globals were not registered");
        }
        return { TerminalCtor, FitAddonCtor };
      });
    }
    return xtermPromise;
  }

  function cssVar(name, fallback, scope) {
    const root = document.documentElement;
    const scoped = scope ? getComputedStyle(scope).getPropertyValue(name).trim() : "";
    const global = getComputedStyle(root).getPropertyValue(name).trim();
    return scoped || global || fallback;
  }

  function resolveCssColor(value, fallback) {
    const probe = document.createElement("span");
    probe.style.color = value || fallback;
    probe.style.position = "absolute";
    probe.style.pointerEvents = "none";
    probe.style.opacity = "0";
    document.body.appendChild(probe);
    const resolved = getComputedStyle(probe).color;
    probe.remove();
    return resolved || fallback;
  }

  function themedTerminalOptions(container, isLive) {
    const page = container && container.closest ? container.closest(".agent-runs-page") : null;
    const bg = resolveCssColor(cssVar("--agent-runs-terminal-bg", cssVar("--background-base", "#041c1c"), page), "#041c1c");
    const fg = resolveCssColor(cssVar("--agent-runs-terminal-fg", cssVar("--midground-base", "#ffe6cb"), page), "#ffe6cb");
    const accent = resolveCssColor(cssVar("--midground-base", "#ffe6cb", page), "#ffe6cb");
    const mono = cssVar("--theme-font-mono", 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace', page);
    return {
      convertEol: true,
      cursorBlink: isLive,
      cursorStyle: "block",
      disableStdin: !isLive,
      fontFamily: mono,
      fontSize: 13,
      lineHeight: 1.25,
      scrollback: 10000,
      theme: {
        background: bg,
        foreground: fg,
        cursor: accent,
        selectionBackground: "#334155",
        black: "#0f172a",
        red: "#ef4444",
        green: "#22c55e",
        yellow: "#eab308",
        blue: "#3b82f6",
        magenta: "#a855f7",
        cyan: "#06b6d4",
        white: fg,
        brightBlack: "#475569",
        brightRed: "#f87171",
        brightGreen: "#86efac",
        brightYellow: "#fde047",
        brightBlue: "#93c5fd",
        brightMagenta: "#d8b4fe",
        brightCyan: "#67e8f9",
        brightWhite: "#ffffff",
      },
    };
  }

  function AgentRunsPage() {
    const [runs, setRuns] = hooks.useState([]);
    const [selectedId, setSelectedId] = hooks.useState(null);
    const [content, setContent] = hooks.useState("");
    const [error, setError] = hooks.useState("");
    const [busy, setBusy] = hooks.useState(false);
    const [xtermActive, setXtermActive] = hooks.useState(false);
    const [form, setForm] = hooks.useState({ harness: "generic", cwd: "", prompt: "", command: "", full_auto: false });
    const xtermContainerRef = hooks.useRef(null);
    const fallbackPreRef = hooks.useRef(null);
    const wsRef = hooks.useRef(null);
    const termRef = hooks.useRef(null);
    const fitRef = hooks.useRef(null);
    const resizeRef = hooks.useRef(null);
    const liveRef = hooks.useRef(false);

    const selected = hooks.useMemo(() => runs.find((r) => r.id === selectedId) || null, [runs, selectedId]);

    const loadRuns = hooks.useCallback(async () => {
      const data = await fetchJSON(`${API}/runs`);
      setRuns(data.runs || []);
      if (!selectedId && data.runs && data.runs.length) setSelectedId(data.runs[0].id);
    }, [selectedId]);

    hooks.useEffect(() => {
      loadRuns().catch((e) => setError(String(e.message || e)));
      const timer = setInterval(() => loadRuns().catch(() => {}), 3000);
      return () => clearInterval(timer);
    }, [loadRuns]);

    function appendFallback(text) {
      setContent((prev) => prev + String(text || ""));
    }

    function writeTerminal(text) {
      const chunk = String(text || "");
      if (termRef.current) {
        termRef.current.write(chunk);
      } else {
        appendFallback(chunk);
      }
    }

    hooks.useEffect(() => {
      liveRef.current = !!(selected && !completed.has(selected.status));
    }, [selected && selected.status, selectedId]);

    hooks.useEffect(() => {
      let cancelled = false;

      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
      if (resizeRef.current) {
        resizeRef.current.disconnect();
        resizeRef.current = null;
      }
      if (termRef.current) {
        termRef.current.dispose();
        termRef.current = null;
      }
      fitRef.current = null;
      setContent("");
      setXtermActive(false);

      if (!selectedId || !selected) {
        setContent("Select or start a CLI session.");
        return () => { cancelled = true; };
      }

      async function openTerminal() {
        const isLive = !completed.has(selected.status);
        try {
          const { TerminalCtor, FitAddonCtor } = await loadXterm();
          if (cancelled || !xtermContainerRef.current) return;

          const term = new TerminalCtor(themedTerminalOptions(xtermContainerRef.current, isLive));
          const fit = new FitAddonCtor();
          term.loadAddon(fit);
          term.open(xtermContainerRef.current);
          termRef.current = term;
          fitRef.current = fit;
          setXtermActive(true);
          setTimeout(() => { try { fit.fit(); } catch {} }, 0);

          if (window.ResizeObserver) {
            const ro = new ResizeObserver(() => {
              try { fit.fit(); } catch {}
            });
            ro.observe(xtermContainerRef.current);
            resizeRef.current = ro;
          } else {
            window.addEventListener("resize", () => { try { fit.fit(); } catch {} }, { passive: true });
          }

          term.onData((data) => {
            if (!liveRef.current) return;
            fetchJSON(`${API}/runs/${encodeURIComponent(selectedId)}/input`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ data }),
            }).catch((e) => setError(String(e.message || e)));
          });

          if (isLive) {
            const ws = new WebSocket(wsUrl(selectedId));
            wsRef.current = ws;
            ws.onmessage = (event) => writeTerminal(event.data || "");
            ws.onerror = () => setError("Terminal websocket error");
            ws.onclose = () => { if (wsRef.current === ws) wsRef.current = null; };
          } else {
            const data = await fetchJSON(`${API}/runs/${encodeURIComponent(selectedId)}/transcript?tail=200000`);
            if (!cancelled) writeTerminal(data.content || "");
          }
        } catch (e) {
          if (cancelled) return;
          setXtermActive(false);
          setError(`xterm.js unavailable; using plain transcript view. ${String(e.message || e)}`);
          if (isLive) {
            const ws = new WebSocket(wsUrl(selectedId));
            wsRef.current = ws;
            ws.onmessage = (event) => appendFallback(event.data || "");
            ws.onerror = () => setError("Terminal websocket error");
            ws.onclose = () => { if (wsRef.current === ws) wsRef.current = null; };
          } else {
            fetchJSON(`${API}/runs/${encodeURIComponent(selectedId)}/transcript?tail=200000`)
              .then((data) => { if (!cancelled) setContent(data.content || ""); })
              .catch((err) => { if (!cancelled) setError(String(err.message || err)); });
          }
        }
      }

      openTerminal();

      return () => {
        cancelled = true;
        if (wsRef.current) {
          wsRef.current.close();
          wsRef.current = null;
        }
        if (resizeRef.current) {
          resizeRef.current.disconnect();
          resizeRef.current = null;
        }
        if (termRef.current) {
          termRef.current.dispose();
          termRef.current = null;
        }
        fitRef.current = null;
      };
    }, [selectedId, selected && selected.status]);

    hooks.useEffect(() => {
      const el = fallbackPreRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    }, [content, selectedId]);

    async function startRun(event) {
      event.preventDefault();
      setBusy(true);
      setError("");
      try {
        const payload = {
          harness: form.harness,
          cwd: form.cwd,
          prompt: form.prompt,
          command: form.command,
          full_auto: !!form.full_auto,
        };
        const run = await fetchJSON(`${API}/runs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        setRuns((prev) => [run, ...prev]);
        setSelectedId(run.id);
      } catch (e) {
        setError(String(e.message || e));
      } finally {
        setBusy(false);
      }
    }

    async function killRun(runId) {
      setError("");
      try {
        await fetchJSON(`${API}/runs/${encodeURIComponent(runId)}/kill`, { method: "POST" });
        await loadRuns();
      } catch (e) { setError(String(e.message || e)); }
    }

    async function deleteRun(runId) {
      setError("");
      try {
        await fetchJSON(`${API}/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
        if (selectedId === runId) {
          setSelectedId(null);
          setContent("");
        }
        await loadRuns();
      } catch (e) { setError(String(e.message || e)); }
    }

    async function finalizeRun(runId) {
      setError("");
      try {
        await fetchJSON(`${API}/runs/${encodeURIComponent(runId)}/finalize`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "review_required", notes: "Marked ready for Hermes/Kanban review from dashboard." }),
        });
        await loadRuns();
      } catch (e) { setError(String(e.message || e)); }
    }

    return h("div", { className: "agent-runs-page" },
      h("aside", { className: "agent-runs-sidebar" },
        h("div", { className: "agent-runs-sidebar-header" },
          h("div", { className: "agent-runs-title" }, "Agent Runs"),
          h("div", { className: "agent-runs-subtitle" }, "PTY side-channel sessions")
        ),
        h("form", { className: "agent-runs-form", onSubmit: startRun },
          h("label", null, "Harness"),
          h("select", {
            value: form.harness,
            onChange: (e) => setForm({ ...form, harness: e.target.value }),
          },
            h("option", { value: "generic" }, "Generic shell"),
            h("option", { value: "codex" }, "Codex"),
            h("option", { value: "claude" }, "Claude Code")
          ),
          h("label", null, "Project cwd"),
          h(Input, {
            value: form.cwd,
            placeholder: "/path/to/project",
            onChange: (e) => setForm({ ...form, cwd: e.target.value }),
          }),
          form.harness === "generic"
            ? h(React.Fragment, null,
                h("label", null, "Command"),
                h("textarea", {
                  className: "agent-runs-textarea",
                  value: form.command,
                  placeholder: "python -m pytest -q",
                  onChange: (e) => setForm({ ...form, command: e.target.value }),
                })
              )
            : h(React.Fragment, null,
                h("label", null, "Prompt"),
                h("textarea", {
                  className: "agent-runs-textarea",
                  value: form.prompt,
                  placeholder: "Implement the feature, run tests, summarize changes…",
                  onChange: (e) => setForm({ ...form, prompt: e.target.value }),
                }),
                form.harness === "codex" ? h("label", { className: "agent-runs-check" },
                  h("input", {
                    type: "checkbox",
                    checked: !!form.full_auto,
                    onChange: (e) => setForm({ ...form, full_auto: e.target.checked }),
                  }),
                  " codex --full-auto"
                ) : null
              ),
          h(Button, { type: "submit", disabled: busy }, busy ? "Starting…" : "Start session")
        ),
        error ? h("div", { className: "agent-runs-error" }, error) : null,
        h("div", { className: "agent-runs-list" },
          runs.length ? runs.map((run) => h("div", {
            key: run.id,
            className: "agent-runs-item" + (run.id === selectedId ? " selected" : ""),
            onClick: () => setSelectedId(run.id),
          },
            h("div", { className: "agent-runs-item-main" },
              h("div", { className: "agent-runs-item-title" }, run.title || run.id),
              h("div", { className: "agent-runs-item-meta" }, `${run.harness} · ${run.status} · ${fmtTime(run.created_at)}`)
            ),
            completed.has(run.status)
              ? h("button", {
                  className: "agent-runs-delete",
                  title: "Delete completed session",
                  onClick: (e) => { e.stopPropagation(); deleteRun(run.id); },
                }, "Delete")
              : null
          )) : h("div", { className: "agent-runs-empty" }, "No sessions yet")
        )
      ),
      h("main", { className: "agent-runs-main" },
        selected ? h("div", { className: "agent-runs-toolbar" },
          h("div", null,
            h("div", { className: "agent-runs-main-title" }, selected.title || selected.id),
            h("div", { className: "agent-runs-main-meta" }, `${selected.command || ""}\n${selected.cwd || ""}`)
          ),
          h("div", { className: "agent-runs-actions" },
            !completed.has(selected.status) ? h(Button, { onClick: () => killRun(selected.id) }, "Kill") : null,
            completed.has(selected.status) && !selected.finalized ? h(Button, { onClick: () => finalizeRun(selected.id) }, "Finalize") : null,
            selected.finalized ? h("span", { className: "agent-runs-finalized" }, "Finalized") : null
          )
        ) : null,
        h("div", { className: "agent-runs-terminal-shell" },
          h("div", { ref: xtermContainerRef, className: "agent-runs-xterm" + (xtermActive ? " active" : "") }),
          !xtermActive ? h("pre", { ref: fallbackPreRef, className: "agent-runs-terminal" },
            selected ? (content || "[agent-runs] waiting for transcript…") : "Select or start a CLI session."
          ) : null
        )
      )
    );
  }

  registry.register("agent-runs", AgentRunsPage);
})();
