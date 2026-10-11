(() => {
  const { api, esc, money, toast, statusLabel, formatDate } = Sky;
  const $ = (selector) => document.querySelector(selector);

  const header = $("#siteHeader");
  const brandTop = $("#brandTop");
  const menuToggle = $("#menuToggle");
  const cartBtn = $("#cartBtn");
  const cartCountEl = $("#cartCount");
  const productGrid = $("#productGrid");
  const filterRow = $("#categoryFilters");
  const accountBtn = $("#accountBtn");
  const accountWrap = $("#accountWrap");
  const accountMenu = $("#accountMenu");
  const authActions = $("#authActions");
  const signInBtn = $("#signInBtn");
  const signUpBtn = $("#signUpBtn");
  const productSearch = $("#productSearch");
  const filterToggle = $("#filterToggle");
  const advancedFilters = $("#advancedFilters");
  const minPrice = $("#minPrice");
  const maxPrice = $("#maxPrice");
  const inStockOnly = $("#inStockOnly");
  const clearFilters = $("#clearFilters");
  const authModal = $("#authModal");
  const authForm = $("#authForm");
  const checkoutModal = $("#checkoutModal");
  const checkoutForm = $("#checkoutForm");
  const ordersModal = $("#ordersModal");
  const ordersList = $("#ordersList");

  const CART_KEY = "skyeCraftsCart.v1";
  const LEGACY_CART_KEY = "skyefableCart.v2";
  const THUMBS = { Bouquets: 1, Keychains: 2, Pots: 3, Figurines: 4 };

  let state = { user: null, categories: [], products: [] };
  let cart = loadCart(); // [{id, qty}] — guests can shop before logging in
  let filter = "";
  let searchTerm = "";
  let advancedFiltersOpen = false;
  let didFocusProductsPage = false;
  let authMode = "login";
  let pendingCheckout = false; // resume checkout after logging in
  let appliedCoupon = null; // {code, discount_cents, subtotal_cents, total_cents}
  let focusReturnTarget = null;
  const isProductsPage = document.body.dataset.page === "products";
  const FEATURED_COUNT = 4;

  /* ---------------------------------------------------------------- cart */

  function loadCart() {
    try {
      const raw = JSON.parse(
        localStorage.getItem(CART_KEY) ||
          localStorage.getItem(LEGACY_CART_KEY) ||
          "[]",
      );
      return Array.isArray(raw)
        ? raw.filter(
            (item) =>
              Number.isInteger(item?.id) &&
              Number.isInteger(item?.qty) &&
              item.qty > 0,
          )
        : [];
    } catch {
      return [];
    }
  }
  function saveCart() {
    try {
      localStorage.setItem(CART_KEY, JSON.stringify(cart));
      localStorage.removeItem(LEGACY_CART_KEY);
    } catch {
      /* storage unavailable */
    }
  }

  const productById = (id) =>
    state.products.find((product) => product.id === id);

  function availability(product) {
    if (product.is_preorder)
      return { label: "Pre‑order", cls: "preorder", canBuy: true, max: 99 };
    if (product.stock <= 0)
      return { label: "Sold out", cls: "out", canBuy: false, max: 0 };
    const max = Math.min(99, product.stock);
    if (product.stock <= 5)
      return {
        label: `Only ${product.stock} left`,
        cls: "low",
        canBuy: true,
        max,
      };
    return { label: "In stock", cls: "", canBuy: true, max };
  }

  function cartCount() {
    return cart.reduce((sum, item) => sum + item.qty, 0);
  }
  function cartTotal() {
    return cart.reduce((sum, item) => {
      const product = productById(item.id);
      return sum + (product ? product.price_cents * item.qty : 0);
    }, 0);
  }
  function updateCartCount() {
    const count = cartCount();
    cartCountEl.textContent = count;
    cartBtn.setAttribute(
      "aria-label",
      `View cart, ${count} item${count === 1 ? "" : "s"}`,
    );
  }

  // Keep the saved cart in line with what the shop currently offers.
  function reconcileCart(quiet) {
    let changed = false;
    cart = cart.flatMap((item) => {
      const product = productById(item.id);
      if (!product) {
        changed = true;
        return [];
      }
      const { max } = availability(product);
      if (max === 0) {
        changed = true;
        return [];
      }
      const qty = Math.min(item.qty, max);
      if (qty !== item.qty) changed = true;
      return [{ id: item.id, qty }];
    });
    saveCart();
    updateCartCount();
    if (drawer.classList.contains("show")) renderCart();
    if (changed && !quiet)
      toast("Your cart was updated to match what’s available.");
  }

  function setQty(id, qty) {
    const product = productById(id);
    if (!product) return;
    qty = Math.max(0, Math.min(qty, availability(product).max));
    const index = cart.findIndex((item) => item.id === id);
    if (qty === 0) {
      if (index >= 0) cart.splice(index, 1);
    } else if (index >= 0) cart[index].qty = qty;
    else cart.push({ id, qty });
    saveCart();
    updateCartCount();
    appliedCoupon = null; // cart changed — any applied discount must be re-checked
    if (drawer.classList.contains("show")) renderCart();
  }

  function addProduct(id) {
    const product = productById(id);
    if (!product) return;
    const info = availability(product);
    const current = cart.find((item) => item.id === id)?.qty || 0;
    if (!info.canBuy) {
      toast(`${product.title} is sold out`);
      return;
    }
    if (current >= info.max) {
      toast(`Only ${info.max} available of ${product.title}`);
      return;
    }
    setQty(id, current + 1);
    cartCountEl.classList.remove("pop");
    void cartCountEl.offsetWidth;
    cartCountEl.classList.add("pop");
    toast(`${product.title} added to cart`);
  }

  /* ------------------------------------------------------------ overlays */

  function syncScrollLock() {
    document.body.classList.toggle(
      "no-scroll",
      !!document.querySelector(".modal-backdrop.show, .cart-drawer.show"),
    );
  }
  function openOverlay(element) {
    focusReturnTarget = document.activeElement;
    closeCart();
    element.classList.add("show");
    syncScrollLock();
    element.querySelector("input:not([type=radio]), textarea")?.focus();
  }
  function closeOverlay(element) {
    element.classList.remove("show");
    syncScrollLock();
    if (
      focusReturnTarget instanceof HTMLElement &&
      document.contains(focusReturnTarget)
    ) {
      focusReturnTarget.focus();
    }
    focusReturnTarget = null;
  }
  function closeAllOverlays() {
    document.querySelectorAll(".modal-backdrop.show").forEach(closeOverlay);
    closeCart();
  }

  document.addEventListener("click", (event) => {
    const closeButton = event.target.closest("[data-close]");
    if (closeButton) closeOverlay(closeButton.closest(".modal-backdrop"));
    else if (event.target.classList?.contains("modal-backdrop"))
      closeOverlay(event.target);

    if (!event.target.closest(".account-wrap")) hideAccountMenu();
  });

  /* ------------------------------------------------------ product modal */

  const productModal = document.createElement("div");
  productModal.className = "modal-backdrop";
  productModal.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
      <button class="modal-close" type="button" data-close aria-label="Close product details">×</button>
      <div class="modal-art">✿</div>
      <h3 id="modalTitle"></h3>
      <p id="modalDesc"></p>
      <div class="modal-buy">
        <div class="modal-price" id="modalPrice"></div>
        <span class="stock" id="modalStock"></span>
      </div>
      <button class="btn btn-primary" type="button" id="modalAdd" style="width:100%">Add to Cart</button>
      <div class="reviews-block" id="reviewsBlock">
        <div class="review-summary" id="reviewSummary"></div>
        <div class="review-list" id="reviewList"></div>
        <form class="review-form" id="reviewForm" hidden>
          <div class="star-rate" id="starRate">
            ${[1, 2, 3, 4, 5].map((n) => `<button type="button" data-star="${n}" aria-label="${n} star${n === 1 ? "" : "s"}">★</button>`).join("")}
          </div>
          <input type="hidden" name="rating" value="0">
          <button class="btn btn-secondary btn-sm" type="submit">Rate this</button>
        </form>
      </div>
    </div>`;
  document.body.appendChild(productModal);

  let activeProductId = null;

  async function loadReviews(id) {
    const summaryEl = $("#reviewSummary");
    const listEl = $("#reviewList");
    summaryEl.textContent = "Loading reviews…";
    listEl.innerHTML = "";
    try {
      const { reviews, count, average } = await api(
        `/api/products/${id}/reviews`,
      );
      summaryEl.innerHTML = count
        ? `<span class="stars">${"★".repeat(Math.round(average))}${"☆".repeat(5 - Math.round(average))}</span> ${average} · ${count} review${count === 1 ? "" : "s"}`
        : "No reviews yet — be the first to rate this piece.";
      listEl.innerHTML = reviews
        .map(
          (review) => `
        <div class="review-item">
          <strong>${esc(review.username)}</strong> · <span class="stars">${"★".repeat(review.rating)}${"☆".repeat(5 - review.rating)}</span>
          ${review.text ? `<div>${esc(review.text)}</div>` : ""}
        </div>`,
        )
        .join("");
    } catch {
      summaryEl.textContent = "Could not load reviews right now.";
    }
  }

  function openProduct(id) {
    const product = productById(id);
    if (!product) return;
    const info = availability(product);
    activeProductId = id;
    $("#modalTitle").textContent = product.title;
    $("#modalDesc").textContent = product.description;
    $("#modalPrice").textContent = money(product.price_cents);
    const stock = $("#modalStock");
    stock.textContent = info.label;
    stock.className = `stock ${info.cls}`.trim();
    const add = $("#modalAdd");
    add.disabled = !info.canBuy;
    add.textContent = info.canBuy ? "Add to Cart" : "Sold out";

    const reviewForm = $("#reviewForm");
    reviewForm.hidden = !state.user;
    reviewForm.reset();
    reviewForm.elements.rating.value = 0;
    $("#starRate")
      .querySelectorAll("button")
      .forEach((star) => star.classList.remove("active"));

    loadReviews(id);
    openOverlay(productModal);
  }
  $("#modalAdd").addEventListener("click", () => {
    if (activeProductId !== null) addProduct(activeProductId);
    closeOverlay(productModal);
  });

  $("#starRate").addEventListener("click", (event) => {
    const star = event.target.closest("[data-star]");
    if (!star) return;
    const value = Number(star.dataset.star);
    $("#reviewForm").elements.rating.value = value;
    $("#starRate")
      .querySelectorAll("button")
      .forEach((button) => {
        button.classList.toggle("active", Number(button.dataset.star) <= value);
      });
  });

  $("#reviewForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const rating = Number(event.currentTarget.elements.rating.value);
    if (!rating) {
      toast("Choose a star rating first.");
      return;
    }
    try {
      await api(`/api/products/${activeProductId}/reviews`, { rating });
      toast("Thanks for rating this piece!");
      loadReviews(activeProductId);
    } catch (error) {
      toast(error.message);
    }
  });

  /* --------------------------------------------------------- cart drawer */

  const drawer = document.createElement("aside");
  drawer.className = "cart-drawer";
  drawer.setAttribute("aria-label", "Shopping cart");
  drawer.innerHTML = `
    <div class="drawer-head"><h3>Your Cart</h3><button class="drawer-close" type="button" aria-label="Close cart">×</button></div>
    <div class="cart-items"></div>
    <div class="cart-footer">
      <div class="cart-total"><span>Total</span><span id="drawerTotal">₱0</span></div>
      <button class="btn btn-primary" type="button" style="width:100%" id="checkoutBtn">Checkout</button>
    </div>`;
  document.body.appendChild(drawer);

  function renderCart() {
    const rows = cart
      .map((item) => {
        const product = productById(item.id);
        if (!product) return "";
        const info = availability(product);
        return `
        <div class="cart-row">
          <div class="cart-info">
            <strong>${esc(product.title)}</strong>
            <small>${money(product.price_cents)}${info.cls === "preorder" ? " · Pre‑order" : ""}</small>
            <div class="qty">
              <button type="button" data-qty="${product.id}" data-delta="-1" aria-label="Decrease quantity of ${esc(product.title)}">−</button>
              <span>${item.qty}</span>
              <button type="button" data-qty="${product.id}" data-delta="1" aria-label="Increase quantity of ${esc(product.title)}" ${item.qty >= info.max ? "disabled" : ""}>+</button>
              <button type="button" class="remove" data-qty="${product.id}" data-delta="-999">Remove</button>
            </div>
          </div>
          <strong>${money(product.price_cents * item.qty)}</strong>
        </div>`;
      })
      .join("");

    drawer.querySelector(".cart-items").innerHTML =
      rows ||
      '<div class="cart-empty">Your cart is empty.<br>Choose a handmade piece to get started ♡</div>';
    drawer.querySelector("#drawerTotal").textContent = money(cartTotal());
  }
  function openCart() {
    renderCart();
    drawer.classList.add("show");
    syncScrollLock();
  }
  function closeCart() {
    drawer.classList.remove("show");
    syncScrollLock();
  }

  cartBtn.addEventListener("click", openCart);
  drawer.querySelector(".drawer-close").addEventListener("click", closeCart);
  drawer.addEventListener("click", (event) => {
    const button = event.target.closest("[data-qty]");
    if (!button) return;
    const id = Number(button.dataset.qty);
    const current = cart.find((item) => item.id === id)?.qty || 0;
    setQty(id, current + Number(button.dataset.delta));
  });
  drawer.querySelector("#checkoutBtn").addEventListener("click", startCheckout);

  /* ------------------------------------------------------------ products */

  function productCard(product) {
    const info = availability(product);
    const thumb = THUMBS[product.category] || (product.id % 4) + 1;
    return `
      <article class="product-card reveal" data-id="${product.id}">
        <div class="product-thumb thumb-${thumb}" role="img" aria-label="${esc(product.title)} illustration"></div>
        <h3>${esc(product.title)}</h3>
        <p class="product-desc">${esc(product.description)}</p>
        <div class="product-meta">
          <span class="price">${money(product.price_cents)}</span>
          <span class="stock ${info.cls}">${esc(info.label)}</span>
        </div>
        <div class="product-actions">
          <button class="view-btn" type="button" data-view="${product.id}">View Product</button>
          <button class="add-btn" type="button" data-add="${product.id}" ${info.canBuy ? "" : "disabled"}>${info.canBuy ? "Add to Cart" : "Sold out"}</button>
        </div>
      </article>`;
  }

  function renderFilters() {
    const catalogue = isProductsPage
      ? state.products
      : state.products.slice(0, FEATURED_COUNT);
    const categories = state.categories.filter((name) =>
      catalogue.some((product) => product.category === name),
    );
    if (filter && !categories.includes(filter)) filter = "";
    filterRow.hidden = categories.length < 2;
    filterRow.innerHTML = ["", ...categories]
      .map(
        (name) => `
      <button type="button" class="chip${name === filter ? " active" : ""}" data-filter="${esc(name)}" aria-pressed="${name === filter}">${name ? esc(name) : "All"}</button>
    `,
      )
      .join("");
  }

  function renderProducts() {
    const catalogue = isProductsPage
      ? state.products
      : state.products.slice(0, FEATURED_COUNT);
    const minimum = Number(minPrice.value || 0) * 100;
    const maximum =
      maxPrice.value === "" ? Infinity : Number(maxPrice.value) * 100;
    const list = catalogue.filter((product) => {
      const searchable =
        `${product.title} ${product.description} ${product.category}`.toLowerCase();
      const matchesSearch = !searchTerm || searchable.includes(searchTerm);
      const matchesCategory = !filter || product.category === filter;
      const matchesPrice =
        product.price_cents >= minimum && product.price_cents <= maximum;
      const matchesStock =
        !inStockOnly.checked || product.is_preorder || product.stock > 0;
      return matchesSearch && matchesCategory && matchesPrice && matchesStock;
    });
    productGrid.innerHTML = list.length
      ? list.map(productCard).join("")
      : '<p class="grid-note">No creations here yet — check back soon ♡</p>';
    observeReveal(productGrid);
  }

  filterRow.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-filter]");
    if (!chip) return;
    filter = chip.dataset.filter;
    renderFilters();
    renderProducts();
  });
  productSearch.addEventListener("input", () => {
    searchTerm = productSearch.value.trim().toLowerCase();
    renderProducts();
  });
  filterToggle.addEventListener("click", () => {
    advancedFiltersOpen = !advancedFiltersOpen;
    advancedFilters.hidden = !advancedFiltersOpen;
    filterToggle.setAttribute("aria-expanded", String(advancedFiltersOpen));
    filterToggle.classList.toggle("active", advancedFiltersOpen);
  });
  [minPrice, maxPrice, inStockOnly].forEach((control) =>
    control.addEventListener("input", renderProducts),
  );
  clearFilters.addEventListener("click", () => {
    searchTerm = "";
    filter = "";
    productSearch.value = "";
    minPrice.value = "";
    maxPrice.value = "";
    inStockOnly.checked = false;
    renderFilters();
    renderProducts();
  });
  productGrid.addEventListener("click", (event) => {
    const view = event.target.closest("[data-view]");
    const add = event.target.closest("[data-add]");
    if (view) openProduct(Number(view.dataset.view));
    if (add) addProduct(Number(add.dataset.add));
  });

  /* ------------------------------------------------------------- account */

  function renderAccount() {
    const user = state.user;
    accountWrap.hidden = !user;
    authActions.hidden = !!user;
    $("#accountIcon").style.display = user ? "none" : "";
    $("#accountInitial").hidden = !user;
    accountBtn.classList.toggle("has-user", !!user);
    accountBtn.setAttribute(
      "aria-label",
      user
        ? `Account menu for ${user.username}`
        : "Log in or create an account",
    );
    if (user) $("#accountInitial").textContent = user.username[0].toUpperCase();

    accountMenu.innerHTML = user
      ? `
      <p class="account-name">Signed in as <strong>${esc(user.username)}</strong></p>
      <button type="button" data-menu="orders">My orders</button>
      ${user.is_admin ? '<a href="/admin">Shop admin</a>' : ""}
      <button type="button" data-menu="logout">Log out</button>`
      : "";
    if (!user) hideAccountMenu();
  }
  function hideAccountMenu() {
    accountMenu.hidden = true;
    accountBtn.setAttribute("aria-expanded", "false");
  }

  accountBtn.addEventListener("click", () => {
    if (!state.user) {
      openAuth("login");
      return;
    }
    const willShow = accountMenu.hidden;
    accountMenu.hidden = !willShow;
    accountBtn.setAttribute("aria-expanded", String(willShow));
  });
  signInBtn.addEventListener("click", () => openAuth("login"));
  signUpBtn.addEventListener("click", () => openAuth("register"));
  accountMenu.addEventListener("click", (event) => {
    const item = event.target.closest("[data-menu]");
    if (!item) return;
    hideAccountMenu();
    if (item.dataset.menu === "orders") openOrders();
    if (item.dataset.menu === "logout") logout();
  });

  function setAuthMode(mode, intro) {
    authMode = mode;
    const registering = mode === "register";
    $("#authTitle").textContent = registering
      ? "Create your account"
      : "Welcome back";
    $("#authIntro").textContent =
      intro ||
      (registering
        ? "An account lets you place orders and track them."
        : "Log in to place orders and track them.");
    $("#authSubmit").textContent = registering ? "Create account" : "Log in";
    $("#authSwitch").textContent = registering
      ? "Already have an account? Log in"
      : "New here? Create an account";
    $("#authHint").hidden = !registering;
    $("#authEmailField").hidden = !registering;
    const password = authForm.elements.password;
    password.minLength = registering ? 10 : 1;
    password.autocomplete = registering ? "new-password" : "current-password";
    $("#authError").textContent = "";
  }
  function openAuth(mode, intro) {
    setAuthMode(mode, intro);
    openOverlay(authModal);
  }
  $("#authSwitch").addEventListener("click", () =>
    setAuthMode(authMode === "login" ? "register" : "login"),
  );

  authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = $("#authSubmit");
    if (submit.disabled) return;
    submit.disabled = true;
    $("#authError").textContent = "";
    try {
      const data = Object.fromEntries(new FormData(authForm));
      if (authMode !== "register") delete data.email;
      await api(authMode === "register" ? "/api/register" : "/api/login", data);
      await loadState();
      authForm.reset();
      closeOverlay(authModal);
      toast(
        authMode === "register"
          ? "Account created. Welcome to Skye Crafts!"
          : "Welcome back!",
      );
      if (pendingCheckout) {
        pendingCheckout = false;
        openCheckout();
      }
    } catch (error) {
      $("#authError").textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  async function logout() {
    try {
      await api("/api/logout", {});
      await loadState();
      toast("Logged out.");
    } catch (error) {
      toast(error.message);
    }
  }

  /* ------------------------------------------------------------ checkout */

  function startCheckout() {
    if (!cart.length) {
      toast("Your cart is empty");
      return;
    }
    if (!state.user) {
      pendingCheckout = true;
      openAuth(
        "login",
        "Log in or create an account to place your order. Your cart will be waiting.",
      );
      return;
    }
    openCheckout();
  }

  function renderCheckoutSummary() {
    const lines = cart
      .map((item) => {
        const product = productById(item.id);
        return product
          ? `<li><span>${esc(product.title)} × ${item.qty}</span><span>${money(product.price_cents * item.qty)}</span></li>`
          : "";
      })
      .join("");

    const subtotal = cartTotal();
    const discount = appliedCoupon?.discount_cents || 0;
    const total = Math.max(0, subtotal - discount);

    $("#checkoutSummary").innerHTML = `
      <ul class="summary-lines">
        ${lines}
        ${discount ? `<li class="discount-line"><span>Coupon ${esc(appliedCoupon.code)}</span><span>−${money(discount)}</span></li>` : ""}
      </ul>
      <div class="summary-total"><span>Total</span><strong>${money(total)}</strong></div>`;

    const hasPreorder = cart.some((item) => productById(item.id)?.is_preorder);
    $("#checkoutNote").textContent =
      "No online payment is collected yet, and shipping fees are not calculated in this version." +
      (hasPreorder ? " Your order includes pre‑order items." : "");
  }

  function updateFulfillment() {
    const pickup = checkoutForm.elements.fulfillment.value === "pickup";
    $("#addressField").hidden = pickup;
    checkoutForm.elements.address.required = !pickup;
    checkoutForm.elements.address.disabled = pickup;
  }
  checkoutForm.addEventListener("change", (event) => {
    if (event.target.name === "fulfillment") updateFulfillment();
  });

  function openCheckout() {
    appliedCoupon = null;
    checkoutForm.elements.coupon.value = "";
    $("#couponNote").textContent = "";
    renderCheckoutSummary();
    $("#checkoutError").textContent = "";
    updateFulfillment();
    openOverlay(checkoutModal);
  }

  $("#applyCoupon").addEventListener("click", async () => {
    const code = checkoutForm.elements.coupon.value.trim();
    const note = $("#couponNote");
    if (!code) {
      note.textContent = "";
      appliedCoupon = null;
      renderCheckoutSummary();
      return;
    }
    if (!cart.length) {
      note.textContent = "Your cart is empty.";
      return;
    }

    try {
      const result = await api("/api/coupons/validate", {
        code,
        items: cart.map((item) => ({
          product_id: item.id,
          quantity: item.qty,
        })),
      });
      appliedCoupon = result;
      note.textContent = `Coupon applied — you save ${money(result.discount_cents)}.`;
      renderCheckoutSummary();
    } catch (error) {
      appliedCoupon = null;
      note.textContent = error.message;
      renderCheckoutSummary();
    }
  });

  checkoutForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = $("#checkoutSubmit");
    if (submit.disabled) return;
    if (!cart.length) {
      closeOverlay(checkoutModal);
      toast("Your cart is empty");
      return;
    }

    submit.disabled = true;
    $("#checkoutError").textContent = "";
    const fields = checkoutForm.elements;
    const delivery = fields.fulfillment.value === "delivery";

    try {
      // Only ids and quantities are sent; the server looks up prices and stock,
      // and re-validates the coupon rather than trusting the client's discount.
      await api("/api/checkout", {
        items: cart.map((item) => ({
          product_id: item.id,
          quantity: item.qty,
        })),
        recipient: fields.recipient.value,
        contact: fields.contact.value,
        fulfillment: fields.fulfillment.value,
        address: delivery ? fields.address.value : "",
        coupon_code: appliedCoupon?.code || fields.coupon.value.trim() || "",
      });
      cart = [];
      saveCart();
      updateCartCount();
      appliedCoupon = null;
      checkoutForm.reset();
      closeOverlay(checkoutModal);
      await loadState(true);
      toast("Order placed. Thank you!");
      openOrders();
    } catch (error) {
      if (error.status === 401) {
        closeOverlay(checkoutModal);
        await loadState(true).catch(() => {});
        pendingCheckout = true;
        openAuth(
          "login",
          "Your session ended. Please log in again to finish your order.",
        );
      } else {
        $("#checkoutError").textContent = error.message;
        if (error.status === 409) {
          await loadState(true).catch(() => {});
          if (cart.length) renderCheckoutSummary();
          else {
            closeOverlay(checkoutModal);
            toast(error.message);
          }
        }
      }
    } finally {
      submit.disabled = false;
    }
  });

  /* -------------------------------------------------------------- orders */

  async function openOrders() {
    ordersList.innerHTML = '<p class="grid-note">Loading your orders…</p>';
    openOverlay(ordersModal);
    try {
      const { orders } = await api("/api/orders");
      renderOrders(orders);
    } catch (error) {
      if (error.status === 401) {
        closeOverlay(ordersModal);
        openAuth("login");
      } else
        ordersList.innerHTML = `<p class="form-error">${esc(error.message)}</p>`;
    }
  }

  const CANCELLABLE = new Set(["Placed", "Approved"]);

  function renderOrders(orders) {
    ordersList.innerHTML = orders.length
      ? orders
          .map((order) => {
            const pickup = order.fulfillment === "pickup";
            const items = order.items
              .map(
                (item) => `
        <li><span>${esc(item.title)} × ${item.quantity}${item.is_preorder ? " <em>(pre‑order)</em>" : ""}</span>
            <span>${money(item.price_cents * item.quantity)}</span></li>`,
              )
              .join("");
            const canCancel = CANCELLABLE.has(order.status);
            return `
        <article class="order-card">
          <div class="order-head">
            <strong>Order #${order.id}</strong>
            <span class="status-pill status-${esc(order.status.toLowerCase())}">${esc(statusLabel(order))}</span>
          </div>
          <p class="order-meta">${esc(formatDate(order.created_at))} · ${pickup ? "Pick up" : "Delivery via J&amp;T Express"}</p>
          <ul class="order-items">${items}</ul>
          <p class="order-address">${esc(order.recipient)} · ${esc(order.contact)}${pickup ? "" : `<br>${esc(order.address)}`}</p>
          ${order.discount_cents ? `<p class="order-meta">Coupon ${esc(order.coupon_code)} applied: −${money(order.discount_cents)}</p>` : ""}
          <div class="order-foot">
            <strong>Total ${money(order.total_cents)}</strong>
            <div class="order-actions">
              ${
                order.status === "Shipped"
                  ? `<button class="btn btn-primary btn-sm" type="button" data-receive="${order.id}">${pickup ? "Confirm pickup" : "Confirm receipt"}</button>`
                  : ""
              }
              ${
                canCancel
                  ? `<button class="cancel-btn" type="button" data-cancel="${order.id}">Cancel order</button>`
                  : ""
              }
            </div>
          </div>
        </article>`;
          })
          .join("")
      : '<p class="grid-note">No orders yet — your first one will show up here ♡</p>';
  }

  ordersList.addEventListener("click", async (event) => {
    const receiveButton = event.target.closest("[data-receive]");
    const cancelButton = event.target.closest("[data-cancel]");

    if (receiveButton && !receiveButton.disabled) {
      receiveButton.disabled = true;
      try {
        await api(
          `/api/orders/${Number(receiveButton.dataset.receive)}/status`,
          { status: "Delivered" },
        );
        toast("Thank you! Order marked as received.");
        const { orders } = await api("/api/orders");
        renderOrders(orders);
      } catch (error) {
        toast(error.message);
        receiveButton.disabled = false;
      }
    }

    if (cancelButton && !cancelButton.disabled) {
      if (!window.confirm("Cancel this order? This cannot be undone.")) return;
      cancelButton.disabled = true;
      try {
        await api(
          `/api/orders/${Number(cancelButton.dataset.cancel)}/cancel`,
          {},
        );
        toast("Order cancelled.");
        const { orders } = await api("/api/orders");
        renderOrders(orders);
        await loadState(true);
      } catch (error) {
        toast(error.message);
        cancelButton.disabled = false;
      }
    }
  });

  /* ---------------------------------------------- custom order, newsletter */

  $("#customForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = $("#customSubmit");
    if (submit.disabled) return;
    submit.disabled = true;
    $("#customError").textContent = "";
    try {
      await api("/api/custom-requests", Object.fromEntries(new FormData(form)));
      form.reset();
      toast("Thanks! Your custom order request was sent.");
    } catch (error) {
      $("#customError").textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  $("#newsletterForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    if (button.disabled) return;
    button.disabled = true;
    try {
      await api("/api/newsletter", { email: form.elements.email.value });
      form.reset();
      toast("Thanks for subscribing!");
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  });

  /* --------------------------------------------- page behaviour (original) */

  // Mobile navigation
  menuToggle?.addEventListener("click", () => {
    header.classList.toggle("open");
    menuToggle.setAttribute(
      "aria-label",
      header.classList.contains("open") ? "Close menu" : "Open menu",
    );
  });
  document.querySelectorAll("nav a").forEach((link) => {
    link.addEventListener("click", () => header.classList.remove("open"));
  });

  // Header shadow on scroll
  window.addEventListener(
    "scroll",
    () => {
      header.classList.toggle("scrolled", window.scrollY > 12);
    },
    { passive: true },
  );

  // Active navigation link
  if ("IntersectionObserver" in window) {
    const sections = [
      ...document.querySelectorAll("main section[id], footer[id]"),
    ];
    const navLinks = [...document.querySelectorAll("nav a")];
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            navLinks.forEach((a) =>
              a.classList.toggle(
                "active",
                a.getAttribute("href") === "#" + entry.target.id,
              ),
            );
          }
        });
      },
      { rootMargin: "-35% 0px -55% 0px", threshold: 0 },
    );
    sections.forEach((section) => observer.observe(section));
  }

  // Reveal-on-scroll (also used for cards rendered after the page loads)
  const revealObserver =
    "IntersectionObserver" in window
      ? new IntersectionObserver(
          (entries) => {
            entries.forEach((entry) => {
              if (entry.isIntersecting) {
                entry.target.classList.add("visible");
                revealObserver.unobserve(entry.target);
              }
            });
          },
          { threshold: 0.12 },
        )
      : null;

  function observeReveal(root) {
    root
      .querySelectorAll(
        ".section-head,.product-card,.why-item,.step,.gallery-item,.faq-list,.cta-band,.custom-card,.reveal",
      )
      .forEach((element) => {
        element.classList.add("reveal");
        if (revealObserver) revealObserver.observe(element);
        else element.classList.add("visible");
      });
  }
  observeReveal(document);

  // FAQ accordion
  document.querySelectorAll(".faq-question").forEach((question) => {
    question.addEventListener("click", () => {
      const item = question.parentElement;
      document.querySelectorAll(".faq-item.open").forEach((other) => {
        if (other !== item) other.classList.remove("open");
      });
      item.classList.toggle("open");
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeAllOverlays();
      hideAccountMenu();
      header.classList.remove("open");
      return;
    }
    if (event.key === "Tab") {
      const dialog = document.querySelector(".modal-backdrop.show .modal");
      if (!dialog) return;
      const focusable = [
        ...dialog.querySelectorAll(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ].filter((element) => !element.hidden);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  brandTop?.addEventListener("click", () =>
    window.scrollTo({ top: 0, behavior: "smooth" }),
  );

  /* ---------------------------------------------------------------- boot */

  async function loadState(quiet) {
    state = await api("/api/state");
    Sky.setCsrf(state.csrf);
    reconcileCart(quiet);
    renderAccount();
    renderFilters();
    renderProducts();
    if (!didFocusProductsPage && document.body.dataset.page === "products") {
      didFocusProductsPage = true;
      requestAnimationFrame(() => $("#shop")?.scrollIntoView());
    }
  }

  updateCartCount();
  loadState().catch((error) => {
    productGrid.innerHTML =
      '<p class="grid-note">Could not load the shop. Please refresh the page.</p>';
    toast(error.message);
  });
})();
