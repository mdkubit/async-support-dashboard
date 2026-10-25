"use strict";

(() => {
  const grid = document.querySelector("#job-grid");
  const panel = document.querySelector("#feed-state");
  const stateTitle = document.querySelector("#state-title");
  const stateDetail = document.querySelector("#state-detail");
  const action = document.querySelector("#state-action");
  const summary = document.querySelector("#result-summary");
  const status = document.querySelector("#system-status");
  const search = document.querySelector("#search");
  const filters = [...document.querySelectorAll("[data-filter]")];
  const dateFormat = new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const sourceNames = new Set(["We Work Remotely", "Remotive"]);
  let jobs = [];
  let selected = "all";
  let phase = "loading";

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function normalize(row) {
    if (!row || typeof row !== "object" || Array.isArray(row)) return null;
    if (!["title", "company", "date_posted", "source_link"].every(key => typeof row[key] === "string" && row[key].trim())) return null;
    if (!Array.isArray(row.tags) || !row.tags.every(tag => typeof tag === "string")) return null;
    const timestamp = Date.parse(row.date_posted);
    if (!Number.isFinite(timestamp)) return null;
    let url;
    try { url = new URL(row.source_link); } catch { return null; }
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
    const tags = row.tags.map(tag => tag.toLowerCase());
    const sample = tags.some(tag => /sample listing|archived position|fictional/.test(tag)) || /sample listing|archived position/i.test(row.title) || url.hostname.endsWith(".invalid");
    return {
      ...row, timestamp, url: url.href, tagsLower: tags, sample,
      // A generic remote tag is not evidence of worldwide eligibility.
      global: tags.some(tag => ["worldwide", "global", "global remote", "anywhere", "work from anywhere"].includes(tag)),
      searchable: `${row.title} ${row.company} ${row.tags.join(" ")}`.toLowerCase(),
    };
  }

  function matches(job) {
    const channels = selected === "all" ||
      (selected === "chat" && job.tagsLower.some(tag => ["chat", "live chat", "live-chat"].includes(tag))) ||
      (selected === "email" && job.tagsLower.some(tag => ["email", "ticket", "tickets", "ticketing"].includes(tag))) ||
      (selected === "global" && job.global);
    return channels && job.searchable.includes(search.value.trim().toLowerCase());
  }

  function card(job) {
    const article = element("article", "job-card");
    const top = element("div", "card-top");
    const company = element("div", "company-identity");
    const initials = job.company.split(/\s+/).slice(0, 2).map(word => [...word][0] || "").join("").toUpperCase();
    const avatar = element("span", "company-avatar", initials);
    avatar.setAttribute("aria-hidden", "true");
    company.append(avatar, element("span", "company-name", job.company));
    const date = element("time", "job-age", dateFormat.format(job.timestamp));
    date.dateTime = job.date_posted;
    top.append(company, date);
    const title = element("h3", "", job.title);
    const location = element("p", "location");
    const icon = element("span", "", "◎");
    icon.setAttribute("aria-hidden", "true");
    location.append(icon, document.createTextNode(job.sample ? "Fictional preview · Not hiring" : job.global ? "Worldwide remote" : "Remote · Check location eligibility"));
    const tags = element("div", "tags");
    if (job.sample) tags.append(element("span", "tag sample", "Sample Listing"));
    const visibleTags = job.tags.filter(tag => !sourceNames.has(tag) && !["remote", "text-oriented", "explicit-non-voice"].includes(tag.toLowerCase()) && !/sample listing|archived position|fictional/i.test(tag)).slice(0, 4);
    for (const tag of visibleTags) {
      const channel = /^(chat|live.chat|email|tickets?|ticketing)$/i.test(tag);
      tags.append(element("span", channel ? "tag channel" : "tag", tag));
    }
    const footer = element("div", "card-footer");
    footer.append(element("span", "source", job.sample ? "Interface preview" : job.tags.find(tag => sourceNames.has(tag)) || new URL(job.url).hostname));
    if (job.sample) {
      footer.append(element("span", "sample-link", "Preview only"));
    } else {
      const link = element("a", "job-link", "View position");
      link.href = job.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.setAttribute("aria-label", `View ${job.title} at ${job.company} (opens in a new tab)`);
      const arrow = element("span", "", "↗");
      arrow.setAttribute("aria-hidden", "true");
      link.append(arrow);
      footer.append(link);
    }
    article.append(top, title, location, tags, footer);
    return article;
  }

  function showState(title, detail, buttonText = "") {
    panel.hidden = false;
    stateTitle.textContent = title;
    stateDetail.textContent = detail;
    action.hidden = !buttonText;
    action.textContent = buttonText;
  }

  function render() {
    if (phase !== "ready") return;
    const visible = jobs.filter(matches);
    grid.replaceChildren();
    panel.hidden = visible.length > 0;
    const realCount = visible.filter(job => !job.sample).length;
    const sampleCount = visible.length - realCount;
    summary.textContent = `${visible.length} ${visible.length === 1 ? "position" : "positions"} in view${sampleCount ? ` · ${sampleCount} sample${sampleCount === 1 ? "" : "s"}` : ""}`;
    document.querySelector("#sample-notice").hidden = sampleCount === 0;
    if (!visible.length) {
      const globalEmpty = selected === "global" && !jobs.some(job => job.global);
      showState(globalEmpty ? "No worldwide signals confirmed yet." : "No signals on this channel.",
        globalEmpty ? "These listings have no explicit worldwide tag. Explore all positions and check each role’s location requirements." : "Try another channel or a different search. Your next connection may be one filter away.",
        selected !== "all" || search.value ? "Reset filters" : "Refresh feed");
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const job of visible) fragment.append(card(job));
    grid.append(fragment);
  }

  function selectFilter(value) {
    selected = value;
    for (const button of filters) {
      const active = button.dataset.filter === selected;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    }
    render();
  }

  async function loadJobs() {
    phase = "loading";
    grid.setAttribute("aria-busy", "true");
    grid.replaceChildren();
    document.querySelector("#sample-notice").hidden = true;
    status.replaceChildren(element("i"), document.createTextNode("Connecting"));
    status.dataset.state = "loading";
    summary.textContent = "Establishing connection…";
    showState("Scanning data nodes...", "Connecting you with the next opportunity.");
    stateTitle.classList.add("loading-dots");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch("./jobs.json", { cache: "no-cache", signal: controller.signal });
      if (!response.ok) throw new Error(`Feed returned HTTP ${response.status}`);
      const data = await response.json();
      if (!Array.isArray(data)) throw new Error("Feed must be an array");
      jobs = data.map(normalize).filter(Boolean).sort((a, b) => b.timestamp - a.timestamp);
      if (data.length && !jobs.length) throw new Error("Feed has no valid records");
      phase = "ready";
      const liveCount = jobs.filter(job => !job.sample).length;
      document.querySelector("#live-count").textContent = liveCount;
      document.querySelector("#all-count").textContent = jobs.length;
      status.replaceChildren(element("i"), document.createTextNode(jobs.some(job => job.sample) ? "Preview data" : "Feed connected"));
      status.dataset.state = "ready";
      render();
    } catch (error) {
      phase = "error";
      document.querySelector("#live-count").textContent = "—";
      document.querySelector("#all-count").textContent = "—";
      status.replaceChildren(element("i"), document.createTextNode("Signal interrupted"));
      status.dataset.state = "error";
      summary.textContent = "The feed is temporarily unavailable.";
      showState("Connection interrupted.", "We couldn’t read the opportunity feed. Please try again in a moment.", "Try again");
      console.warn("Unable to load job feed:", error);
    } finally {
      clearTimeout(timeout);
      stateTitle.classList.remove("loading-dots");
      grid.setAttribute("aria-busy", "false");
    }
  }

  filters.forEach(button => button.addEventListener("click", () => selectFilter(button.dataset.filter)));
  search.addEventListener("input", render);
  action.addEventListener("click", () => {
    if (phase === "error" || (selected === "all" && !search.value)) loadJobs();
    else { search.value = ""; selectFilter("all"); }
  });
  loadJobs();
})();
