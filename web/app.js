(() => {
  "use strict";

  const POLL_INTERVAL_MS = 850;
  const TERMINAL_STATES = new Set(["complete", "completed", "done"]);
  const FAILED_STATES = new Set(["error", "failed"]);
  const CLASSIFICATIONS = new Set(["core", "supporting", "unrelated"]);
  const VERDICTS = new Set(["in_scope", "minor_drift", "scope_creep"]);

  const ui = {
    form: document.querySelector("[data-check-form]"),
    input: document.querySelector("[data-pr-input]"),
    help: document.querySelector("[data-form-help]"),
    submit: document.querySelector("[data-check-submit]"),
    submitLabel: document.querySelector("[data-submit-label]"),
    submitSpinner: document.querySelector("[data-submit-spinner]"),
    error: document.querySelector("[data-form-error]"),
    panel: document.querySelector("[data-review-panel]"),
    empty: document.querySelector("[data-empty-state]"),
    prReference: document.querySelector("[data-pr-reference]"),
    title: document.querySelector("[data-pr-title]"),
    cancel: document.querySelector("[data-cancel-check]"),
    resultCard: document.querySelector("[data-result-card]"),
    verdict: document.querySelector("[data-verdict-badge]"),
    scoreLabel: document.querySelector("[data-score-label]"),
    score: document.querySelector("[data-drift-score]"),
    summary: document.querySelector("[data-result-summary]"),
    scoreRing: document.querySelector("[data-score-ring]"),
    progressCopy: document.querySelector("[data-progress-copy]"),
    groups: document.querySelector("[data-file-groups]"),
    fileCount: document.querySelector("[data-file-count]"),
    themeToggle: document.querySelector("[data-theme-toggle]"),
    themeLabel: document.querySelector("[data-theme-label]"),
    tierLabel: document.querySelector("[data-tier-label]"),
    confidenceLabel: document.querySelector("[data-confidence-label]"),
    spendLabel: document.querySelector("[data-spend-label]"),
    ledgerReceipt: document.querySelector("[data-ledger-receipt]"),
    ledgerDetail: document.querySelector("[data-ledger-detail]")
  };

  const noopMotion = {
    init() {},
    enhanceCards() {},
    typewrite(element, text) {
      element.textContent = text;
      return Promise.resolve();
    },
    animateRing(container, score) {
      const value = container.querySelector("[data-ring-value]");
      if (value) value.textContent = String(score);
    },
    popVerdict() {}
  };

  const motion = Object.assign({}, noopMotion, window.ScopeCreepMotion || {});

  const state = {
    active: null,
    theme: safeStorageGet("scopecreep-theme") || "system",
    cards: new Map(),
    reasons: new Map()
  };

  /*
   * The transport boundary is intentionally small. Replacing polling with SSE
   * or WebSocket delivery only requires changing start/status and poll().
   */
  const transport = {
    async start(pr, signal) {
      const response = await fetch("/api/check", {
        method: "POST",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ pr }),
        signal
      });

      if (!response.ok) {
        throw new Error(`Check request failed (${response.status})`);
      }

      const payload = await response.json();
      if (!payload || typeof payload.job_id !== "string") {
        throw new Error("The check service returned an invalid job.");
      }

      return {
        kind: "api",
        id: payload.job_id
      };
    },

    async status(job, signal) {
      if (job.kind === "simulation") {
        return job.simulator.next();
      }

      const response = await fetch(
        `/api/status/${encodeURIComponent(job.id)}`,
        {
          headers: { "Accept": "application/json" },
          signal
        }
      );

      if (!response.ok) {
        throw new Error(`Status request failed (${response.status})`);
      }

      return response.json();
    },

    simulated(reference) {
      return {
        kind: "simulation",
        id: `demo-${Date.now()}`,
        demoTitle: "Improve checkout flow and promotional-code handling",
        simulator: createSimulator(reference)
      };
    }
  };

  function safeStorageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (_) {
      return null;
    }
  }

  function safeStorageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (_) {
      // Theme persistence is optional in restricted browsing contexts.
    }
  }

  function parseReference(value) {
    const raw = value.trim();
    const shortForm = /^([\w.-]+)\/([\w.-]+)#(\d+)$/;
    const urlForm =
      /^(?:https?:\/\/)?(?:www\.)?github\.com\/([\w.-]+)\/([\w.-]+)\/pull\/(\d+)(?:[/?#].*)?$/i;
    const match = raw.match(shortForm) || raw.match(urlForm);

    if (!match) return null;

    return {
      raw,
      owner: match[1],
      repo: match[2],
      number: match[3],
      display: `${match[1]}/${match[2]}#${match[3]}`,
      fallbackTitle: `${match[1]}/${match[2]} pull request #${match[3]}`
    };
  }

  function setTheme(theme) {
    const supported = new Set(["system", "light", "dark"]);
    state.theme = supported.has(theme) ? theme : "system";
    document.documentElement.dataset.theme = state.theme;
    safeStorageSet("scopecreep-theme", state.theme);

    const media = window.matchMedia
      ? window.matchMedia("(prefers-color-scheme: dark)")
      : null;
    const isDark =
      state.theme === "dark" ||
      (state.theme === "system" && Boolean(media && media.matches));

    ui.themeLabel.textContent = isDark
      ? "Use light theme"
      : "Use dark theme";
  }

  function cycleTheme() {
    const next =
      state.theme === "system"
        ? "dark"
        : state.theme === "dark"
          ? "light"
          : "system";

    setTheme(next);
  }

  function setChecking(checking) {
    ui.submit.disabled = checking;
    ui.input.disabled = checking;
    ui.submitLabel.textContent = checking
      ? "Checking…"
      : "Check pull request";
    ui.submitSpinner.hidden = !checking;
    ui.cancel.hidden = !checking;
    ui.panel.setAttribute("aria-busy", String(checking));
  }

  function setError(message) {
    ui.error.textContent = message || "";
    ui.error.hidden = !message;
  }

  function setReceipt(ledger, footer) {
    if (!ledger || typeof ledger !== "object") {
      ui.tierLabel.textContent = "Cascade tier pending";
      ui.confidenceLabel.textContent = "";
      ui.spendLabel.textContent = "$0.00 API spend";
      ui.ledgerDetail.textContent = "";
      ui.ledgerReceipt.title = "No review ledger available yet";
      return;
    }

    const calls = Number.isFinite(Number(ledger.calls))
      ? Math.max(0, Number(ledger.calls))
      : 0;
    const escalations = Number.isFinite(Number(ledger.escalations))
      ? Math.max(0, Number(ledger.escalations))
      : 0;
    // tier0_held is an int count on the wire (see ledger_footer.py), not a
    // bool — tier 0 "held" means every call resolved without escalating.
    const tier0Held = calls > 0 && escalations === 0;
    const tier = tier0Held ? 0 : Math.max(1, escalations);

    const callLabel = `${calls} call${calls === 1 ? "" : "s"}`;
    const escalationLabel =
      `${escalations} escalation${escalations === 1 ? "" : "s"}`;
    // Prefer the backend's own footer_line() string (real, measured data);
    // fall back to the same wording for callers that don't send one (demo).
    const detail =
      typeof footer === "string" && footer
        ? footer
        : calls === 0
        ? "Judged without a single model call — $0.00 API spend."
        : `Judged in ${calls} cascade call${calls === 1 ? "" : "s"} on local models ` +
          `— tier 0 held ${Math.max(0, calls - escalations)}/${calls} calls, $0.00 API spend.`;

    ui.tierLabel.textContent = `Cascade tier: ${tier}`;
    ui.confidenceLabel.textContent = detail;
    ui.spendLabel.textContent =
      `$0.00 API spend · ${callLabel} · ${escalationLabel}`;
    ui.ledgerDetail.textContent = detail;
    ui.ledgerReceipt.title = detail;
  }

  function resetResult(reference) {
    state.cards.clear();
    state.reasons.clear();

    ui.empty.hidden = true;
    ui.panel.hidden = false;
    ui.resultCard.hidden = false;
    ui.verdict.hidden = true;
    ui.scoreLabel.hidden = true;
    ui.groups.replaceChildren();

    delete ui.resultCard.dataset.verdict;
    delete ui.verdict.dataset.verdict;

    ui.title.textContent = reference.fallbackTitle;
    ui.prReference.textContent = reference.display;
    ui.summary.textContent = "ScopeCreep is mapping the changed files.";
    ui.score.textContent = "0";
    ui.progressCopy.textContent = "Preparing a review map…";
    ui.fileCount.textContent = "0 files";

    motion.animateRing(ui.scoreRing, 0);
    motion.enhanceCards(ui.resultCard);
    setReceipt(null);
  }

  function makeSkeletonGroup(index) {
    const item = document.createElement("li");
    item.className = "file-group skeleton-group";
    item.dataset.skeleton = String(index);

    const header = document.createElement("div");
    header.className = "group-header";

    const title = document.createElement("span");
    title.className = "skeleton-line skeleton-title";

    const meta = document.createElement("span");
    meta.className = "skeleton-line skeleton-meta";

    header.append(title, meta);
    item.append(header);
    ui.groups.append(item);
  }

  function showSkeletons() {
    for (let index = 0; index < 3; index += 1) {
      makeSkeletonGroup(index);
    }
  }

  function clearSkeletons() {
    ui.groups
      .querySelectorAll("[data-skeleton]")
      .forEach((skeleton) => skeleton.remove());
  }

  function validClassification(value) {
    return CLASSIFICATIONS.has(value);
  }

  function validVerdict(value) {
    return VERDICTS.has(value);
  }

  function verdictLabel(verdict) {
    return verdict.replaceAll("_", " ");
  }

  function createGroupCard(group, index) {
    const item = document.createElement("li");
    item.className = "file-group";
    item.dataset.motion = "card";

    const header = document.createElement("div");
    header.className = "group-header";

    const heading = document.createElement("h4");
    heading.className = "group-name";

    const stats = document.createElement("span");
    stats.className = "group-stats";

    header.append(heading, stats);

    const files = document.createElement("ul");
    files.className = "group-files";

    const detail = document.createElement("div");
    detail.className = "group-detail";
    detail.hidden = true;

    const badge = document.createElement("span");
    badge.className = "classification-badge";

    const reason = document.createElement("p");
    reason.className = "classification-reason";
    reason.dataset.motion = "typewriter";

    detail.append(badge, reason);
    item.append(header, files, detail);

    const skeleton = ui.groups.querySelector(
      `[data-skeleton="${index}"]`
    );

    if (skeleton) {
      skeleton.replaceWith(item);
    } else {
      ui.groups.append(item);
    }

    const card = {
      item,
      heading,
      stats,
      files,
      detail,
      badge,
      reason
    };

    state.cards.set(group.group, card);
    motion.enhanceCards(item);

    return card;
  }

  function renderGroup(group, index) {
    if (
      !group ||
      typeof group.group !== "string" ||
      !Array.isArray(group.files)
    ) {
      return;
    }

    let card = state.cards.get(group.group);
    if (!card) {
      card = createGroupCard(group, index);
    }

    card.heading.textContent = group.group;

    const adds = Number.isFinite(Number(group.adds))
      ? Number(group.adds)
      : 0;
    const dels = Number.isFinite(Number(group.dels))
      ? Number(group.dels)
      : 0;
    const fileCount = group.files.length;

    card.stats.textContent =
      `${fileCount} file${fileCount === 1 ? "" : "s"} · ` +
      `+${adds} −${dels}`;

    card.files.replaceChildren();

    group.files.forEach((file) => {
      const row = document.createElement("li");
      row.className = "file-row";

      const path = document.createElement("code");
      path.className = "file-path";
      path.textContent = String(file);

      row.append(path);
      card.files.append(row);
    });

    if (validClassification(group.classification)) {
      card.item.dataset.classification = group.classification;
      card.badge.textContent = group.classification;
      card.detail.hidden = false;

      const reasonText =
        typeof group.reason === "string" ? group.reason : "";

      if (state.reasons.get(group.group) !== reasonText) {
        state.reasons.set(group.group, reasonText);
        motion.typewrite(card.reason, reasonText);
      }
    }
  }

  function renderProgress(progress) {
    const safeProgress = Array.isArray(progress) ? progress : [];
    safeProgress.forEach(renderGroup);

    const fileTotal = safeProgress.reduce((total, group) => {
      return total +
        (group && Array.isArray(group.files) ? group.files.length : 0);
    }, 0);

    const classified = safeProgress.filter((group) => {
      return group && validClassification(group.classification);
    }).length;

    ui.fileCount.textContent =
      `${fileTotal} file${fileTotal === 1 ? "" : "s"}`;

    ui.progressCopy.textContent = classified
      ? `Classified ${classified} of ${safeProgress.length} change groups…`
      : "Mapping changed files…";
  }

  function renderResult(result) {
    if (!result || !validVerdict(result.verdict)) {
      return false;
    }

    const score = Math.max(
      0,
      Math.min(100, Number(result.drift_score) || 0)
    );
    const verdict = result.verdict;

    clearSkeletons();

    ui.resultCard.dataset.verdict = verdict;
    ui.verdict.dataset.verdict = verdict;
    ui.verdict.textContent = verdictLabel(verdict);
    ui.verdict.hidden = false;
    ui.scoreLabel.hidden = false;
    ui.score.textContent = String(score);
    ui.summary.textContent =
      typeof result.summary === "string" ? result.summary : "";

    ui.scoreRing.setAttribute(
      "aria-label",
      `Drift score ${score} out of 100`
    );

    motion.animateRing(ui.scoreRing, score);
    motion.popVerdict(ui.verdict);
    setReceipt(result.ledger, result.footer);

    return true;
  }

  function renderPacket(packet) {
    renderProgress(packet && packet.progress);
    return Boolean(packet && renderResult(packet.result));
  }

  function cancelActive() {
    if (!state.active) return;

    state.active.cancelled = true;
    state.active.controller.abort();
    state.active = null;

    clearSkeletons();
    setChecking(false);
    ui.progressCopy.textContent =
      "Check cancelled. Any completed classifications remain visible.";
  }

  function pause(signal) {
    return new Promise((resolve, reject) => {
      const onAbort = () => {
        window.clearTimeout(timeout);
        reject(new DOMException("Cancelled", "AbortError"));
      };

      const timeout = window.setTimeout(() => {
        signal.removeEventListener("abort", onAbort);
        resolve();
      }, POLL_INTERVAL_MS);

      signal.addEventListener("abort", onAbort, { once: true });
    });
  }

  async function poll(job, run) {
    while (!run.cancelled && state.active === run) {
      const packet = await transport.status(
        job,
        run.controller.signal
      );

      if (run.cancelled || state.active !== run) return;

      const hasResult = renderPacket(packet);
      const packetState =
        packet && typeof packet.state === "string"
          ? packet.state.toLowerCase()
          : "";
      const terminal = TERMINAL_STATES.has(packetState);
      const failed = FAILED_STATES.has(packetState);

      if (failed) {
        throw new Error(
          (packet && typeof packet.error === "string" && packet.error) ||
            "The reviewer could not complete this check."
        );
      }

      if (terminal || hasResult) {
        clearSkeletons();

        ui.progressCopy.textContent = hasResult
          ? "Review complete."
          : "Check completed without a valid verdict.";

        state.active = null;
        setChecking(false);
        return;
      }

      await pause(run.controller.signal);
    }
  }

  async function beginCheck(event) {
    event.preventDefault();

    const reference = parseReference(ui.input.value);

    if (!reference) {
      setError(
        "Enter a GitHub PR URL or a reference like owner/repository#123."
      );
      ui.input.focus();
      return;
    }

    cancelActive();
    setError("");
    resetResult(reference);
    showSkeletons();

    const run = {
      controller: new AbortController(),
      cancelled: false
    };

    state.active = run;
    setChecking(true);

    try {
      let job;

      try {
        job = await transport.start(
          reference.raw,
          run.controller.signal
        );
      } catch (error) {
        if (error && error.name === "AbortError") throw error;
        // Only mask a failure with fake data when there's no real backend to
        // fail — a live-but-erroring server should surface the error, not a
        // fabricated verdict.
        if (window.location.protocol !== "file:") throw error;

        job = transport.simulated(reference);
        ui.title.textContent = job.demoTitle;
        ui.progressCopy.textContent =
          "API unavailable — running the local demonstration.";
      }

      await poll(job, run);
    } catch (error) {
      if (
        (error && error.name === "AbortError") ||
        run.cancelled
      ) {
        return;
      }

      state.active = null;
      clearSkeletons();
      setChecking(false);

      ui.progressCopy.textContent =
        "Check stopped before a verdict was available.";
      setError(
        error && error.message
          ? error.message
          : "Something went wrong while checking that pull request."
      );
    }
  }

  function createSimulator() {
    const groups = [
      {
        group: "Feature behavior",
        files: [
          "src/routes/checkout.ts",
          "src/components/PromoCode.tsx"
        ],
        adds: 116,
        dels: 18,
        classification: null,
        reason: null
      },
      {
        group: "Shared configuration",
        files: [
          "package.json",
          "src/config/flags.ts"
        ],
        adds: 24,
        dels: 7,
        classification: null,
        reason: null
      },
      {
        group: "Test coverage",
        files: [
          "src/routes/checkout.test.tsx"
        ],
        adds: 89,
        dels: 4,
        classification: null,
        reason: null
      }
    ];

    let step = 0;

    return {
      next() {
        step += 1;

        if (step === 1) {
          return {
            state: "running",
            progress: groups.map((group) => ({ ...group })),
            result: null
          };
        }

        if (step === 2) {
          groups[0] = {
            ...groups[0],
            classification: "core",
            reason:
              "The checkout changes directly implement the pull request’s stated user-flow objective."
          };

          return {
            state: "running",
            progress: groups.map((group) => ({ ...group })),
            result: null
          };
        }

        if (step === 3) {
          groups[1] = {
            ...groups[1],
            classification: "unrelated",
            reason:
              "The feature-flag and dependency changes widen deployment behavior beyond the checkout interaction."
          };

          return {
            state: "running",
            progress: groups.map((group) => ({ ...group })),
            result: null
          };
        }

        groups[2] = {
          ...groups[2],
          classification: "supporting",
          reason:
            "The added tests validate the intended checkout behavior and its fallback path."
        };

        return {
          state: "completed",
          progress: groups.map((group) => ({ ...group })),
          result: {
            verdict: "minor_drift",
            drift_score: 38,
            summary:
              "Most changes are in scope. Review the shared configuration group before merging.",
            // tier0_held is an int count on the real wire (ledger_footer.py),
            // never a bool — keep the demo data shaped like production so
            // this class of bug can't hide behind demo mode again.
            ledger: {
              calls: 5,
              tier0_held: 5,
              escalations: 0
            },
            footer:
              "Judged in 5 cascade calls on local models " +
              "— tier 0 held 5/5 calls, $0.00 API spend."
          }
        };
      }
    };
  }

  ui.form.addEventListener("submit", beginCheck);
  ui.cancel.addEventListener("click", cancelActive);
  ui.themeToggle.addEventListener("click", cycleTheme);

  if (window.matchMedia) {
    const colorMedia = window.matchMedia(
      "(prefers-color-scheme: dark)"
    );

    const onColorChange = () => {
      if (state.theme === "system") {
        setTheme("system");
      }
    };

    if (typeof colorMedia.addEventListener === "function") {
      colorMedia.addEventListener("change", onColorChange);
    } else if (typeof colorMedia.addListener === "function") {
      colorMedia.addListener(onColorChange);
    }
  }

  if (window.location.protocol === "file:" && !ui.input.value) {
    ui.input.value = "octocat/example#42";
    ui.help.textContent =
      "Standalone demo ready — submit the prefilled pull request.";
  }

  setTheme(state.theme);
  motion.init(document);
})();
