(() => {
  const parseNumber = (value) => {
    if (value === undefined || value === null || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const formatPlanPrice = (plan) => {
    if (plan.price_label) return plan.price_label;
    if (plan.price !== undefined && plan.currency) {
      return `${plan.price} ${plan.currency} / мес`;
    }
    return "уточняйте на сайте";
  };

  const formatPlanSpecs = (plan) => {
    const parts = [];
    if (plan.cpu) parts.push(`${plan.cpu} vCPU`);
    if (plan.ram_gb) parts.push(`${plan.ram_gb} GB RAM`);
    if (plan.storage_gb) parts.push(`${plan.storage_gb} GB disk`);
    if (plan.traffic_unlimited) parts.push("трафик ∞");
    else if (plan.traffic_tb) parts.push(`${plan.traffic_tb} TB`);
    return parts.join(" · ") || "конфигурация на сайте";
  };

  const escapeHtml = (value) =>
    String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");

  const flagImg = (country, className = "market-flag-img", size = { width: 16, height: 12 }) => {
    if (!country?.flag_url) return "";
    return `<img class="${className}" src="${escapeHtml(country.flag_url)}" alt="${escapeHtml(country.name || country.code)}" title="${escapeHtml(country.name || country.code)}" width="${size.width}" height="${size.height}" loading="lazy">`;
  };

  window.initProvidersMarket = (config) => {
    const providers = config.providers || [];
    const countries = config.countries || [];
    const promos = config.promos || [];
    const notice = config.notice || "";
    const pricesAsOf = config.pricesAsOf || "";

    const countryScroll = document.querySelector("#market-countries");
    const searchInput = document.querySelector("#market-search");
    const grid = document.querySelector("#market-grid");
    const listView = document.querySelector("#market-list-view");
    const detailView = document.querySelector("#market-detail-view");
    const detailRoot = document.querySelector("#market-detail");
    const emptyState = document.querySelector("#market-empty");
    const promosRoot = document.querySelector("#market-promos");
    const backButton = document.querySelector("#market-back");

    const filterFields = {
      priceMin: document.querySelector("#market-filter-price-min"),
      priceMax: document.querySelector("#market-filter-price-max"),
      currency: document.querySelector("#market-filter-currency"),
      ramMin: document.querySelector("#market-filter-ram-min"),
      ramMax: document.querySelector("#market-filter-ram-max"),
      cpuMin: document.querySelector("#market-filter-cpu-min"),
      cpuMax: document.querySelector("#market-filter-cpu-max"),
      trafficMin: document.querySelector("#market-filter-traffic-min"),
      trafficMax: document.querySelector("#market-filter-traffic-max"),
      api: document.querySelector("#market-filter-api"),
    };
    const resetFilters = document.querySelector("#market-filter-reset");

    if (!grid || !listView || !detailView || !detailRoot) return;

    let activeCountry = "";

    const readFilters = () => ({
      query: (searchInput?.value || "").trim().toLowerCase(),
      country: activeCountry,
      currency: filterFields.currency?.value || "",
      priceMin: parseNumber(filterFields.priceMin?.value),
      priceMax: parseNumber(filterFields.priceMax?.value),
      ramMin: parseNumber(filterFields.ramMin?.value),
      ramMax: parseNumber(filterFields.ramMax?.value),
      cpuMin: parseNumber(filterFields.cpuMin?.value),
      cpuMax: parseNumber(filterFields.cpuMax?.value),
      trafficMin: parseNumber(filterFields.trafficMin?.value),
      trafficMax: parseNumber(filterFields.trafficMax?.value),
      apiOnly: Boolean(filterFields.api?.checked),
    });

    const hasPlanFilters = (filters) =>
      filters.currency ||
      filters.priceMin !== null ||
      filters.priceMax !== null ||
      filters.ramMin !== null ||
      filters.ramMax !== null ||
      filters.cpuMin !== null ||
      filters.cpuMax !== null ||
      filters.trafficMin !== null ||
      filters.trafficMax !== null;

    const planMatches = (plan, filters) => {
      const price = parseNumber(plan.price);
      const ram = parseNumber(plan.ram_gb);
      const cpu = parseNumber(plan.cpu);
      const traffic = plan.traffic_unlimited ? null : parseNumber(plan.traffic_tb);

      if (filters.currency && plan.currency && plan.currency !== filters.currency) return false;
      if (filters.priceMin !== null) {
        if (price === null || price < filters.priceMin) return false;
      }
      if (filters.priceMax !== null) {
        if (price === null || price > filters.priceMax) return false;
      }
      if (filters.ramMin !== null) {
        if (ram === null || ram < filters.ramMin) return false;
      }
      if (filters.ramMax !== null) {
        if (ram === null || ram > filters.ramMax) return false;
      }
      if (filters.cpuMin !== null) {
        if (cpu === null || cpu < filters.cpuMin) return false;
      }
      if (filters.cpuMax !== null) {
        if (cpu === null || cpu > filters.cpuMax) return false;
      }
      if (filters.trafficMin !== null) {
        if (plan.traffic_unlimited) {
          /* unlimited passes any minimum */
        } else if (traffic === null || traffic < filters.trafficMin) {
          return false;
        }
      }
      if (filters.trafficMax !== null) {
        if (plan.traffic_unlimited) {
          /* unlimited exceeds explicit max filter */
          return false;
        }
        if (traffic === null || traffic > filters.trafficMax) return false;
      }
      return true;
    };

    const providerMatches = (provider, filters) => {
      if (filters.country && !(provider.countries || []).includes(filters.country)) return false;
      if (filters.query) {
        const haystack = `${provider.name} ${provider.domain}`.toLowerCase();
        if (!haystack.includes(filters.query)) return false;
      }
      if (filters.apiOnly && !(provider.has_api || provider.api_docs_url)) return false;
      if (hasPlanFilters(filters)) {
        return (provider.plans || []).some((plan) => planMatches(plan, filters));
      }
      if (filters.currency) {
        return (
          provider.default_currency === filters.currency ||
          (provider.plan_currencies || []).includes(filters.currency)
        );
      }
      return true;
    };

    const filteredProviders = () => {
      const filters = readFilters();
      return providers.filter((provider) => providerMatches(provider, filters));
    };

    const renderPromos = () => {
      if (!promosRoot) return;
      if (!promos.length) {
        promosRoot.innerHTML =
          '<p class="market-promo-empty muted">Партнёрские предложения и промокоды появятся здесь.</p>';
        return;
      }
      promosRoot.innerHTML = promos
        .map((promo) => {
          const url = promo.referral_url || promo.website_url || "#";
          const sponsored = promo.sponsored ? '<span class="market-badge">Партнёр</span>' : "";
          const code = promo.promo_code
            ? `<span class="market-promo-code">${escapeHtml(promo.promo_code)}</span>`
            : "";
          return `
            <article class="market-promo-card">
              <div>
                ${sponsored}
                <strong>${escapeHtml(promo.title || promo.provider_domain || "Предложение")}</strong>
                <p>${escapeHtml(promo.text || "")}</p>
                ${code}
              </div>
              <a class="secondary" href="${escapeHtml(url)}" target="_blank" rel="noreferrer sponsored">Подробнее</a>
            </article>
          `;
        })
        .join("");
    };

    const renderBadges = (provider) => {
      const badges = [];
      if (provider.api_docs_url) badges.push('<span class="market-badge market-badge-api">API docs</span>');
      if (provider.integration_type === "billmanager") {
        badges.push('<span class="market-badge">BILLmanager</span>');
      }
      if (provider.sponsored) badges.push('<span class="market-badge market-badge-partner">Партнёр</span>');
      return badges.join("");
    };

    const renderGrid = () => {
      const items = filteredProviders();
      grid.innerHTML = items
        .map(
          (provider) => `
          <button type="button" class="market-card" data-domain="${escapeHtml(provider.domain)}">
            <span class="market-card-top">
              <strong class="market-card-name">${escapeHtml(provider.name)}</strong>
              <span class="market-card-badges">${renderBadges(provider)}</span>
            </span>
            <span class="market-card-domain">${escapeHtml(provider.domain)}</span>
            <span class="market-card-price">${escapeHtml(provider.price_hint || "")}</span>
            <span class="market-card-flags">${(provider.country_labels || [])
              .slice(0, 10)
              .map((country) => flagImg(country, "market-card-flag-img"))
              .join("")}</span>
          </button>
        `,
        )
        .join("");
      emptyState?.classList.toggle("hidden", items.length > 0);
    };

    const renderDetail = (provider) => {
      const promo = provider.promo_text
        ? `<p class="market-detail-promo">${escapeHtml(provider.promo_text)}</p>`
        : "";
      const api = provider.api_docs_url
        ? `<a class="secondary-link" href="${escapeHtml(provider.api_docs_url)}" target="_blank" rel="noreferrer">API docs</a>`
        : "";
      const plans = (provider.plans || [])
        .map(
          (plan) => `
          <article class="market-plan">
            <strong>${escapeHtml(plan.name || "Тариф")}</strong>
            <span class="market-plan-price">${escapeHtml(formatPlanPrice(plan))}</span>
            <span class="market-plan-specs">${escapeHtml(formatPlanSpecs(plan))}</span>
          </article>
        `,
        )
        .join("");
      const visitRel = provider.sponsored ? "noreferrer sponsored" : "noreferrer";
      const priceNote = pricesAsOf
        ? `Цены ориентиры на ${escapeHtml(pricesAsOf)}. Проверяйте актуальность на сайте провайдера.`
        : "Цены — ориентиры с сайта провайдера. Актуальность зависит от даты обновления каталога.";
      detailRoot.innerHTML = `
        <div class="market-detail-head">
          <div>
            <span class="market-card-domain">${escapeHtml(provider.domain)}</span>
            <h2>${escapeHtml(provider.name)}</h2>
            <p class="market-detail-flags">${(provider.country_labels || [])
              .map((country) => `${flagImg(country, "market-flag-img")}<span>${escapeHtml(country.name)}</span>`)
              .join("")}</p>
            <p class="muted">${escapeHtml(provider.notes || "")}</p>
            ${promo}
          </div>
          <div class="market-detail-badges">${renderBadges(provider)}</div>
        </div>
        <div class="market-plans">${plans}</div>
        <p class="muted market-plan-note">${priceNote}</p>
        <div class="market-detail-actions">
          <a class="primary" href="/?add=server&template=${encodeURIComponent(provider.domain)}">Создать сервер</a>
          <a class="secondary" href="${escapeHtml(provider.visit_url || provider.website_url || "#")}" target="_blank" rel="${visitRel}">На сайт провайдера</a>
          ${api}
        </div>
      `;
    };

    const showList = () => {
      listView.hidden = false;
      detailView.hidden = true;
    };

    const showDetail = (domain) => {
      const provider = providers.find((item) => item.domain === domain);
      if (!provider) return;
      renderDetail(provider);
      listView.hidden = true;
      detailView.hidden = false;
    };

    const refreshList = () => {
      renderGrid();
      showList();
    };

    countryScroll.innerHTML = [
      `<button type="button" class="market-country active" data-country="" title="Все страны"><img class="market-flag-img market-flag-img-all" src="/static/flags/world.svg" alt="Все" width="16" height="12"></button>`,
      ...countries.map(
        (country) =>
          `<button type="button" class="market-country" data-country="${escapeHtml(country.code)}" title="${escapeHtml(country.name)}">${flagImg(country, "market-flag-img", { width: 16, height: 12 })}<span class="market-country-code">${escapeHtml(country.code)}</span></button>`,
      ),
    ].join("");

    countryScroll.querySelectorAll("[data-country]").forEach((button) => {
      button.addEventListener("click", () => {
        activeCountry = button.dataset.country || "";
        countryScroll.querySelectorAll(".market-country").forEach((node) => {
          node.classList.toggle("active", node === button);
        });
        refreshList();
      });
    });

    const bindFilter = (node) => {
      node?.addEventListener("input", refreshList);
      node?.addEventListener("change", refreshList);
    };
    Object.values(filterFields).forEach(bindFilter);
    searchInput?.addEventListener("input", refreshList);

    resetFilters?.addEventListener("click", () => {
      activeCountry = "";
      if (searchInput) searchInput.value = "";
      Object.entries(filterFields).forEach(([key, node]) => {
        if (!node) return;
        if (key === "api") node.checked = false;
        else node.value = "";
      });
      countryScroll.querySelectorAll(".market-country").forEach((node, index) => {
        node.classList.toggle("active", index === 0);
      });
      refreshList();
    });

    grid.addEventListener("click", (event) => {
      const card = event.target.closest(".market-card");
      if (!card) return;
      showDetail(card.dataset.domain || "");
    });

    backButton?.addEventListener("click", showList);

    if (notice && document.querySelector("#market-catalog-notice")) {
      document.querySelector("#market-catalog-notice").textContent = notice;
    }

    renderPromos();
    renderGrid();

    const requested = new URLSearchParams(window.location.search).get("provider");
    if (requested && providers.some((item) => item.domain === requested)) {
      showDetail(requested);
      history.replaceState({}, "", "/providers");
    }
  };
})();
