(() => {
  const {api, esc, money, toast, statusLabel, formatDate} = Sky;

  const app = document.getElementById('adminApp');
  const logoutButton = document.getElementById('adminLogout');

  let user = null;
  let data = null;
  let tab = 'orders';
  let editingProductId = null;
  let editingCouponId = null;

  // Mirrors ADMIN_TRANSITIONS in app.py — what each status can move to next,
  // and the label to show on that action button.
  const NEXT_STATUS = {
    Placed: [['Approved', 'Approve order'], ['Declined', 'Decline']],
    Approved: [['Packed', 'Mark packed'], ['Cancelled', 'Cancel']],
    Packed: [['Shipped', 'Mark shipped'], ['Cancelled', 'Cancel']],
    Shipped: [['Delivered', 'Mark delivered']],
  };
  const REASON_REQUIRED = new Set(['Declined', 'Cancelled']);

  /* ---------------------------------------------------------------- boot */

  async function boot() {
    try {
      const state = await api('/api/state');
      Sky.setCsrf(state.csrf);
      user = state.user;
      logoutButton.hidden = !user;

      if (!user) return renderLogin();
      if (!user.is_admin) return renderDenied();

      await loadOverview();
      render();
    } catch (error) {
      app.innerHTML = `<p class="grid-note">${esc(error.message)}</p>`;
    }
  }

  async function loadOverview() {
    data = await api('/api/admin/overview');
  }

  /* --------------------------------------------------------- login / deny */

  function renderLogin(message) {
    app.innerHTML = `
      <div class="panel auth-card">
        <h2>Shop admin</h2>
        <p class="panel-note">Log in with the shop owner account.</p>
        <form id="loginForm" class="form-stack">
          <label class="field">Username
            <input name="username" required maxlength="30" autocomplete="username">
          </label>
          <label class="field">Password
            <input name="password" type="password" required maxlength="128" autocomplete="current-password">
          </label>
          <p class="form-error" id="loginError" role="alert">${esc(message || '')}</p>
          <button class="btn btn-primary" type="submit">Log in</button>
        </form>
      </div>`;
  }

  function renderDenied() {
    app.innerHTML = `
      <div class="panel auth-card">
        <h2>Admins only</h2>
        <p class="panel-note">You're signed in as <strong>${esc(user.username)}</strong>, which isn't a shop admin account.</p>
        <p style="margin-top:16px"><a class="btn btn-primary" href="/">Back to the shop</a></p>
      </div>`;
  }

  /* ---------------------------------------------------------------- shell */

  function tabButton(id, label, count) {
    return `<button type="button" class="tab${tab === id ? ' active' : ''}" role="tab"
              aria-selected="${tab === id}" data-tab="${id}">${label}${count ? `<span class="count">${count}</span>` : ''}</button>`;
  }

  function render() {
    const newOrders = data.orders.filter(order => order.status === 'Placed').length;
    const newRequests = data.custom_requests.filter(request => request.status === 'New').length;

    app.innerHTML = `
      <h1>Shop admin</h1>
      <p class="panel-note">Signed in as ${esc(user.username)}</p>
      <div class="admin-tabs" role="tablist">
        ${tabButton('orders', 'Orders', newOrders)}
        ${tabButton('products', 'Products')}
        ${tabButton('coupons', 'Coupons')}
        ${tabButton('requests', 'Custom requests', newRequests)}
        ${tabButton('reviews', 'Reviews')}
        ${tabButton('subscribers', 'Subscribers')}
      </div>
      <div id="tabBody">${{
        orders: ordersTab,
        products: productsTab,
        coupons: couponsTab,
        requests: requestsTab,
        reviews: reviewsTab,
        subscribers: subscribersTab
      }[tab]()}</div>`;
  }

  /* --------------------------------------------------------------- orders */

  function ordersTab() {
    if (!data.orders.length) return '<div class="panel"><p class="grid-note">No orders yet.</p></div>';

    return `<div class="stack">${data.orders.map(order => {
      const pickup = order.fulfillment === 'pickup';
      const hasPreorder = order.items.some(item => item.is_preorder);
      const items = order.items.map(item => `
        <li><span>${esc(item.title)} × ${item.quantity}${item.is_preorder ? ' <span class="tag">Pre‑order</span>' : ''}</span>
            <span>${money(item.price_cents * item.quantity)}</span></li>`).join('');

      const nextActions = (NEXT_STATUS[order.status] || []).map(([target, label]) => {
        if (REASON_REQUIRED.has(target)) {
          return `
            <span class="reason-prompt">
              <input type="text" data-reason-for="${order.id}" data-reason-target="${target}" placeholder="Reason (optional)" maxlength="300">
              <button class="btn btn-secondary btn-sm" type="button" data-order="${order.id}" data-status="${target}">${label}</button>
            </span>`;
        }
        return `<button class="btn btn-primary btn-sm" type="button" data-order="${order.id}" data-status="${target}">${label}</button>`;
      }).join('');

      const history = (order.status_history || []).map(entry => `
        <li><strong>${esc(entry.status)}</strong> — ${esc(entry.note)} (${esc(entry.updated_by)}, ${esc(formatDate(entry.created_at))})</li>
      `).join('');

      return `
        <article class="order-card">
          <div class="order-head">
            <strong>Order #${order.id} · ${esc(order.recipient)}</strong>
            <span>
              ${hasPreorder ? '<span class="tag">Has pre‑order items</span> ' : ''}
              <span class="status-pill status-${esc(order.status.toLowerCase())}">${esc(statusLabel(order))}</span>
            </span>
          </div>
          <p class="order-meta">${esc(formatDate(order.created_at))} · account ${esc(order.buyer)} · ${esc(order.contact)} · ${pickup ? 'Pick up' : 'Delivery via J&amp;T Express'}</p>
          <ul class="order-items">${items}</ul>
          ${pickup ? '' : `<p class="order-address">${esc(order.address)}</p>`}
          ${order.discount_cents ? `<p class="order-meta">Coupon ${esc(order.coupon_code)}: subtotal ${money(order.subtotal_cents)}, −${money(order.discount_cents)}</p>` : ''}
          <div class="order-foot">
            <strong>Total ${money(order.total_cents)}</strong>
            <div class="order-actions">${nextActions || '<span class="panel-note">No further action — order is final.</span>'}</div>
          </div>
          ${history ? `<ul class="status-history">${history}</ul>` : ''}
        </article>`;
    }).join('')}</div>`;
  }

  /* ------------------------------------------------------------- products */

  function productsTab() {
    const editing = data.products.find(product => product.id === editingProductId);
    const product = editing || {
      title: '', description: '', category: data.categories[0],
      price_cents: null, stock: 0, is_preorder: false, is_active: true
    };

    const categories = data.categories.map(name =>
      `<option value="${esc(name)}"${name === product.category ? ' selected' : ''}>${esc(name)}</option>`).join('');

    const rows = data.products.map(item => `
      <tr>
        <td><strong>${esc(item.title)}</strong><br><small class="panel-note">${esc(item.category)}</small></td>
        <td class="num">${money(item.price_cents)}</td>
        <td class="num">${item.is_preorder ? '—' : item.stock}</td>
        <td>
          ${item.is_preorder ? '<span class="tag">Pre‑order</span> ' : ''}
          <span class="tag ${item.is_active ? 'ok' : 'off'}">${item.is_active ? 'Visible' : 'Hidden'}</span>
        </td>
        <td class="num"><button class="btn btn-secondary btn-sm" type="button" data-edit="${item.id}">Edit</button></td>
      </tr>`).join('');

    return `
      <div class="admin-grid">
        <div class="panel">
          <h2>${editing ? 'Edit product' : 'Add a product'}</h2>
          <form id="productForm" class="form-stack">
            <label class="field">Name
              <input name="title" required minlength="3" maxlength="100" value="${esc(product.title)}">
            </label>
            <label class="field">Description
              <textarea name="description" required maxlength="1000">${esc(product.description)}</textarea>
            </label>
            <label class="field">Category
              <select name="category">${categories}</select>
            </label>
            <div class="form-2col">
              <label class="field">Price (₱)
                <input name="price" type="number" min="0.01" max="100000" step="0.01" required
                       value="${product.price_cents === null ? '' : (product.price_cents / 100).toFixed(2)}">
              </label>
              <label class="field">Stock
                <input name="stock" type="number" min="0" max="100000" step="1" required value="${product.stock}">
              </label>
            </div>
            <label class="check"><input type="checkbox" name="is_preorder"${product.is_preorder ? ' checked' : ''}> Pre‑order item (no stock limit)</label>
            <label class="check"><input type="checkbox" name="is_active"${product.is_active ? ' checked' : ''}> Visible in the shop</label>
            <p class="form-error" id="productError" role="alert"></p>
            <div class="order-actions">
              <button class="btn btn-primary" type="submit">${editing ? 'Save changes' : 'Add product'}</button>
              ${editing ? '<button class="btn btn-secondary" type="button" id="cancelProductEdit">Cancel</button>' : ''}
            </div>
          </form>
        </div>

        <div class="panel">
          <h2>Products</h2>
          <p class="panel-note">Stock is reduced automatically when an order is placed, and restored if it's later declined or cancelled. Pre‑order items have no stock limit.</p>
          <div class="table-wrap">
            <table class="admin-table">
              <thead><tr><th>Product</th><th class="num">Price</th><th class="num">Stock</th><th>Status</th><th></th></tr></thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
        </div>
      </div>`;
  }

  /* -------------------------------------------------------------- coupons */

  function couponsTab() {
    const editing = data.coupons.find(coupon => coupon.id === editingCouponId);
    // Coupon dates are Philippine calendar days (see SHOP_TZ in app.py).
    const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Manila'}).format(new Date());
    const coupon = editing || {
      code: '', discount_type: 'percentage', discount_value: '', max_discount_cents: null,
      min_order_cents: null, max_uses: 0, once_per_customer: true,
      start_date: today, end_date: today, is_active: true
    };
    const isPercent = coupon.discount_type === 'percentage';

    const rows = data.coupons.length ? data.coupons.map(item => `
      <tr>
        <td><strong>${esc(item.code)}</strong></td>
        <td>${item.discount_type === 'percentage' ? `${item.discount_value}%` : money(item.discount_value)}</td>
        <td class="num">${item.used_count}${item.max_uses ? ` / ${item.max_uses}` : ''}${item.once_per_customer ? '<br><small class="panel-note">1 per customer</small>' : ''}</td>
        <td>${esc(item.start_date)} → ${esc(item.end_date)}</td>
        <td><span class="tag ${item.is_active ? 'ok' : 'off'}">${item.is_active ? 'Active' : 'Off'}</span></td>
        <td class="num">
          <button class="btn btn-secondary btn-sm" type="button" data-edit-coupon="${item.id}">Edit</button>
          <button class="btn btn-secondary btn-sm" type="button" data-delete-coupon="${item.id}">Delete</button>
        </td>
      </tr>`).join('') : `<tr><td colspan="6" class="grid-note">No coupons yet.</td></tr>`;

    return `
      <div class="admin-grid">
        <div class="panel">
          <h2>${editing ? 'Edit coupon' : 'Add a coupon'}</h2>
          <p class="panel-note">Ported from KamiKoto's coupon model: percentage or fixed discount, optional cap, minimum order, usage limit, and a validity window.</p>
          <form id="couponForm" class="form-stack">
            <label class="field">Code
              <input name="code" required minlength="3" maxlength="40" value="${esc(coupon.code)}" style="text-transform:uppercase">
            </label>
            <div class="form-2col">
              <label class="field">Discount type
                <select name="discount_type" id="couponType">
                  <option value="percentage"${isPercent ? ' selected' : ''}>Percentage</option>
                  <option value="fixed"${!isPercent ? ' selected' : ''}>Fixed amount (₱)</option>
                </select>
              </label>
              <label class="field" id="discountValueField">${isPercent ? 'Discount (%)' : 'Discount (₱)'}
                <input name="discount_value" type="number" required
                       ${isPercent ? 'min="1" max="100" step="1"' : 'min="0.01" max="100000" step="0.01"'}
                       value="${isPercent ? coupon.discount_value : (coupon.discount_value ? (coupon.discount_value / 100).toFixed(2) : '')}">
              </label>
            </div>
            <div class="form-2col">
              <label class="field">Max discount (₱) <span class="opt">(optional, % only)</span>
                <input name="max_discount" type="number" min="0" step="0.01" value="${coupon.max_discount_cents ? (coupon.max_discount_cents / 100).toFixed(2) : ''}">
              </label>
              <label class="field">Minimum order (₱) <span class="opt">(optional)</span>
                <input name="min_order" type="number" min="0" step="0.01" value="${coupon.min_order_cents ? (coupon.min_order_cents / 100).toFixed(2) : ''}">
              </label>
            </div>
            <div class="form-2col">
              <label class="field">Start date
                <input name="start_date" type="date" required value="${esc(coupon.start_date)}">
              </label>
              <label class="field">End date
                <input name="end_date" type="date" required value="${esc(coupon.end_date)}">
              </label>
            </div>
            <p class="panel-note">Philippine time: valid from 12:00 AM on the start date until 11:59 PM on the end date.</p>
            <label class="field">Max uses <span class="opt">(0 = unlimited; cancelled and declined orders give their use back)</span>
              <input name="max_uses" type="number" min="0" step="1" value="${coupon.max_uses || 0}">
            </label>
            <label class="check"><input type="checkbox" name="once_per_customer"${coupon.once_per_customer ? ' checked' : ''}> One use per customer</label>
            <label class="check"><input type="checkbox" name="is_active"${coupon.is_active ? ' checked' : ''}> Active</label>
            <p class="form-error" id="couponError" role="alert"></p>
            <div class="order-actions">
              <button class="btn btn-primary" type="submit">${editing ? 'Save changes' : 'Add coupon'}</button>
              ${editing ? '<button class="btn btn-secondary" type="button" id="cancelCouponEdit">Cancel</button>' : ''}
            </div>
          </form>
        </div>

        <div class="panel">
          <h2>Coupons</h2>
          <div class="table-wrap">
            <table class="admin-table">
              <thead><tr><th>Code</th><th>Discount</th><th class="num">Used</th><th>Valid</th><th>Status</th><th></th></tr></thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
        </div>
      </div>`;
  }

  // Switch the value field between % and ₱ without re-rendering, so the
  // chosen type and anything already typed into the form are kept.
  function syncCouponType(form) {
    const isPercent = form.elements.discount_type.value === 'percentage';
    const input = form.elements.discount_value;
    document.getElementById('discountValueField').firstChild.textContent =
      isPercent ? 'Discount (%)' : 'Discount (₱)';
    Object.assign(input, isPercent
      ? {min: '1', max: '100', step: '1'}
      : {min: '0.01', max: '100000', step: '0.01'});
  }

  /* ------------------------------------------------------ custom requests */

  function requestsTab() {
    if (!data.custom_requests.length) {
      return '<div class="panel"><p class="grid-note">No custom order requests yet.</p></div>';
    }

    return `<div class="stack">${data.custom_requests.map(request => `
      <article class="order-card request-card">
        <div class="order-head">
          <strong>${esc(request.name)}${request.occasion ? ` · ${esc(request.occasion)}` : ''}</strong>
          <select data-request="${request.id}" aria-label="Status for request ${request.id}">
            ${data.custom_statuses.map(status =>
              `<option${status === request.status ? ' selected' : ''}>${esc(status)}</option>`).join('')}
          </select>
        </div>
        <p class="order-meta">${esc(formatDate(request.created_at))} · ${esc(request.contact)}${request.account ? ` · account ${esc(request.account)}` : ' · guest'}</p>
        <p class="order-address">${esc(request.details)}</p>
      </article>`).join('')}</div>`;
  }

  /* -------------------------------------------------------------- reviews */

  function reviewsTab() {
    if (!data.reviews.length) {
      return '<div class="panel"><p class="grid-note">No reviews yet.</p></div>';
    }
    const rows = data.reviews.map(review => `
      <tr>
        <td>${esc(review.product)}</td>
        <td>${esc(review.username)}</td>
        <td>${'★'.repeat(review.rating)}${'☆'.repeat(5 - review.rating)}</td>
        <td>${esc(review.text || '')}</td>
        <td class="panel-note">${esc(formatDate(review.created_at))}</td>
      </tr>`).join('');
    return `
      <div class="panel">
        <h2>Product reviews</h2>
        <div class="table-wrap">
          <table class="admin-table">
            <thead><tr><th>Product</th><th>Customer</th><th>Rating</th><th>Comment</th><th>Date</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  }

  /* ---------------------------------------------------------- subscribers */

  function subscribersTab() {
    if (!data.subscribers.length) {
      return '<div class="panel"><p class="grid-note">No newsletter subscribers yet.</p></div>';
    }
    return `
      <div class="panel">
        <div class="order-head">
          <h2>${data.subscribers.length} subscriber${data.subscribers.length === 1 ? '' : 's'}</h2>
          <button class="btn btn-secondary btn-sm" type="button" data-copy>Copy all emails</button>
        </div>
        <div class="table-wrap">
          <table class="admin-table">
            <thead><tr><th>Email</th><th>Joined</th></tr></thead>
            <tbody>${data.subscribers.map(subscriber => `
              <tr><td>${esc(subscriber.email)}</td><td>${esc(formatDate(subscriber.created_at))}</td></tr>`).join('')}
            </tbody>
          </table>
        </div>
      </div>`;
  }

  /* --------------------------------------------------------------- events */

  async function refresh(message) {
    await loadOverview();
    render();
    if (message) toast(message);
  }

  app.addEventListener('click', async event => {
    const tabButtonEl = event.target.closest('[data-tab]');
    if (tabButtonEl) {
      tab = tabButtonEl.dataset.tab;
      render();
      return;
    }

    const orderButton = event.target.closest('[data-order]');
    if (orderButton && !orderButton.disabled) {
      orderButton.disabled = true;
      const target = orderButton.dataset.status;
      const reasonInput = app.querySelector(
        `[data-reason-for="${orderButton.dataset.order}"][data-reason-target="${target}"]`
      );
      try {
        await api(`/api/admin/orders/${Number(orderButton.dataset.order)}/status`, {
          status: target,
          reason: reasonInput ? reasonInput.value : ''
        });
        await refresh('Order updated.');
      } catch (error) {
        toast(error.message);
        orderButton.disabled = false;
      }
      return;
    }

    const editButton = event.target.closest('[data-edit]');
    if (editButton) {
      editingProductId = Number(editButton.dataset.edit);
      render();
      document.getElementById('productForm')?.scrollIntoView({behavior: 'smooth', block: 'center'});
      return;
    }
    if (event.target.closest('#cancelProductEdit')) {
      editingProductId = null;
      render();
      return;
    }

    const editCouponButton = event.target.closest('[data-edit-coupon]');
    if (editCouponButton) {
      editingCouponId = Number(editCouponButton.dataset.editCoupon);
      render();
      document.getElementById('couponForm')?.scrollIntoView({behavior: 'smooth', block: 'center'});
      return;
    }
    if (event.target.closest('#cancelCouponEdit')) {
      editingCouponId = null;
      render();
      return;
    }
    const deleteCouponButton = event.target.closest('[data-delete-coupon]');
    if (deleteCouponButton) {
      if (!window.confirm('Delete this coupon? This cannot be undone.')) return;
      try {
        await api(`/api/admin/coupons/${Number(deleteCouponButton.dataset.deleteCoupon)}/delete`, {});
        await refresh('Coupon deleted.');
      } catch (error) {
        toast(error.message);
      }
      return;
    }

    if (event.target.closest('[data-copy]')) {
      const emails = data.subscribers.map(subscriber => subscriber.email).join(', ');
      try {
        await navigator.clipboard.writeText(emails);
        toast('Emails copied.');
      } catch {
        toast('Could not copy. Select the emails manually instead.');
      }
    }
  });

  app.addEventListener('change', async event => {
    if (event.target.id === 'couponType') { syncCouponType(event.target.form); return; }

    const select = event.target.closest('[data-request]');
    if (!select) return;
    try {
      await api(`/api/admin/custom-requests/${Number(select.dataset.request)}/status`, {status: select.value});
      await refresh('Request updated.');
    } catch (error) {
      toast(error.message);
      await refresh();
    }
  });

  app.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.target;
    const submit = form.querySelector('[type=submit]');
    if (submit.disabled) return;

    if (form.id === 'loginForm') {
      submit.disabled = true;
      try {
        await api('/api/login', Object.fromEntries(new FormData(form)));
        await boot();
      } catch (error) {
        document.getElementById('loginError').textContent = error.message;
        submit.disabled = false;
      }
    }

    if (form.id === 'productForm') {
      const fields = form.elements;
      const error = document.getElementById('productError');
      error.textContent = '';
      submit.disabled = true;
      try {
        await api(editingProductId ? `/api/admin/products/${editingProductId}` : '/api/admin/products', {
          title: fields.title.value,
          description: fields.description.value,
          category: fields.category.value,
          price: fields.price.value,
          stock: Number(fields.stock.value),
          is_preorder: fields.is_preorder.checked,
          is_active: fields.is_active.checked
        });
        const wasEditing = editingProductId !== null;
        editingProductId = null;
        await refresh(wasEditing ? 'Product updated.' : 'Product added.');
      } catch (failure) {
        error.textContent = failure.message;
        submit.disabled = false;
      }
    }

    if (form.id === 'couponForm') {
      const fields = form.elements;
      const error = document.getElementById('couponError');
      error.textContent = '';
      submit.disabled = true;
      try {
        await api(editingCouponId ? `/api/admin/coupons/${editingCouponId}` : '/api/admin/coupons', {
          code: fields.code.value,
          discount_type: fields.discount_type.value,
          discount_value: fields.discount_type.value === 'percentage'
            ? Number(fields.discount_value.value)
            : fields.discount_value.value,
          max_discount: fields.max_discount.value || null,
          min_order: fields.min_order.value || null,
          max_uses: Number(fields.max_uses.value || 0),
          once_per_customer: fields.once_per_customer.checked,
          start_date: fields.start_date.value,
          end_date: fields.end_date.value,
          is_active: fields.is_active.checked
        });
        const wasEditing = editingCouponId !== null;
        editingCouponId = null;
        await refresh(wasEditing ? 'Coupon updated.' : 'Coupon added.');
      } catch (failure) {
        error.textContent = failure.message;
        submit.disabled = false;
      }
    }
  });

  logoutButton.addEventListener('click', async () => {
    try {
      await api('/api/logout', {});
    } catch (error) {
      toast(error.message);
    }
    editingProductId = null;
    editingCouponId = null;
    tab = 'orders';
    boot();
  });

  boot();
})();
