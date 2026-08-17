(() => {
  "use strict";

  const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";
  const animations = new Map();
  const enhancedCards = new Set();
  const reducedMedia = window.matchMedia
    ? window.matchMedia(REDUCED_QUERY)
    : null;

  function prefersReducedMotion() {
    return Boolean(reducedMedia && reducedMedia.matches);
  }

  function stopAnimation(element, jumpToEnd) {
    const active = animations.get(element);
    if (!active) return;

    animations.delete(element);
    active.cancel();

    if (jumpToEnd) {
      active.finish();
    }
  }

  function registerAnimation(element, animation) {
    stopAnimation(element, false);
    animations.set(element, animation);
  }

  function finishAllAnimations() {
    const activeAnimations = Array.from(animations.entries());
    animations.clear();

    activeAnimations.forEach(([, animation]) => {
      animation.cancel();
      animation.finish();
    });
  }

  function setAuroraState() {
    const aurora = document.querySelector('[data-motion="aurora"]');
    if (!aurora) return;

    aurora.dataset.aurora = prefersReducedMotion()
      ? "still"
      : "active";
  }

  function animateRing(container, rawScore) {
    if (!container) return;

    const progress = container.querySelector("[data-ring-progress]");
    const value = container.querySelector("[data-ring-value]");
    if (!progress || !value) return;

    stopAnimation(container, false);

    const score = Math.max(
      0,
      Math.min(100, Number(rawScore) || 0)
    );
    const radius = Number(progress.getAttribute("r")) || 50;
    const circumference = 2 * Math.PI * radius;

    progress.style.strokeDasharray = String(circumference);
    progress.style.strokeDashoffset = String(circumference);
    value.textContent = "0";

    const render = (amount) => {
      const eased = 1 - Math.pow(1 - amount, 3);
      const visibleScore = Math.round(score * eased);
      const offset =
        circumference - circumference * (score / 100) * eased;

      progress.style.strokeDashoffset = String(offset);
      value.textContent = String(visibleScore);
    };

    if (prefersReducedMotion() || score === 0) {
      render(1);
      return;
    }

    const duration = 980;
    const startedAt = performance.now();
    let frame = 0;

    const finish = () => {
      render(1);
    };

    const cancel = () => {
      window.cancelAnimationFrame(frame);
    };

    const step = (now) => {
      const elapsed = now - startedAt;
      const amount = Math.min(1, elapsed / duration);
      render(amount);

      if (amount < 1) {
        frame = window.requestAnimationFrame(step);
      } else {
        animations.delete(container);
        finish();
      }
    };

    registerAnimation(container, { cancel, finish });
    frame = window.requestAnimationFrame(step);
  }

  function typewrite(element, rawText) {
    if (!element) return Promise.resolve();

    const text = String(rawText || "");
    const characters = Array.from(text);

    stopAnimation(element, false);
    element.textContent = "";
    element.setAttribute("aria-busy", "true");

    const finish = () => {
      element.textContent = text;
      element.setAttribute("aria-busy", "false");
    };

    if (prefersReducedMotion() || characters.length === 0) {
      finish();
      return Promise.resolve();
    }

    return new Promise((resolve) => {
      const startedAt = performance.now();
      const characterDelay = 19;
      let frame = 0;
      let previousCount = 0;

      const complete = () => {
        finish();
        resolve();
      };

      const cancel = () => {
        window.cancelAnimationFrame(frame);
        element.setAttribute("aria-busy", "false");
        resolve();
      };

      const step = (now) => {
        const elapsed = now - startedAt;
        const count = Math.min(
          characters.length,
          Math.floor(elapsed / characterDelay) + 1
        );

        if (count !== previousCount) {
          element.textContent =
            characters.slice(0, count).join("");
          previousCount = count;
        }

        if (count < characters.length) {
          frame = window.requestAnimationFrame(step);
        } else {
          animations.delete(element);
          complete();
        }
      };

      registerAnimation(element, {
        cancel,
        finish: complete
      });

      frame = window.requestAnimationFrame(step);
    });
  }

  function popVerdict(element) {
    if (!element) return;

    stopAnimation(element, false);
    element.classList.remove("is-popping");
    element.dataset.motionState = "complete";

    if (prefersReducedMotion()) {
      return;
    }

    element.dataset.motionState = "animating";
    void element.offsetWidth;
    element.classList.add("is-popping");

    let timeout = 0;

    const finish = () => {
      window.clearTimeout(timeout);
      element.classList.remove("is-popping");
      element.dataset.motionState = "complete";
      element.removeEventListener("animationend", onAnimationEnd);
    };

    const cancel = () => {
      window.clearTimeout(timeout);
      element.classList.remove("is-popping");
      element.removeEventListener("animationend", onAnimationEnd);
    };

    const onAnimationEnd = (event) => {
      if (event.target !== element) return;
      animations.delete(element);
      finish();
    };

    registerAnimation(element, { cancel, finish });
    element.addEventListener("animationend", onAnimationEnd);
    timeout = window.setTimeout(() => {
      animations.delete(element);
      finish();
    }, 520);
  }

  function prepareCard(card) {
    if (!card || enhancedCards.has(card)) return;

    enhancedCards.add(card);
    card.dataset.hover = "false";

    const setHover = (active) => {
      card.dataset.hover =
        !prefersReducedMotion() && active ? "true" : "false";
    };

    card.addEventListener("pointerenter", () => setHover(true));
    card.addEventListener("pointerleave", () => setHover(false));
    card.addEventListener("focusin", () => setHover(true));
    card.addEventListener("focusout", (event) => {
      if (!card.contains(event.relatedTarget)) {
        setHover(false);
      }
    });

    if (!prefersReducedMotion()) {
      card.classList.add("is-arriving");

      const removeArrival = () => {
        card.classList.remove("is-arriving");
        card.removeEventListener("animationend", removeArrival);
      };

      card.addEventListener("animationend", removeArrival);
      window.setTimeout(removeArrival, 460);
    }
  }

  function enhanceCards(root) {
    if (!root) return;

    if (
      root.nodeType === Node.ELEMENT_NODE &&
      root.matches('[data-motion="card"]')
    ) {
      prepareCard(root);
    }

    if (typeof root.querySelectorAll === "function") {
      root
        .querySelectorAll('[data-motion="card"]')
        .forEach(prepareCard);
    }
  }

  function init(root) {
    setAuroraState();
    enhanceCards(root || document);
  }

  function onReducedMotionChange() {
    setAuroraState();

    if (prefersReducedMotion()) {
      finishAllAnimations();

      enhancedCards.forEach((card) => {
        card.dataset.hover = "false";
        card.classList.remove("is-arriving");
      });
    }
  }

  if (reducedMedia) {
    if (typeof reducedMedia.addEventListener === "function") {
      reducedMedia.addEventListener(
        "change",
        onReducedMotionChange
      );
    } else if (typeof reducedMedia.addListener === "function") {
      reducedMedia.addListener(onReducedMotionChange);
    }
  }

  window.ScopeCreepMotion = {
    init,
    animateRing,
    typewrite,
    popVerdict,
    enhanceCards,
    prefersReducedMotion,
    finishAll: finishAllAnimations
  };
})();
