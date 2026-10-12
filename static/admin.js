/* AMUAA admin — shell interactions.
   Every behaviour is opt-in through a data attribute so a page missing markup
   simply stays inert rather than throwing. */
(function () {
    "use strict";

    const root = document.documentElement;
    const body = document.body;

    /* ---------------- Theme ---------------- */
    const themeBtn = document.getElementById("themeToggle");

    function setTheme(mode) {
        root.dataset.theme = mode;
        try { localStorage.setItem("amuaa-theme", mode); } catch (e) { /* private mode */ }
        if (themeBtn) themeBtn.setAttribute("aria-pressed", String(mode === "dark"));
    }

    if (themeBtn) {
        themeBtn.setAttribute("aria-pressed", String(root.dataset.theme === "dark"));
        themeBtn.addEventListener("click", () =>
            setTheme(root.dataset.theme === "dark" ? "light" : "dark"));
    }

    /* ---------------- Mobile sidebar ---------------- */
    const openNav = () => body.classList.add("nav-open");
    const closeNav = () => body.classList.remove("nav-open");

    document.querySelectorAll("[data-open-sidebar]").forEach(el =>
        el.addEventListener("click", openNav));
    document.querySelectorAll("[data-close-sidebar]").forEach(el =>
        el.addEventListener("click", closeNav));

    /* ---------------- Alert bell ---------------- */
    document.querySelectorAll("[data-bell]").forEach(wrap => {
        const toggle = wrap.querySelector("[data-bell-toggle]");
        if (!toggle) return;
        toggle.addEventListener("click", e => {
            e.stopPropagation();
            const open = wrap.classList.toggle("open");
            toggle.setAttribute("aria-expanded", String(open));
        });
        // Keep the panel alive when confirming a payment inside it.
        wrap.addEventListener("click", e => e.stopPropagation());
    });

    document.addEventListener("click", () => {
        document.querySelectorAll("[data-bell].open").forEach(wrap => {
            wrap.classList.remove("open");
            const t = wrap.querySelector("[data-bell-toggle]");
            if (t) t.setAttribute("aria-expanded", "false");
        });
    });

    /* ---------------- Keyboard ---------------- */
    document.addEventListener("keydown", e => {
        if (e.key !== "Escape") return;
        document.querySelectorAll("[data-bell].open").forEach(w => w.classList.remove("open"));
        closeNav();
    });

    /* ---------------- Row navigation ---------------- */
    document.addEventListener("click", e => {
        if (e.target.closest("a, button, input, select, textarea, label, form")) return;
        const row = e.target.closest("tr[data-href]");
        if (row) window.location.href = row.dataset.href;
    });

    /* ---------------- Submit-on-change controls ---------------- */
    document.querySelectorAll("[data-submit-on-change]").forEach(el =>
        el.addEventListener("change", () => el.form && el.form.submit()));

    /* ---------------- Debounced search ---------------- */
    document.querySelectorAll("[data-autosubmit]").forEach(form => {
        const input = form.querySelector("input[type='search'], input[name='q']");
        if (!input) return;
        const delay = parseInt(form.dataset.autosubmit, 10) || 500;
        let timer = null;

        // Do not fire when the field is untouched on load, or the user gets a
        // pointless request while typing their first character.
        const startValue = input.value;

        input.addEventListener("input", () => {
            clearTimeout(timer);
            if (input.value === startValue) return;
            timer = setTimeout(() => {
                form.classList.add("is-loading");
                form.submit();
            }, delay);
        });
        form.addEventListener("submit", () => form.classList.add("is-loading"));
    });

    /* ---------------- KPI count-up ---------------- */
    const easeOut = t => 1 - Math.pow(1 - t, 3);
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function renderCount(el, target, decimals) {
        el.textContent = target >= 1000
            ? "KES " + Math.round(target).toLocaleString()
            : target.toFixed(decimals);
    }

    function countUp(el) {
        const target = parseFloat(el.dataset.count);
        if (!isFinite(target)) return;
        const decimals = (el.dataset.count.split(".")[1] || "").length;
        if (reduceMotion) { renderCount(el, target, decimals); return; }
        const duration = 620;
        const t0 = performance.now();

        function frame(now) {
            const p = Math.min(1, (now - t0) / duration);
            renderCount(el, target * easeOut(p), decimals);
            if (p < 1) requestAnimationFrame(frame);
        }
        requestAnimationFrame(frame);
    }

    const counters = document.querySelectorAll("[data-count]");
    if (counters.length && "IntersectionObserver" in window) {
        const io = new IntersectionObserver((entries, obs) => {
            entries.forEach(entry => {
                if (!entry.isIntersecting) return;
                countUp(entry.target);
                obs.unobserve(entry.target);
            });
        }, { threshold: 0.4 });
        counters.forEach(el => io.observe(el));
    } else {
        counters.forEach(el => {
            const n = parseFloat(el.dataset.count);
            if (isFinite(n) && n >= 1000) el.textContent = "KES " + Math.round(n).toLocaleString();
        });
    }

    /* ---------------- Toast auto-dismiss ---------------- */
    document.querySelectorAll(".flash").forEach(el => {
        setTimeout(() => {
            el.style.transition = "opacity .3s, transform .3s";
            el.style.opacity = "0";
            el.style.transform = "translateX(16px)";
            setTimeout(() => el.remove(), 320);
        }, 4200);
    });
})();
