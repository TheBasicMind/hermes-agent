(function () {
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry) return;

  const React = sdk.React;
  const { useEffect, useMemo, useState } = sdk.hooks;
  const { Card, CardContent, Badge, Input } = sdk.components;
  const fetchJSON = sdk.fetchJSON;

  function isInjected(skill) {
    if (typeof skill.inject_frontmatter === "boolean") return skill.inject_frontmatter;
    return !!skill.preload;
  }

  function inlineMarkdown(text, keyPrefix) {
    const parts = String(text || "").split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g).filter(Boolean);
    return parts.map(function (part, idx) {
      const key = keyPrefix + "-inline-" + idx;
      if (part.startsWith("`") && part.endsWith("`")) {
        return React.createElement("code", { key: key }, part.slice(1, -1));
      }
      if (part.startsWith("**") && part.endsWith("**")) {
        return React.createElement("strong", { key: key }, part.slice(2, -2));
      }
      if (part.startsWith("*") && part.endsWith("*")) {
        return React.createElement("em", { key: key }, part.slice(1, -1));
      }
      return part;
    });
  }

  function MarkdownPreview(props) {
    const markdown = props.markdown || "";
    const lines = markdown.split(/\r?\n/);
    const elements = [];
    let codeLines = [];
    let inCode = false;

    function flushCode(idx) {
      if (!codeLines.length) return;
      elements.push(React.createElement("pre", { key: "code-" + idx },
        React.createElement("code", null, codeLines.join("\n"))
      ));
      codeLines = [];
    }

    lines.forEach(function (line, idx) {
      if (line.startsWith("```")) {
        if (inCode) flushCode(idx);
        inCode = !inCode;
        return;
      }
      if (inCode) {
        codeLines.push(line);
        return;
      }
      if (!line.trim()) {
        elements.push(React.createElement("div", { key: "blank-" + idx, className: "markdown-blank" }));
        return;
      }
      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        const tag = "h" + Math.min(heading[1].length, 4);
        elements.push(React.createElement(tag, { key: "h-" + idx }, inlineMarkdown(heading[2], "h-" + idx)));
        return;
      }
      const bullet = line.match(/^\s*[-*]\s+(.*)$/);
      if (bullet) {
        elements.push(React.createElement("div", { key: "li-" + idx, className: "markdown-bullet" },
          React.createElement("span", { className: "markdown-bullet-dot" }, "•"),
          React.createElement("span", null, inlineMarkdown(bullet[1], "li-" + idx))
        ));
        return;
      }
      elements.push(React.createElement("p", { key: "p-" + idx }, inlineMarkdown(line, "p-" + idx)));
    });
    flushCode("end");

    return React.createElement("div", { className: "skill-markdown-preview" }, elements);
  }

  function SkillPreloadPage() {
    const [skills, setSkills] = useState([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [toggling, setToggling] = useState(new Set());
    const [error, setError] = useState("");
    const [selectedSkill, setSelectedSkill] = useState(null);
    const [markdown, setMarkdown] = useState("");
    const [markdownDraft, setMarkdownDraft] = useState("");
    const [markdownLoading, setMarkdownLoading] = useState(false);
    const [markdownError, setMarkdownError] = useState("");
    const [textMode, setTextMode] = useState(false);

    useEffect(function () {
      fetchJSON("/api/skills/preload")
        .then(function (items) {
          setSkills(Array.isArray(items) ? items : []);
          setError("");
        })
        .catch(function (err) {
          setError((err && err.message) || "Failed to load startup frontmatter settings");
        })
        .finally(function () { setLoading(false); });
    }, []);

    const filtered = useMemo(function () {
      const q = search.trim().toLowerCase();
      if (!q) return skills;
      return skills.filter(function (skill) {
        return String(skill.name || "").toLowerCase().includes(q)
          || String(skill.description || "").toLowerCase().includes(q)
          || String(skill.category || "").toLowerCase().includes(q)
          || String(skill.skill_path || "").toLowerCase().includes(q);
      });
    }, [skills, search]);

    const byCategory = useMemo(function () {
      const grouped = new Map();
      filtered.forEach(function (skill) {
        const category = skill.category || "general";
        if (!grouped.has(category)) grouped.set(category, []);
        grouped.get(category).push(skill);
      });
      return Array.from(grouped.entries()).sort(function (a, b) { return a[0].localeCompare(b[0]); });
    }, [filtered]);

    function loadMarkdown(skill) {
      setSelectedSkill(skill);
      setMarkdown("");
      setMarkdownDraft("");
      setMarkdownError("");
      setMarkdownLoading(true);
      setTextMode(false);
      fetchJSON("/api/skills/" + encodeURIComponent(skill.name) + "/markdown")
        .then(function (payload) {
          const raw = String((payload && payload.markdown) || "");
          setMarkdown(raw);
          setMarkdownDraft(raw);
          setSelectedSkill(Object.assign({}, skill, {
            skill_path: (payload && payload.skill_path) || skill.skill_path,
          }));
          setMarkdownError("");
        })
        .catch(function (err) {
          setMarkdownError((err && err.message) || "Failed to load skill markdown");
        })
        .finally(function () { setMarkdownLoading(false); });
    }

    function toggle(skill) {
      const nextValue = !isInjected(skill);
      setToggling(function (prev) {
        const next = new Set(prev);
        next.add(skill.name);
        return next;
      });
      fetchJSON("/api/skills/preload/toggle", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        // The backend model still calls this field enabled for compatibility with
        // the existing SkillToggle schema, but it means: inject frontmatter at
        // new-session startup. It does NOT enable/disable the skill itself.
        body: JSON.stringify({ name: skill.name, enabled: nextValue }),
      })
        .then(function () {
          setSkills(function (prev) {
            return prev.map(function (item) {
              return item.name === skill.name
                ? Object.assign({}, item, { inject_frontmatter: nextValue, preload: nextValue })
                : item;
            });
          });
          setError("");
        })
        .catch(function (err) {
          setError((err && err.message) || "Failed to toggle startup frontmatter injection");
        })
        .finally(function () {
          setToggling(function (prev) {
            const next = new Set(prev);
            next.delete(skill.name);
            return next;
          });
        });
    }

    const injectedCount = skills.filter(isInjected).length;

    if (selectedSkill) {
      return React.createElement("div", { className: "skill-preload-page" },
        React.createElement("div", { className: "skill-markdown-header" },
          React.createElement("button", { className: "skill-markdown-back", onClick: function () { setSelectedSkill(null); } }, "← Back to skills"),
          React.createElement("div", null,
            React.createElement("h1", null, selectedSkill.name),
            React.createElement("div", { className: "skill-preload-path-row" },
              React.createElement("span", null, "Path"),
              React.createElement("code", null, selectedSkill.skill_path || selectedSkill.name)
            )
          ),
          React.createElement("label", { className: "skill-markdown-mode" },
            React.createElement("input", {
              type: "checkbox",
              checked: textMode,
              onChange: function (event) { setTextMode(event.target.checked); },
            }),
            React.createElement("span", null, textMode ? "Editable text" : "WYSIWYG markdown")
          )
        ),
        React.createElement(Card, { className: "skill-preload-card" },
          React.createElement(CardContent, { className: "skill-preload-card-content" },
            markdownLoading ? React.createElement("div", { className: "skill-preload-muted" }, "Loading markdown...") : null,
            markdownError ? React.createElement("div", { className: "skill-preload-error" }, markdownError) : null,
            !markdownLoading && !markdownError && textMode ? React.createElement("textarea", {
              className: "skill-markdown-textarea",
              value: markdownDraft,
              onChange: function (event) { setMarkdownDraft(event.target.value); },
              spellCheck: false,
            }) : null,
            !markdownLoading && !markdownError && !textMode ? React.createElement(MarkdownPreview, { markdown: markdownDraft || markdown }) : null
          )
        )
      );
    }

    return React.createElement("div", { className: "skill-preload-page" },
      React.createElement("div", { className: "skill-preload-hero" },
        React.createElement("div", null,
          React.createElement("h1", null, "Startup Frontmatter"),
          React.createElement("p", null,
            React.createElement("strong", null, "Enabled"),
            " = skill is available to tools. ",
            React.createElement("strong", null, "Inject"),
            " = only that skill's frontmatter/catalog entry is added to the new-session system prompt. Turning Inject off keeps the skill available via skills_list, skill_view, and skill_manage."
          )
        ),
        React.createElement("div", { className: "skill-preload-stats" },
          React.createElement(Badge, { variant: "secondary" }, skills.length + " enabled skills"),
          React.createElement(Badge, { variant: "secondary" }, injectedCount + " injected at startup")
        )
      ),
      React.createElement("div", { className: "skill-preload-controls" },
        React.createElement(Input, {
          value: search,
          onChange: function (event) { setSearch(event.target.value); },
          placeholder: "Search enabled skills...",
        })
      ),
      error ? React.createElement("div", { className: "skill-preload-error" }, error) : null,
      loading ? React.createElement("div", { className: "skill-preload-muted" }, "Loading...") : null,
      !loading && byCategory.length === 0 ? React.createElement("div", { className: "skill-preload-muted" }, "No enabled skills found.") : null,
      byCategory.map(function (entry) {
        const category = entry[0];
        const items = entry[1];
        return React.createElement(Card, { key: category, className: "skill-preload-card" },
          React.createElement(CardContent, { className: "skill-preload-card-content" },
            React.createElement("div", { className: "skill-preload-category" }, category),
            items.sort(function (a, b) { return String(a.name).localeCompare(String(b.name)); }).map(function (skill) {
              const busy = toggling.has(skill.name);
              const injected = isInjected(skill);
              return React.createElement("div", {
                key: skill.name,
                className: "skill-preload-row",
              },
                React.createElement("div", { className: "skill-preload-main" },
                  React.createElement("div", { className: "skill-preload-name-line" },
                    React.createElement("span", { className: "skill-preload-name" }, skill.name),
                    React.createElement(Badge, { variant: "outline" }, "Enabled"),
                    React.createElement(Badge, { variant: injected ? "default" : "secondary" }, injected ? "Frontmatter injected" : "Frontmatter hidden")
                  ),
                  React.createElement("div", { className: "skill-preload-path-row" },
                    React.createElement("span", null, "Path"),
                    React.createElement("code", null, skill.skill_path || skill.name)
                  ),
                  React.createElement("button", {
                    type: "button",
                    className: "skill-preload-description-button",
                    onClick: function () {
                      loadMarkdown(skill);
                    },
                    title: "Open SKILL.md markdown",
                  }, skill.description || "No description")
                ),
                React.createElement("button", {
                  className: "skill-preload-toggle " + (injected ? "is-on" : "is-off") + (busy ? " is-busy" : ""),
                  onClick: function () { toggle(skill); },
                  disabled: busy,
                  role: "switch",
                  "aria-checked": injected,
                  title: injected ? "Click to stop injecting this skill's frontmatter at startup" : "Click to inject this skill's frontmatter at startup",
                }, injected ? "Inject On" : "Inject Off")
              );
            })
          )
        );
      })
    );
  }

  registry.register("skill-preload", SkillPreloadPage);
})();
