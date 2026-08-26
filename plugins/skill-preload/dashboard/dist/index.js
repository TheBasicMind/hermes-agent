(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!SDK || !registry) return;

  const React = SDK.React;
  const h = React.createElement;
  const { useEffect, useMemo, useState } = SDK.hooks;
  const { Card, CardContent, Badge, Input, Button } = SDK.components;

  function advertised(skill) {
    return typeof skill.inject_frontmatter === "boolean"
      ? skill.inject_frontmatter
      : !!skill.preload;
  }

  function StartupSkillsPage() {
    const [skills, setSkills] = useState([]);
    const [query, setQuery] = useState("");
    const [busy, setBusy] = useState(new Set());
    const [error, setError] = useState("");
    const [detail, setDetail] = useState(null);

    useEffect(function () {
      SDK.fetchJSON("/api/skills/preload")
        .then(function (rows) { setSkills(Array.isArray(rows) ? rows : []); })
        .catch(function (err) { setError(String(err && err.message || err)); });
    }, []);

    const visible = useMemo(function () {
      const needle = query.trim().toLowerCase();
      if (!needle) return skills;
      return skills.filter(function (skill) {
        return [skill.name, skill.description, skill.category]
          .some(function (value) { return String(value || "").toLowerCase().includes(needle); });
      });
    }, [skills, query]);

    function showContent(skill) {
      SDK.fetchJSON("/api/skills/content?name=" + encodeURIComponent(skill.name))
        .then(function (payload) { setDetail(payload); })
        .catch(function (err) { setError(String(err && err.message || err)); });
    }

    function toggle(skill) {
      const next = !advertised(skill);
      setBusy(function (current) { return new Set(current).add(skill.name); });
      SDK.fetchJSON("/api/skills/preload/toggle", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: skill.name, enabled: next }),
      }).then(function () {
        setSkills(function (current) {
          return current.map(function (row) {
            return row.name === skill.name
              ? Object.assign({}, row, { inject_frontmatter: next, preload: next })
              : row;
          });
        });
      }).catch(function (err) {
        setError(String(err && err.message || err));
      }).finally(function () {
        setBusy(function (current) {
          const copy = new Set(current);
          copy.delete(skill.name);
          return copy;
        });
      });
    }

    if (detail) {
      return h("div", { className: "skill-preload-page" },
        h(Button, { variant: "outline", onClick: function () { setDetail(null); } }, "Back"),
        h("h1", null, detail.name),
        h("pre", { className: "skill-preload-markdown" }, detail.content || "")
      );
    }

    return h("div", { className: "skill-preload-page" },
      h("div", { className: "skill-preload-heading" },
        h("div", null,
          h("h1", null, "Startup Skills"),
          h("p", null, "These switches change prompt catalog visibility only. Every enabled skill remains explicitly loadable.")
        ),
        h(Badge, { variant: "secondary" }, skills.filter(advertised).length + " advertised")
      ),
      h(Input, {
        value: query,
        placeholder: "Search enabled skills",
        onChange: function (event) { setQuery(event.target.value); },
      }),
      error ? h("div", { className: "skill-preload-error" }, error) : null,
      h("div", { className: "skill-preload-list" }, visible.map(function (skill) {
        const on = advertised(skill);
        return h(Card, { key: skill.name },
          h(CardContent, { className: "skill-preload-row" },
            h("button", { className: "skill-preload-info", onClick: function () { showContent(skill); } },
              h("strong", null, skill.name),
              h("span", null, skill.description || "No description")
            ),
            h(Button, {
              variant: on ? "default" : "outline",
              disabled: busy.has(skill.name),
              role: "switch",
              "aria-checked": on,
              onClick: function () { toggle(skill); },
            }, on ? "Advertised" : "Hidden")
          )
        );
      }))
    );
  }

  registry.register("skill-preload", StartupSkillsPage);
})();
