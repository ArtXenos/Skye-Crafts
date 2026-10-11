/* Shared helpers for the shop (script.js) and the admin page (admin.js). */
window.Sky = (() => {
  let csrf = '';
  let toastTimer;

  // Escape every piece of server/user text before putting it in innerHTML.
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);

  // Prices are stored as centavos; show whole pesos without ".00".
  const money = cents => '₱' + (cents / 100).toLocaleString('en-PH', {
    minimumFractionDigits: cents % 100 ? 2 : 0,
    maximumFractionDigits: 2
  });

  async function api(path, data) {
    const options = {credentials: 'same-origin', headers: {}};
    if (data !== undefined) {
      options.method = 'POST';
      options.headers['Content-Type'] = 'application/json';
      options.headers['X-CSRF-Token'] = csrf;
      options.body = JSON.stringify(data);
    }

    let response;
    try {
      response = await fetch(path, options);
    } catch {
      throw new Error('Could not reach the server. Please try again.');
    }

    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(result.error || 'Request failed. Please try again.');
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function toast(message) {
    const el = document.getElementById('toast');
    if (!el) return;
    el.textContent = message;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
  }

  // Order status as shown to people (pickup orders use different wording).
  function statusLabel(order) {
    const pickup = order.fulfillment === 'pickup';
    const map = {
      Placed: 'Order placed',
      Approved: 'Being prepared',
      Packed: 'Packed',
      Shipped: pickup ? 'Ready for pickup' : 'Shipped via J&T Express',
      Delivered: pickup ? 'Picked up' : 'Delivered',
      Declined: 'Declined',
      Cancelled: 'Cancelled',
    };
    return map[order.status] || order.status;
  }

  function formatDate(value) {
    // SQLite CURRENT_TIMESTAMP is UTC "YYYY-MM-DD HH:MM:SS".
    const date = new Date(String(value).replace(' ', 'T') + 'Z');
    return isNaN(date) ? String(value) : date.toLocaleString('en-PH', {
      dateStyle: 'medium', timeStyle: 'short'
    });
  }

  return {esc, money, api, toast, statusLabel, formatDate, setCsrf: value => { csrf = value; }};
})();
